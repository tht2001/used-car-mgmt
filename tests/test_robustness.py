#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""脏数据防崩测试（2026-11-03 线上 500 回归）。

库里只要有一条脏数据（文本/空价格、坏日期、文本费用），所有列表页
都必须正常打开、提交非法数字必须走校验闪现、绝不 500。
运行：cd car-mgmt && python -m pytest tests/test_robustness.py -q
"""
import os
import sys
import tempfile
import unittest

TMPDIR = tempfile.mkdtemp(prefix="car_mgmt_robust_")
os.environ["CAR_MGMT_DB_PATH"] = os.path.join(TMPDIR, "test.db")
os.environ["APP_ENV"] = "testing"
os.environ.pop("SECRET_KEY", None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
import pricing
import app as app_module
from werkzeug.security import generate_password_hash


def make_user(username, password="pw123456", role="admin", name="测试"):
    return db.execute(
        "INSERT INTO users (username, password_hash, name, role) VALUES (?,?,?,?)",
        (username, generate_password_hash(password), name, role),
    )


class RobustBase(unittest.TestCase):
    def setUp(self):
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
        data = dict(data)
        data.setdefault("csrf_token", self.csrf)
        return self.client.post(path, data=data)

    def insert_dirty_data(self):
        """直接写库模拟脏数据（UI 入口已被校验拦住，脏数据只可能来自外部写库/导入）。"""
        admin_id = make_user("admin1", role="admin")
        sales_id = make_user("sales1", role="sales")
        # 文本价格 + 坏日期 + 文本里程
        db.execute(
            "INSERT INTO vehicles (vin, brand, model, year, mileage, color, plate,"
            " buy_price, status, purchase_date, created_at)"
            " VALUES ('V1','丰田','卡罗拉',2021,'很高','白','京A1','abc','在售',"
            " '不是日期','2026-11-03 10:00:00')"
        )
        # 空价格 + 空日期
        db.execute(
            "INSERT INTO vehicles (vin, brand, model, buy_price, status,"
            " purchase_date, created_at)"
            " VALUES ('V2','本田','思域',NULL,'待检测',NULL,NULL)"
        )
        # 干净行 + 文本费用 + 空价销售单 + 文本价销售单
        vid3 = db.execute(
            "INSERT INTO vehicles (vin, brand, model, buy_price, status,"
            " purchase_date, created_at)"
            " VALUES ('V3','大众','朗逸',68000,'已售出','2026-10-01','2026-10-01 09:00:00')"
        )
        db.execute(
            "INSERT INTO cost_ledger (vehicle_id, category, amount, note, phase)"
            " VALUES (1, '洗车', '很多钱', 'x', '入库后')"
        )
        db.execute(
            "INSERT INTO sales_orders (vehicle_id, sales_id, customer, price)"
            " VALUES (?, ?, '客户甲', NULL)",
            (vid3, sales_id),
        )
        db.execute(
            "INSERT INTO sales_orders (vehicle_id, sales_id, customer, price,"
            " loan_rebate, discount) VALUES (?, ?, '客户乙', '天价', '不少', '减')"
            ,
            (vid3, sales_id),
        )
        oid = db.execute(
            "INSERT INTO rework_orders (vehicle_id, assignee_id, status)"
            " VALUES (?, ?, '进行中')",
            (vid3, admin_id),
        )
        db.execute(
            "INSERT INTO rework_items (order_id, item, cost) VALUES (?, '补漆', '贵')",
            (oid,),
        )
        db.execute(
            "INSERT INTO approvals (type, title, applicant_id, amount, status)"
            " VALUES ('采购审批', '脏审批', ?, '很多', '待审批')",
            (admin_id,),
        )
        # 非法系统参数
        db.execute(
            "INSERT INTO settings(key,value) VALUES('buy_high_price_threshold','abc')"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        )
        db.execute(
            "INSERT INTO settings(key,value) VALUES('sales_commission_rate','xyz')"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        )
        return admin_id, sales_id, vid3, oid


class TestDirtyDataPages(RobustBase):
    def test_all_pages_render_with_dirty_data(self):
        admin_id, sales_id, vid3, oid = self.insert_dirty_data()
        self.assertEqual(self.login("admin1").status_code, 302)
        pages = [
            "/", "/vehicles", "/ledger", "/finance", "/acquisition", "/sales",
            "/rework", "/approvals", "/settings", "/reports",
            "/reports/vehicles.csv", "/reports/finance.csv", "/reports/sales.csv",
            "/vehicles/1", "/vehicles/2", "/vehicles/3",
        ]
        for path in pages:
            resp = self.client.get(path)
            self.assertNotEqual(
                resp.status_code, 500, f"{path} 在脏数据下 500"
            )
            self.assertIn(resp.status_code, (200, 302), path)
            self.assertNotIn("Internal Server Error", resp.get_data(as_text=True))

    def test_sales_dashboard_with_dirty_orders(self):
        self.insert_dirty_data()
        self.assertEqual(self.login("sales1").status_code, 302)
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("Internal Server Error", resp.get_data(as_text=True))


class TestBadFormSubmits(RobustBase):
    def test_sales_price_text_does_not_500(self):
        admin_id, _, vid3, _ = self.insert_dirty_data()
        self.login("admin1")
        vid = db.execute(
            "INSERT INTO vehicles (vin, brand, model, buy_price, status)"
            " VALUES ('V9','奥迪','A4',90000,'在售')"
        )
        before = db.query_one("SELECT COUNT(*) c FROM sales_orders")["c"]
        resp = self.post("/sales", {
            "vehicle_id": str(vid), "customer": "客户丙", "price": "abc",
        })
        self.assertIn(resp.status_code, (302, 200))
        after = db.query_one("SELECT COUNT(*) c FROM sales_orders")["c"]
        self.assertEqual(before, after)

    def test_cost_amount_text_does_not_500(self):
        self.insert_dirty_data()
        self.login("admin1")
        before = db.query_one("SELECT COUNT(*) c FROM cost_ledger")["c"]
        resp = self.post("/vehicles/3/costs", {
            "phase": "入库后", "category": "洗车", "amount": "abc",
        })
        self.assertIn(resp.status_code, (302, 200))
        after = db.query_one("SELECT COUNT(*) c FROM cost_ledger")["c"]
        self.assertEqual(before, after)

    def test_approval_amount_text_does_not_500(self):
        self.insert_dirty_data()
        self.login("admin1")
        resp = self.post("/approvals", {
            "type": "采购审批", "title": "测试", "amount": "abc",
        })
        self.assertIn(resp.status_code, (302, 200))

    def test_rework_item_cost_text_does_not_500(self):
        _, _, _, oid = self.insert_dirty_data()
        self.login("admin1")
        before = db.query_one("SELECT COUNT(*) c FROM rework_items")["c"]
        resp = self.post(f"/rework/{oid}/add_item", {"item": "测试", "cost": "abc"})
        self.assertIn(resp.status_code, (302, 200))
        after = db.query_one("SELECT COUNT(*) c FROM rework_items")["c"]
        self.assertEqual(before, after)


class TestPricingRobustness(unittest.TestCase):
    def test_compute_pricing_with_dirty_inputs(self):
        out = pricing.compute_pricing("abc", None, "x", "bad")
        self.assertEqual(out["cost"], 0)
        self.assertEqual(out["wholesale_price"], 4000)
        out2 = pricing.compute_pricing("100000", "200", None, 5)
        self.assertEqual(out2["cost"], 100200)

    def test_total_deal_price_with_dirty_sale(self):
        sale = {"price": "abc", "loan_rebate": None, "gps_rebate": "100",
                "insurance_rebate": "", "warranty_profit": "x",
                "other_income": 50}
        self.assertEqual(pricing.total_deal_price(sale), 150)


class TestOrphanedSession(RobustBase):
    """孤儿会话（旧库登录态 + 新库无此账号）必须回到登录页，绝不 500。

    2026-11-03 线上 500 第二根因：演示库清空后，浏览器旧 session 的
    user_id 在新库不存在，工作台取 g.user['role'] 直接抛错。
    """

    def test_orphaned_session_redirects_to_login(self):
        make_user("fresh", role="admin")
        with self.client.session_transaction() as sess:
            sess["user_id"] = 999
            sess["csrf_token"] = "x" * 64
        for path in ["/", "/vehicles", "/ledger", "/finance", "/sales"]:
            resp = self.client.get(path)
            self.assertNotEqual(resp.status_code, 500, path)
            self.assertEqual(resp.status_code, 302, path)
            self.assertIn("/login", resp.headers["Location"])
        # 清掉孤儿会话后，用新账号能正常登录进工作台
        resp = self.login("fresh")
        self.assertEqual(resp.status_code, 302)
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
