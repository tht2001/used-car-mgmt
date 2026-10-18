"""二手车业务管理系统 — SQLite 数据层。

只使用标准库 sqlite3，不依赖 ORM。表结构定义与建库逻辑集中在此，
业务代码通过 get_conn / query / query_one / execute / log_action 访问数据。

注意：本模块 import 时不做任何建表动作，建表请显式调用 init_db()。
"""

import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 默认库路径；可用环境变量 CAR_MGMT_DB_PATH 覆盖（测试/多环境隔离用）
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "data", "car_mgmt.db")
DB_PATH = os.environ.get("CAR_MGMT_DB_PATH", DEFAULT_DB_PATH)

DEFAULT_CREATED_AT = "datetime('now','localtime')"

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS departments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        name TEXT NOT NULL,
        role TEXT NOT NULL,
        dept_id INTEGER,
        phone TEXT DEFAULT '',
        active INTEGER DEFAULT 1,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )
    """,
    # status: 待检测/待整备/整备中/待入库审核/在售/已售出/已退回
    """
    CREATE TABLE IF NOT EXISTS vehicles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        vin TEXT,
        brand TEXT,
        model TEXT,
        year INTEGER,
        mileage REAL,
        color TEXT,
        plate TEXT,
        buy_price REAL DEFAULT 0,
        status TEXT DEFAULT '待检测',
        buyer_id INTEGER,
        sales_id INTEGER,
        customer_name TEXT DEFAULT '',
        customer_phone TEXT DEFAULT '',
        note TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )
    """,
    # result: 通过/退回
    """
    CREATE TABLE IF NOT EXISTS inspections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        vehicle_id INTEGER NOT NULL,
        inspector TEXT,
        score INTEGER,
        issues TEXT,
        photos_note TEXT DEFAULT '',
        result TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )
    """,
    # status: 待处理/进行中/已完成
    """
    CREATE TABLE IF NOT EXISTS rework_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        vehicle_id INTEGER NOT NULL,
        assignee_id INTEGER,
        status TEXT DEFAULT '待处理',
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rework_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        item TEXT,
        cost REAL DEFAULT 0,
        supplier TEXT DEFAULT '',
        pay_method TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sales_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        vehicle_id INTEGER NOT NULL,
        sales_id INTEGER,
        customer TEXT,
        phone TEXT DEFAULT '',
        price REAL DEFAULT 0,
        discount REAL DEFAULT 0,
        finance_plan TEXT DEFAULT '',
        transfer_status TEXT DEFAULT '未办理',
        delivery_status TEXT DEFAULT '待交付',
        contract_no TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )
    """,
    # category: 整备费用/其他（只增不改，成本修改留痕）
    """
    CREATE TABLE IF NOT EXISTS cost_ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        vehicle_id INTEGER NOT NULL,
        category TEXT,
        amount REAL DEFAULT 0,
        note TEXT DEFAULT '',
        created_by INTEGER,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )
    """,
    # type: 收购高价审批/销售优惠审批/报销审批；status: 待审批/已通过/已驳回
    """
    CREATE TABLE IF NOT EXISTS approvals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT,
        title TEXT,
        applicant_id INTEGER,
        amount REAL DEFAULT 0,
        reason TEXT DEFAULT '',
        status TEXT DEFAULT '待审批',
        approver_id INTEGER,
        comment TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS operation_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        action TEXT,
        target TEXT DEFAULT '',
        detail TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """,
]

# role: admin/boss/finance/sales/buyer/rework
DEFAULT_SETTINGS = [
    ("company_name", "诚信二手车"),
    ("sales_commission_rate", "0.05"),
    ("buy_high_price_threshold", "150000"),
    ("stock_standard",
     "1. 无重大事故、水泡、火烧；\n"
     "2. 里程表无调表嫌疑；\n"
     "3. 手续齐全，可正常过户；\n"
     "4. 整备项目全部完成并质检通过。"),
]


def get_conn() -> sqlite3.Connection:
    """返回新的 sqlite3 连接，row_factory=sqlite3.Row。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """建全部表（不存在才建），写入默认 settings（已存在则跳过）。"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_conn()
    try:
        for ddl in SCHEMA:
            conn.execute(ddl)
        conn.executemany(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            DEFAULT_SETTINGS,
        )
        conn.commit()
    finally:
        conn.close()


def query(sql: str, args: tuple = ()) -> list[dict]:
    """执行 SELECT，返回字典列表。"""
    conn = get_conn()
    try:
        cur = conn.execute(sql, args)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def query_one(sql: str, args: tuple = ()) -> dict | None:
    """执行 SELECT，返回第一行字典，无结果返回 None。"""
    conn = get_conn()
    try:
        cur = conn.execute(sql, args)
        row = cur.fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def execute(sql: str, args: tuple = ()) -> int:
    """执行 INSERT/UPDATE/DELETE，自动 commit，返回 lastrowid。"""
    conn = get_conn()
    try:
        cur = conn.execute(sql, args)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def log_action(user_id: int, action: str, target: str = "", detail: str = "") -> int:
    """写入一条操作日志到 operation_logs，返回日志 id。"""
    return execute(
        "INSERT INTO operation_logs (user_id, action, target, detail) "
        "VALUES (?, ?, ?, ?)",
        (user_id, action, target, detail),
    )
