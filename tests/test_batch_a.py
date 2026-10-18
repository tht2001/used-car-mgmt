#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批次 A 回归测试（unittest + Flask test_client，零新增依赖）。

关键约束：全部用例使用临时数据库 —— 模块导入前即把 CAR_MGMT_DB_PATH
指向 /tmp 下临时文件、APP_ENV=testing，绝不触碰 data/car_mgmt.db。

运行：cd car-mgmt && python -m unittest discover -s tests -v
"""
import importlib
import os
import sqlite3
import subprocess
import sys
import tempfile

TMPDIR = tempfile.mkdtemp(prefix="car_mgmt_batch_a_")
os.environ["CAR_MGMT_DB_PATH"] = os.path.join(TMPDIR, "test.db")
os.environ["APP_ENV"] = "testing"
os.environ.pop("SECRET_KEY", None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

import db
import app as app_module
from werkzeug.security import generate_password_hash

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def make_user(username, password="pw123456", role="boss", name="测试"):
    return db.execute(
        "INSERT INTO users (username, password_hash, name, role) VALUES (?,?,?,?)",
        (username, generate_password_hash(password), name, role),
    )


def make_vehicle(buy_price=80000, status="待检测", buyer_id=None):
    return db.execute(
        "INSERT INTO vehicles (vin, brand, model, buy_price, status, buyer_id) "
        "VALUES (?,?,?,?,?,?)",
        ("TESTVIN" + os.urandom(4).hex(), "测试品牌", "测试车型",
         buy_price, status, buyer_id),
    )


class BatchABase(unittest.TestCase):
    def setUp(self):
        p = db.DB_PATH
        self.assertTrue(
            p.startswith(TMPDIR),
            f"测试库路径异常，拒绝执行以防误伤真实库：{p}",
        )
        if os.path.exists(p):
            os.remove(p)
        db.init_db()
        self.client = app_module.app.test_client()

    def login(self, username, password="pw123456", csrf=True):
        """登录并返回 (response, csrf_token)。"""
        self.client.get("/login")
        with self.client.session_transaction() as sess:
            token = sess.get("csrf_token", "")
        data = {"username": username, "password": password}
        if csrf:
            data["csrf_token"] = token
        resp = self.client.post("/login", data=data)
        with self.client.session_transaction() as sess:
            new_token = sess.get("csrf_token", "")
        return resp, new_token


class TestStartupKeepsData(BatchABase):
    def test_startup_keeps_data(self):
        """临时库建用户和车辆后，重新 import 应用 / 再次 init，数据仍在。"""
        uid = make_user("keepme", role="admin", name="保留")
        vid = make_vehicle(buy_price=12345, buyer_id=uid)
        importlib.reload(app_module)  # 模拟重启后重新导入应用
        db.init_db()  # 再次建表（幂等）
        u = db.query_one("SELECT * FROM users WHERE id=?", (uid,))
        v = db.query_one("SELECT * FROM vehicles WHERE id=?", (vid,))
        self.assertIsNotNone(u)
        self.assertEqual(u["username"], "keepme")
        self.assertIsNotNone(v)
        self.assertEqual(v["buy_price"], 12345)


class TestProductionGuards(unittest.TestCase):
    def _env(self, **over):
        env = dict(os.environ)
        env["APP_ENV"] = "production"
        env.pop("SECRET_KEY", None)
        env.update(over)
        return env

    def test_production_requires_secret(self):
        """APP_ENV=production 且无 SECRET_KEY 时，导入应用必须失败（非零退出）。"""
        r = subprocess.run(
            [sys.executable, "-c", "import app"],
            cwd=REPO_ROOT, env=self._env(),
            capture_output=True, text=True, timeout=60,
        )
        self.assertNotEqual(r.returncode, 0, "生产缺密钥时应用应当拒绝启动")
        self.assertIn("SECRET_KEY", r.stderr)

    def test_seed_refused_in_production(self):
        """生产环境执行 seed --confirm-reset 被拒绝，目标库文件不被删除/改动。"""
        tmpdb = os.path.join(TMPDIR, "prod_guard.db")
        conn = sqlite3.connect(tmpdb)
        conn.execute("CREATE TABLE marker (id INTEGER PRIMARY KEY, note TEXT)")
        conn.execute("INSERT INTO marker (note) VALUES ('do-not-touch')")
        conn.commit()
        conn.close()
        before = os.path.getmtime(tmpdb)

        r = subprocess.run(
            [sys.executable, "seed.py", "--confirm-reset"],
            cwd=REPO_ROOT,
            env=self._env(CAR_MGMT_DB_PATH=tmpdb),
            capture_output=True, text=True, timeout=60,
        )
        self.assertNotEqual(r.returncode, 0, "生产环境 seed 必须被拒绝")
        self.assertTrue(os.path.exists(tmpdb), "生产库文件不应被删除")
        self.assertEqual(os.path.getmtime(tmpdb), before, "生产库文件不应被改动")
        conn = sqlite3.connect(tmpdb)
        note = conn.execute("SELECT note FROM marker").fetchone()[0]
        conn.close()
        self.assertEqual(note, "do-not-touch")


class TestCsrf(BatchABase):
    def test_csrf(self):
        make_user("boss1", role="boss", name="老板")
        # 1. 未登录、无 token 的登录 POST -> 400
        r = self.client.post("/login", data={"username": "boss1",
                                             "password": "pw123456"})
        self.assertEqual(r.status_code, 400)
        # 2. 带 token 登录成功
        resp, token = self.login("boss1")
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(token)
        # 3. 登录后无 token 的业务 POST -> 400
        r = self.client.post("/approvals", data={
            "type": "报销审批", "title": "x", "amount": "10"})
        self.assertEqual(r.status_code, 400)
        # 4. 带 token 的业务 POST -> 正常（302 跳转）
        r = self.client.post("/approvals", data={
            "type": "报销审批", "title": "x", "amount": "10",
            "csrf_token": token})
        self.assertEqual(r.status_code, 302)
        row = db.query_one("SELECT * FROM approvals WHERE title='x'")
        self.assertIsNotNone(row)


class TestDashboardStatus(BatchABase):
    def test_dashboard_status(self):
        make_user("boss2", role="boss", name="老板")
        make_vehicle(buy_price=50000, status="待检测")
        make_vehicle(buy_price=60000, status="在售")
        resp, _ = self.login("boss2")
        self.assertEqual(resp.status_code, 302)
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        for s in app_module.STATUSES:
            self.assertIn(s, html, f"状态 {s} 应显示在工作台")
        # F11 回归：模板曾把字典键当对象，数量渲染成 bound method
        self.assertNotIn("bound method", html)


class TestReworkRestrictedDetail(BatchABase):
    def test_rework_restricted_detail(self):
        rw = make_user("rw1", role="rework", name="整备工")
        make_user("other", role="buyer", name="收购员")
        vid_mine = make_vehicle(buy_price=87654, status="整备中")
        db.execute(
            "INSERT INTO rework_orders (vehicle_id, assignee_id, status) "
            "VALUES (?,?,'进行中')", (vid_mine, rw))
        vid_other = make_vehicle(buy_price=99999, status="在售")

        resp, _ = self.login("rw1")
        self.assertEqual(resp.status_code, 302)

        # 自己的工单车辆：200，且不含收购价数字与财务区块
        r = self.client.get(f"/vehicles/{vid_mine}")
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertNotIn("87654", html)
        self.assertNotIn("成本明细", html)
        self.assertNotIn("销售订单", html)
        self.assertNotIn("操作记录", html)
        # 允许看到：基本资料、检测结论、整备工单
        self.assertIn("基本资料", html)
        self.assertIn("检测记录", html)
        self.assertIn("整备工单", html)

        # 无工单车辆：403
        r = self.client.get(f"/vehicles/{vid_other}")
        self.assertEqual(r.status_code, 403)

        # 工作台整备工单链接指向车辆 id（而非工单 id）
        r = self.client.get("/")
        html = r.get_data(as_text=True)
        self.assertIn(f"/vehicles/{vid_mine}", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
