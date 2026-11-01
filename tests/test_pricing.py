#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""定价链 / 费用分摊 / 库存总账测试（客户管理表单规则）。

运行：cd car-mgmt && python -m unittest discover -s tests -v
临时数据库，与 test_batch_a.py 相同的隔离约束。
"""
import datetime as dt
import os
import sys
import tempfile
import unittest

TMPDIR = tempfile.mkdtemp(prefix="car_mgmt_pricing_")
os.environ["CAR_MGMT_DB_PATH"] = os.path.join(TMPDIR, "test.db")
os.environ["APP_ENV"] = "testing"
os.environ.pop("SECRET_KEY", None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
import pricing
import app as app_module
from werkzeug.security import generate_password_hash


def make_user(username, password="pw123456", role="boss", name="测试"):
    return db.execute(
        "INSERT INTO users (username, password_hash, name, role) VALUES (?,?,?,?)",
        (username, generate_password_hash(password), name, role),
    )


def make_vehicle(buy_price=80000, status="在售", buyer_id=None, purchase_date=None):
    return db.execute(
        "INSERT INTO vehicles (vin, brand, model, buy_price, status, buyer_id, purchase_date) "
        "VALUES (?,?,?,?,?,?,?)",
        ("TESTVIN" + os.urandom(4).hex(), "测试品牌", "测试车型",
         buy_price, status, buyer_id, purchase_date),
    )


class PricingBase(unittest.TestCase):
    def setUp(self):
        # 多测试模块合跑时 db.DB_PATH 是全局共享的；每个用例开始前指回本模块临时库
        db.DB_PATH = os.path.join(TMPDIR, "test.db")
        p = db.DB_PATH
        self.assertTrue(p.startswith(TMPDIR), f"测试库路径异常：{p}")
        if os.path.exists(p):
            os.remove(p)
        db.init_db()
        self.client = app_module.app.test_client()

    def login(self, username, password="pw123456"):
        self.client.get("/login")
        with self.client.session_transaction() as sess:
            token = sess.get("csrf_token", "")
        resp = self.client.post(
            "/login",
            data={"username": username, "password": password, "csrf_token": token},
        )
        with self.client.session_transaction() as sess:
            self.csrf = sess.get("csrf_token", "")
        return resp

    def post(self, path, data):
        """带 CSRF token 的 POST（test_client 不执行 app.js 的自动注入）。"""
        data = dict(data)
        data.setdefault("csrf_token", self.csrf)
        return self.client.post(path, data=data)


class TestTierTables(unittest.TestCase):
    def test_profit_rate_boundaries(self):
        self.assertEqual(pricing.profit_rate(100000), 0.12)
        self.assertEqual(pricing.profit_rate(100001), 0.10)
        self.assertEqual(pricing.profit_rate(200000), 0.09)
        self.assertEqual(pricing.profit_rate(250000), 0.08)
        self.assertEqual(pricing.profit_rate(350000), 0.06)
        self.assertEqual(pricing.profit_rate(9999999), 0.06)

    def test_cycle_discount_bands_and_cap(self):
        self.assertEqual(pricing.cycle_discount(0), 0.0)
        self.assertEqual(pricing.cycle_discount(10), 0.0)
        self.assertEqual(pricing.cycle_discount(11), -0.01)
        self.assertEqual(pricing.cycle_discount(140), -0.13)
        # 141–150 天原文无分支，按封顶值 -14% 计（待客户确认）
        self.assertEqual(pricing.cycle_discount(145), -0.14)
        self.assertEqual(pricing.cycle_discount(151), -0.14)
        self.assertEqual(pricing.cycle_discount(999), -0.14)

    def test_showcard_and_standards(self):
        self.assertEqual(pricing.showcard_amount(100000), 6000)
        self.assertEqual(pricing.showcard_amount(350001), 12000)
        # AI 分支重叠：IFS 首匹配，20–25 万档取 8000
        self.assertEqual(pricing.wholesale_std(200000), 7000)
        self.assertEqual(pricing.wholesale_std(220000), 8000)
        self.assertEqual(pricing.wholesale_std(300000), 9000)
        # AJ 19000 档原文照录（疑似笔误，待客户确认）
        self.assertEqual(pricing.floor_std(320000), 19000)
        self.assertEqual(pricing.floor_std(360000), 10000)

    def test_compute_pricing_worked_example(self):
        # 查询表示例行：打款价 190000 + 入库前 282 + 入库后 24，库存 234 天
        p = pricing.compute_pricing(190000, 282, 24, 234)
        self.assertEqual(p["cost"], 190306)
        self.assertEqual(p["profit_rate"], 0.09)
        self.assertEqual(p["cycle_discount"], -0.14)
        self.assertAlmostEqual(p["profit_value"], -9515.3)
        self.assertEqual(p["showcard"], 8000)
        self.assertEqual(p["wholesale_std"], 7000)
        self.assertEqual(p["floor_std"], 6000)
        self.assertEqual(p["wholesale_price"], 197306)
        self.assertAlmostEqual(p["floor_price"], 180790.7)
        self.assertAlmostEqual(p["showroom_price"], 186290.7)
        self.assertAlmostEqual(p["online_price"], 188790.7)

    def test_dates_and_deal(self):
        v = {"purchase_date": "2026-03-12", "created_at": "2026-01-01 10:00:00"}
        self.assertEqual(pricing.stock_in_date(v), "2026-03-12")
        v2 = {"purchase_date": "", "created_at": "2026-01-01 10:00:00"}
        self.assertEqual(pricing.stock_in_date(v2), "2026-01-01")
        self.assertEqual(
            pricing.stock_days("2026-03-12", dt.date(2026, 3, 18)), 6
        )
        self.assertEqual(pricing.stock_days("2099-01-01"), 0)
        self.assertEqual(pricing.stock_days("not-a-date"), 0)
        self.assertEqual(
            pricing.turnover_days("2026-03-12", "2026-03-18"), 6
        )
        self.assertIsNone(pricing.turnover_days("2026-03-12", None))
        self.assertIsNone(pricing.total_deal_price(None))
        sale = {"price": 200000, "loan_rebate": 1700, "gps_rebate": 0,
                "insurance_rebate": 500, "warranty_profit": 0, "other_income": 0}
        self.assertEqual(pricing.total_deal_price(sale), 202200)
        self.assertEqual(pricing.vin_last6("LBV1234567890"), "567890")
        self.assertEqual(pricing.vin_last6(""), "-")


class TestCostPhasesAndLedger(PricingBase):
    def test_cost_post_phases_and_pricing(self):
        uid = make_user("buyer1", role="buyer")
        self.login("buyer1")
        vid = make_vehicle(buy_price=190000, purchase_date="2026-03-12")
        # 入库前费用
        r = self.post(
            f"/vehicles/{vid}/costs",
            data={"phase": "入库前", "category": "板车费", "amount": "1500",
                  "note": "物流"},
        )
        self.assertEqual(r.status_code, 302)
        # 类别与归属不对应 → 拒绝
        r = self.post(
            f"/vehicles/{vid}/costs",
            data={"phase": "入库前", "category": "洗车", "amount": "100"},
        )
        self.assertEqual(r.status_code, 302)
        # 入库后费用
        self.post(
            f"/vehicles/{vid}/costs",
            data={"phase": "入库后", "category": "洗车", "amount": "400"},
        )
        row = db.query_one("SELECT * FROM vehicles WHERE id=?", (vid,))
        self.assertEqual(
            db.query_one(
                "SELECT COUNT(*) c FROM cost_ledger WHERE vehicle_id=? AND phase='入库前'",
                (vid,))["c"], 1)
        self.assertEqual(
            db.query_one(
                "SELECT COUNT(*) c FROM cost_ledger WHERE vehicle_id=? AND phase='入库后'",
                (vid,))["c"], 1)
        rows = app_module.vehicle_rows("WHERE v.id=?", (vid,))
        self.assertEqual(rows[0]["pre_cost"], 1500)
        self.assertEqual(rows[0]["post_cost"], 400)
        self.assertEqual(rows[0]["total_cost"], 191900)
        self.assertEqual(rows[0]["pricing"]["cost"], 191900)

    def test_ledger_page_and_roles(self):
        make_user("admin1", role="admin")
        make_user("sales1", role="sales")
        self.login("admin1")
        vid = make_vehicle(buy_price=190000, purchase_date="2026-03-12")
        db.execute(
            "INSERT INTO cost_ledger(vehicle_id,phase,category,amount) VALUES(?,?,?,?)",
            (vid, "入库前", "板车费", 282),
        )
        db.execute(
            "INSERT INTO cost_ledger(vehicle_id,phase,category,amount) VALUES(?,?,?,?)",
            (vid, "入库后", "洗车", 24),
        )
        r = self.client.get("/ledger")
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        for needle in ("库存总账", "车辆成本", "当期批发价", "网络标价",
                       "190306", "197306"):
            self.assertIn(needle, html)
        # 销售角色无权查看总账
        self.login("sales1")
        self.assertEqual(self.client.get("/ledger").status_code, 403)

    def test_sale_rebates_flow_into_ledger(self):
        make_user("boss1", role="boss")
        self.login("boss1")
        vid = make_vehicle(buy_price=100000, status="在售")
        r = self.post(
            "/sales",
            data={"vehicle_id": str(vid), "customer": "买家",
                  "price": "120000", "discount": "0",
                  "finance_plan": "全款", "contract_no": "",
                  "sale_channel": "进店客户",
                  "loan_rebate": "1700", "gps_rebate": "0",
                  "insurance_rebate": "500", "warranty_profit": "0",
                  "other_income": "0"},
        )
        self.assertEqual(r.status_code, 302)
        sale = db.query_one(
            "SELECT * FROM sales_orders WHERE vehicle_id=?", (vid,))
        self.assertEqual(sale["loan_rebate"], 1700)
        self.assertEqual(sale["sale_channel"], "进店客户")
        html = self.client.get("/ledger").get_data(as_text=True)
        self.assertIn("122200", html)  # 合计成交价 120000+1700+500
        self.assertIn("进店客户", html)

    def test_restricted_rework_sees_no_pricing(self):
        rw = make_user("rw1", role="rework", name="整备工")
        make_user("buyer1", role="buyer")
        vid = make_vehicle(buy_price=87654, status="整备中")
        db.execute(
            "INSERT INTO rework_orders (vehicle_id, assignee_id, status) "
            "VALUES (?,?,'进行中')", (vid, rw))
        self.login("rw1")
        html = self.client.get(f"/vehicles/{vid}").get_data(as_text=True)
        self.assertNotIn("定价链", html)
        self.assertNotIn("成本明细", html)
        self.assertNotIn("87654", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
