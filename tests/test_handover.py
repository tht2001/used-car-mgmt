"""交接空白交付测试：默认不灌演示数据，管理员只按环境变量创建。"""
import importlib.util
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from werkzeug.security import check_password_hash

TMPDIR = tempfile.mkdtemp(prefix="car_mgmt_handover_")
os.environ["CAR_MGMT_DB_PATH"] = os.path.join(TMPDIR, "test.db")

import db  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_create_admin():
    spec = importlib.util.spec_from_file_location(
        "handover_create_admin", os.path.join(REPO, "scripts", "create_admin.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fresh_db():
    db.DB_PATH = os.path.join(TMPDIR, "test.db")
    if os.path.exists(db.DB_PATH):
        os.remove(db.DB_PATH)
    db.init_db()


def _count(db_path, table):
    con = sqlite3.connect(db_path)
    try:
        return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        con.close()


def _run_script(script, db_path, app_env="testing", **extra_env):
    env = {
        "PATH": os.environ["PATH"],
        "CAR_MGMT_DB_PATH": db_path,
        "APP_ENV": app_env,
        "PYTHONUTF8": "1",
    }
    env.update(extra_env)
    return subprocess.run(
        [sys.executable, script],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _no_admin_env():
    ctx = mock.patch.dict(os.environ, {}, clear=False)
    ctx.start()
    os.environ.pop("ADMIN_USERNAME", None)
    os.environ.pop("ADMIN_PASSWORD", None)
    return ctx


class TestEnsureAdminFromEnv(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        self.create_admin = _load_create_admin()

    def test_skipped_without_env(self):
        with _no_admin_env():
            assert self.create_admin.ensure_admin_from_env() == "skipped"
        assert _count(db.DB_PATH, "users") == 0

    def test_created_and_idempotent(self):
        with mock.patch.dict(
            os.environ,
            {"ADMIN_USERNAME": "jiaojie", "ADMIN_PASSWORD": "JiaoJie2026!"},
            clear=False,
        ):
            assert self.create_admin.ensure_admin_from_env() == "created:jiaojie"
            assert self.create_admin.ensure_admin_from_env() == "exists"
        assert _count(db.DB_PATH, "users") == 1
        row = db.query_one("SELECT * FROM users WHERE username='jiaojie'")
        assert row["role"] == "admin"
        assert check_password_hash(row["password_hash"], "JiaoJie2026!")

    def test_existing_admin_not_overwritten(self):
        with mock.patch.dict(
            os.environ,
            {"ADMIN_USERNAME": "first", "ADMIN_PASSWORD": "pw-first"},
            clear=False,
        ):
            assert self.create_admin.ensure_admin_from_env() == "created:first"
        with mock.patch.dict(
            os.environ,
            {"ADMIN_USERNAME": "second", "ADMIN_PASSWORD": "pw-second"},
            clear=False,
        ):
            assert self.create_admin.ensure_admin_from_env() == "exists"
        assert _count(db.DB_PATH, "users") == 1
        row = db.query_one("SELECT * FROM users")
        assert row["username"] == "first"
        assert check_password_hash(row["password_hash"], "pw-first")


class TestInitDemoScript:
    def _db_path(self, name):
        path = os.path.join(TMPDIR, name)
        if os.path.exists(path):
            os.remove(path)
        return path

    def test_blank_by_default(self):
        path = self._db_path("blank.db")
        res = _run_script(os.path.join("scripts", "init_demo.py"), path)
        assert res.returncode == 0, res.stderr
        assert "跳过演示数据" in res.stdout
        assert _count(path, "users") == 0
        assert _count(path, "vehicles") == 0

    def test_seeds_when_flagged(self):
        path = self._db_path("seeded.db")
        res = _run_script(
            os.path.join("scripts", "init_demo.py"), path, SEED_DEMO="1"
        )
        assert res.returncode == 0, res.stderr
        assert _count(path, "users") == 9
        assert _count(path, "vehicles") == 12

    def test_creates_admin_from_env(self):
        path = self._db_path("admin.db")
        res = _run_script(
            os.path.join("scripts", "init_demo.py"),
            path,
            ADMIN_USERNAME="jiaojie",
            ADMIN_PASSWORD="JiaoJie2026!",
        )
        assert res.returncode == 0, res.stderr
        assert _count(path, "users") == 1
        res2 = _run_script(
            os.path.join("scripts", "init_demo.py"),
            path,
            ADMIN_USERNAME="jiaojie",
            ADMIN_PASSWORD="JiaoJie2026!",
        )
        assert res2.returncode == 0, res2.stderr
        assert _count(path, "users") == 1

    def test_refuses_production(self):
        path = self._db_path("prod.db")
        res = _run_script(
            os.path.join("scripts", "init_demo.py"), path, app_env="production"
        )
        assert res.returncode == 1
        assert not os.path.exists(path)


class TestSeedCli:
    def _db_path(self, name):
        path = os.path.join(TMPDIR, name)
        if os.path.exists(path):
            os.remove(path)
        return path

    def test_blank_by_default(self):
        path = self._db_path("seed_blank.db")
        res = _run_script("seed.py", path)
        assert res.returncode == 0, res.stderr
        assert _count(path, "users") == 0
        assert _count(path, "vehicles") == 0

    def test_seeds_when_flagged(self):
        path = self._db_path("seed_flagged.db")
        res = _run_script("seed.py", path, SEED_DEMO="1")
        assert res.returncode == 0, res.stderr
        assert _count(path, "users") == 9
        assert _count(path, "vehicles") == 12


EXPECTED_DEPARTMENTS = [
    "总店",
    "销售一部", "销售二部", "销售三部",
    "收购一部", "收购二部", "收购三部",
    "后勤", "摄影",
]


class TestDefaultDepartments(unittest.TestCase):
    """空白库也必须预置客户组织架构的 9 个部门（设置页新增用户下拉的唯一来源）。

    部门是基础数据、不是演示数据；按名幂等补齐，重复建库不翻倍。
    """

    def setUp(self):
        _fresh_db()

    def test_blank_db_has_nine_departments(self):
        con = sqlite3.connect(db.DB_PATH)
        try:
            names = [r[0] for r in con.execute(
                "SELECT name FROM departments ORDER BY id")]
        finally:
            con.close()
        self.assertEqual(names, EXPECTED_DEPARTMENTS)

    def test_init_db_twice_does_not_duplicate(self):
        db.init_db()
        self.assertEqual(_count(db.DB_PATH, "departments"), 9)

    def test_settings_page_lists_departments(self):
        import app as app_module
        from werkzeug.security import generate_password_hash
        app_module.db.DB_PATH = db.DB_PATH
        # 先建管理员再设 session（否则孤儿会话清理会把未建好的登录态清掉）
        db.execute(
            "INSERT INTO users(username,password_hash,name,role,active) "
            "VALUES('admin',?,'管理员','admin',1)",
            (generate_password_hash("123456"),),
        )
        client = app_module.app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = 1
        resp = client.get("/settings")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        for name in EXPECTED_DEPARTMENTS:
            self.assertIn(name, html)
