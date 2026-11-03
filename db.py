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
    # 车辆附件：车辆照片/证件资料/其他照片/收购车况视频（kind 区分）
    # 文件存 static/uploads/<vehicle_id>/，生产环境迁对象存储（OSS）
    """
    CREATE TABLE IF NOT EXISTS vehicle_files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        vehicle_id INTEGER NOT NULL,
        kind TEXT NOT NULL,
        path TEXT NOT NULL,
        original_name TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )
    """,
]

# 车辆扩展字段（大风车 APP「新建车辆」7 大类栏目，2026-11-01 客户要求补齐）。
# 仅新增列，全部可空，旧库/旧数据不受影响；init_db() 幂等补列。
# 金额单位：库内统一用“元”，仅“万元”标注的字段在表单层按万元录入、入库时换算。
VEHICLE_EXTRA_COLUMNS = [
    # 1 基础信息
    ("vehicle_type", "TEXT"),        # 车辆类型：二手车/新车
    ("first_plate_date", "TEXT"),    # 首次上牌日期（空=未上牌）
    ("sales_mode", "TEXT"),          # 销售方式：批零兼售/零售/批发
    ("evaluator", "TEXT"),           # 评估师姓名
    ("store", "TEXT"),               # 门店
    # 2 车辆档案
    ("vehicle_no", "TEXT"),          # 车辆编号
    ("stock_status", "TEXT"),        # 库存状态：在厅/仓库/在途/已预订
    ("stock_area", "TEXT"),          # 库存区域
    ("stock_location", "TEXT"),      # 库存位置
    ("stock_desc", "TEXT"),          # 库存描述
    ("weidian_status", "TEXT"),      # 微店上架：未上架/已上架（仅存状态，不联动微店）
    ("use_type", "TEXT"),            # 使用性质：非营运/营运/营转非/租赁
    ("ownership", "TEXT"),           # 所有权归属：公户/私户
    ("engine_no", "TEXT"),           # 发动机号
    ("manufacture_date", "TEXT"),    # 出厂日期
    ("license_vehicle_type", "TEXT"),  # 行驶证车辆类型
    ("license_model", "TEXT"),       # 行驶证品牌型号
    ("registration_no", "TEXT"),     # 登记证号
    ("transfer_dmv", "TEXT"),        # 转入地车辆管理所
    ("interior_color", "TEXT"),      # 内饰颜色
    ("new_car_price", "REAL"),       # 新车指导价（万元）
    ("purchase_tax", "REAL"),        # 购置税（万元）
    ("addon_cost", "REAL"),          # 加装费用（元）
    ("addon_content", "TEXT"),       # 加装内容
    ("location", "TEXT"),            # 所在地
    ("stock_age_warn_days", "INTEGER"),  # 库龄预警天数
    ("booking_warn_days", "INTEGER"),    # 预定预警天数
    ("weidian_warn_days", "INTEGER"),    # 微店上架预警天数（仅存值）
    ("listing_warn_days", "INTEGER"),    # 车源上架预警天数
    # 3 车况详情
    ("condition_summary", "TEXT"),   # 车况概述
    ("condition_internal", "TEXT"),  # 车况（对内）
    # 4 价格信息（万元字段在表单层按万元录入，入库统一为元）
    ("online_price", "REAL"),        # 网络标价
    ("installment_price", "REAL"),   # 分期价
    ("showroom_price", "REAL"),      # 展厅标价
    ("floor_price", "REAL"),         # 销售底价
    ("manager_price", "REAL"),       # 经理底价
    ("wholesale_price", "REAL"),     # 批发价
    ("show_wholesale", "TEXT"),      # 展示批发价：对外展示/仅对内
    ("alliance_price", "REAL"),      # 联盟批发价
    ("weidian_deposit", "REAL"),     # 微店订金（元）
    # 5 评估采购信息
    ("purchase_type", "TEXT"),       # 采购类型：收购/寄售/置换/其他
    ("purchase_source", "TEXT"),     # 收购来源：同行批发/朋友/客户介绍/门店/粉丝/58资源包/懂车帝资源包/中介/调同行车
    ("paid_amount", "REAL"),         # 已付金额（元）
    ("payee_company", "TEXT"),       # 收款公司
    ("purchase_date", "TEXT"),       # 采购日期
    ("transfer_method", "TEXT"),     # 采购过户方式
    ("pickup_method", "TEXT"),       # 收车方式：店内收车/异地物流收车
    ("beihu_name", "TEXT"),          # 背户人姓名（敏感，仅非受限角色可见）
    ("beihu_phone", "TEXT"),         # 背户人手机号
    ("beihu_idcard", "TEXT"),        # 背户人身份证
    ("beihu_company", "TEXT"),       # 企业名称
    ("rework_plan_start", "TEXT"),   # 预计整备开始
    ("rework_plan_end", "TEXT"),     # 预计整备完成
    ("rework_plan_items", "TEXT"),   # 预计整备项
    ("rework_plan_cost", "REAL"),    # 预计整备费用（元）
    # 6 车主信息（敏感，仅非受限角色可见；姓名/电话复用既有 customer_* 列）
    ("owner_type", "TEXT"),          # 客户类型：直客/合作商
    ("owner_source", "TEXT"),        # 客户来源
    ("payee_name", "TEXT"),          # 收款人
    ("bank_card", "TEXT"),           # 银行卡号
    ("bank_name", "TEXT"),           # 开户行
    ("owner_idcard", "TEXT"),        # 身份证号
    ("other_account", "TEXT"),       # 其他账户
    ("owner_address", "TEXT"),       # 地址
    # 7 牌证信息（有/无）
    ("cert_registration", "TEXT"),   # 登记证
    ("cert_driving", "TEXT"),        # 行驶证
    ("cert_owner_idcard", "TEXT"),   # 原车主身份证
    ("cert_transfer_video", "TEXT"),  # 过户视频
    ("cert_invoice", "TEXT"),        # 进项发票
    ("cert_conformity", "TEXT"),     # 一致性证书
    ("cert_env", "TEXT"),            # 环保清单
    ("cert_qualification", "TEXT"),  # 合格证
    ("cert_plate_photo", "TEXT"),    # 车辆牌照
    ("transfer_count", "INTEGER"),   # 过户次数
    ("key_count", "INTEGER"),        # 钥匙数量
    ("insurance_compulsory", "TEXT"),  # 交强险：有/无
    ("insurance_compulsory_expiry", "TEXT"),  # 交强险到期
    ("insurance_commercial", "TEXT"),  # 商业险：有/无
    ("insurance_commercial_expiry", "TEXT"),  # 商业险到期
    ("insurance_commercial_amount", "REAL"),  # 商业险金额（元）
    ("annual_inspection_expiry", "TEXT"),  # 年检到期日
    ("cert_note", "TEXT"),           # 备注
]

# role: admin/boss/finance/sales/buyer/rework
DEFAULT_SETTINGS = [
    ("company_name", "娇姐严选新能源"),
    ("sales_commission_rate", "0.05"),
    ("buy_high_price_threshold", "150000"),
    ("stock_standard",
     "1. 无重大事故、水泡、火烧；\n"
     "2. 里程表无调表嫌疑；\n"
     "3. 手续齐全，可正常过户；\n"
     "4. 整备项目全部完成并质检通过。"),
]

# 客户组织架构（客户管理表单所载部门结构）。部门是系统运转必需的
# 基础数据（新建用户时从下拉选择），不是演示数据：空白库也必须有，
# 否则设置页“部门”下拉为空且系统内无任何新增部门入口。
# 按名称幂等补齐（name 列 UNIQUE），绝不覆盖、删除已有部门。
DEFAULT_DEPARTMENTS = (
    "总店",
    "销售一部", "销售二部", "销售三部",
    "收购一部", "收购二部", "收购三部",
    "后勤", "摄影",
)


def get_conn() -> sqlite3.Connection:
    """返回新的 sqlite3 连接，row_factory=sqlite3.Row。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """建全部表（不存在才建），写入默认 settings 与部门（已存在则跳过），补齐车辆扩展列。"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_conn()
    try:
        for ddl in SCHEMA:
            conn.execute(ddl)
        _migrate_vehicle_columns(conn)
        _migrate_cost_ledger_phase(conn)
        _migrate_sales_rebates(conn)
        conn.executemany(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            DEFAULT_SETTINGS,
        )
        conn.executemany(
            "INSERT OR IGNORE INTO departments (name) VALUES (?)",
            [(name,) for name in DEFAULT_DEPARTMENTS],
        )
        conn.commit()
    finally:
        conn.close()


def _migrate_cost_ledger_phase(conn: sqlite3.Connection) -> None:
    """cost_ledger 增加 phase 列（入库前/入库后），旧行按类别回填。

    类别为“整备费用”的旧行均为整备产生，归入“入库后”；其余旧行
    无法判定归属，phase 留空，计入“未分类费用”，仍计入总成本。
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(cost_ledger)")}
    if "phase" not in cols:
        conn.execute("ALTER TABLE cost_ledger ADD COLUMN phase TEXT")
        conn.execute(
            "UPDATE cost_ledger SET phase='入库后' WHERE category='整备费用'"
        )


SALES_REBATE_COLUMNS = [
    ("loan_rebate", "REAL DEFAULT 0"),       # 贷款返点
    ("gps_rebate", "REAL DEFAULT 0"),        # GPS 返点
    ("insurance_rebate", "REAL DEFAULT 0"),  # 保险返点
    ("warranty_profit", "REAL DEFAULT 0"),   # 质保利润
    ("other_income", "REAL DEFAULT 0"),      # 其他（成交相关收入）
    ("sale_channel", "TEXT DEFAULT ''"),     # 销售渠道：进店客户/抖音客户/懂车帝/…
]


def _migrate_sales_rebates(conn: sqlite3.Connection) -> None:
    """sales_orders 增加返点列（合计成交价 AW 的组成部分）与销售渠道，幂等补列。"""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(sales_orders)")}
    for name, ddl in SALES_REBATE_COLUMNS:
        if name not in cols:
            conn.execute(f"ALTER TABLE sales_orders ADD COLUMN {name} {ddl}")


def _migrate_vehicle_columns(conn: sqlite3.Connection) -> None:
    """幂等补列：vehicles 表缺失的扩展字段逐一 ALTER TABLE 增加。

    旧库直接升级，不动已有数据；新库建表后本函数无操作。
    """
    existing = {row[1] for row in conn.execute("PRAGMA table_info(vehicles)")}
    for name, ddl in VEHICLE_EXTRA_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE vehicles ADD COLUMN {name} {ddl}")


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
