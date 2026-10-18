"""演示数据初始化脚本（显式命令，非破坏性默认行为）。

- load_demo_data()：建表（幂等）并在 users 表为空时插入演示数据；绝不删库。
- CLI 破坏性重置（删除重建演示库）必须同时满足：
  APP_ENV ∈ {development, demo, testing} 且带 --confirm-reset 参数。
- APP_ENV=production 时任何参数都拒绝执行（非零退出），生产库永不 seed。

运行方式（在 car-mgmt 目录下）：
  python seed.py                  # 建表 + 库为空时灌演示数据（安全，可重复跑）
  python seed.py --confirm-reset   # 删除重建演示库（仅开发/演示/测试环境）
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
from werkzeug.security import generate_password_hash

ALLOWED_RESET_ENVS = {"development", "demo", "testing"}

TABLES = [
    "departments", "users", "vehicles", "inspections", "rework_orders",
    "rework_items", "sales_orders", "cost_ledger", "approvals",
    "operation_logs", "settings",
]

DEFAULT_PASSWORD = "123456"

DEPARTMENTS = [
    (1, "收购部"),
    (2, "销售部"),
    (3, "整备部"),
    (4, "财务部"),
]

# (username, name, role, dept_id, phone)
USERS = [
    ("admin", "超级管理员", "admin", None, "13800000000"),
    ("boss", "老板", "boss", None, "13800000001"),
    ("caiwu", "财务-小陈", "finance", 4, "13800000002"),
    ("sales1", "销售-小刘", "sales", 2, "13800000003"),
    ("sales2", "销售-小杨", "sales", 2, "13800000004"),
    ("buyer1", "收购-李明", "buyer", 1, "13800000005"),
    ("buyer2", "收购-王芳", "buyer", 1, "13800000006"),
    ("rework1", "整备-赵师傅", "rework", 3, "13800000007"),
    ("rework2", "整备-孙师傅", "rework", 3, "13800000008"),
]

# (vin, brand, model, year, mileage, color, plate, buy_price, status,
#  buyer_id, sales_id, customer_name, customer_phone, note, created_at)
VEHICLES = [
    ("LVR12345678900001", "丰田", "卡罗拉", 2021, 6.5, "白", "沪A8K321", 85000, "待检测", 6, None, "张先生", "13800001111", "车况良好", "2026-10-11 10:00:00"),
    ("2HG12345678900002", "本田", "思域", 2020, 7.2, "黑", "沪B3M782", 92000, "待检测", 7, None, "刘女士", "13800002222", "", "2026-10-11 15:30:00"),
    ("LSV12345678900003", "大众", "朗逸", 2019, 8.1, "银", "沪C9P456", 68000, "待整备", 6, None, "陈先生", "13800003333", "", "2026-10-08 09:20:00"),
    ("LDK12345678900004", "日产", "轩逸", 2021, 5.3, "白", "沪D2Q987", 78000, "待整备", 7, None, "赵女士", "13800004444", "", "2026-10-08 14:10:00"),
    ("LSG12345678900005", "别克", "英朗", 2020, 6.9, "灰", "沪E5R654", 72000, "整备中", 6, None, "孙先生", "13800005555", "", "2026-10-06 11:00:00"),
    ("KMH12345678900006", "现代", "伊兰特", 2019, 9.0, "黑", "沪F7T321", 60000, "待入库审核", 7, None, "周先生", "13800006666", "", "2026-10-05 10:40:00"),
    ("LVR12345678900007", "丰田", "雷凌", 2022, 3.2, "蓝", "沪G1W234", 105000, "在售", 6, None, "吴女士", "13800007777", "", "2026-09-28 09:00:00"),
    ("1HG12345678900008", "本田", "雅阁", 2021, 4.8, "白", "沪H4X567", 145000, "在售", 7, None, "郑先生", "13800008888", "", "2026-09-29 13:20:00"),
    ("LFV12345678900009", "大众", "速腾", 2020, 5.5, "银", "沪J6Y890", 98000, "在售", 6, None, "王女士", "13800009999", "", "2026-09-30 10:10:00"),
    ("WAU12345678900010", "奥迪", "A4L", 2019, 7.8, "黑", "沪K8Z123", 165000, "已售出", 6, 4, "钱先生", "13800001010", "", "2026-09-15 09:30:00"),
    ("WBA12345678900011", "宝马", "3系", 2020, 6.1, "白", "沪L2A345", 178000, "已售出", 7, 5, "冯女士", "13800002020", "", "2026-09-18 14:00:00"),
    ("WF012345678900012", "福特", "福克斯", 2018, 10.2, "红", "沪M3B678", 45000, "已退回", 6, None, "褚先生", "13800003030", "疑似事故车", "2026-10-10 16:00:00"),
]

# (vehicle_id, inspector, score, issues, result, created_at)
INSPECTIONS = [
    (3, "收购-李明", 85, "左前门小划痕；轮胎磨损正常", "通过", "2026-10-09 10:00:00"),
    (4, "收购-王芳", 88, "内饰轻微磨损", "通过", "2026-10-09 11:00:00"),
    (5, "收购-李明", 80, "需更换轮胎、钣金喷漆", "通过", "2026-10-07 09:30:00"),
    (6, "收购-王芳", 82, "常规保养+电瓶更换", "通过", "2026-10-06 10:00:00"),
    (10, "收购-李明", 90, "车况精品", "通过", "2026-09-16 09:00:00"),
    (11, "收购-王芳", 92, "车况精品", "通过", "2026-09-19 09:00:00"),
    (12, "收购-李明", 45, "右后纵梁修复痕迹，疑似事故车", "退回", "2026-10-11 09:00:00"),
]

# (vehicle_id, assignee_id, status, created_at, [(item, cost, supplier, pay_method, created_at)])
REWORK_DATA = [
    (5, 8, "进行中", "2026-10-07 14:00:00", [
        ("更换轮胎", 1800, "米其林经销", "对公转账", "2026-10-08 10:00:00"),
        ("钣金喷漆", 2500, "快修店", "现金", "2026-10-09 11:00:00"),
    ]),
    (6, 9, "已完成", "2026-10-06 15:00:00", [
        ("常规保养", 800, "4S店", "对公转账", "2026-10-07 09:00:00"),
        ("更换电瓶", 600, "电瓶专卖", "现金", "2026-10-07 14:00:00"),
    ]),
    (10, 8, "已完成", "2026-09-16 10:00:00", [
        ("全车抛光打蜡", 1200, "美容店", "现金", "2026-09-17 10:00:00"),
    ]),
    (11, 9, "已完成", "2026-09-19 10:00:00", [
        ("更换刹车片", 1500, "4S店", "对公转账", "2026-09-20 10:00:00"),
    ]),
]

# (vehicle_id, sales_id, customer, phone, price, discount, finance_plan,
#  transfer_status, delivery_status, contract_no, created_at)
SALES_ORDERS = [
    (10, 4, "李女士", "13900001111", 188000, 2000, "全款", "已完成", "已交付", "XS202610001", "2026-10-02 15:00:00"),
    (11, 5, "王先生", "13900002222", 205000, 0, "分期（36期）", "已完成", "已交付", "XS202610002", "2026-10-06 11:30:00"),
]

# (type, title, applicant_id, amount, reason, status, approver_id, comment, created_at)
APPROVALS = [
    ("销售优惠审批", "雅阁优惠2000元促成交", 4, 2000, "客户要求优惠，竞品压价", "待审批", None, "", "2026-10-11 10:00:00"),
    ("报销审批", "整备工具采购报销", 8, 1500, "采购扭力扳手套装", "待审批", None, "", "2026-10-10 09:00:00"),
    ("收购高价审批", "宝马3系高价收购178000元", 7, 178000, "车况精品，超阈值需审批", "已通过", 2, "同意", "2026-09-18 16:00:00"),
    ("销售优惠审批", "速腾优惠5000元", 5, 5000, "库存超60天", "已驳回", 2, "优惠幅度过大", "2026-10-08 14:00:00"),
]

# (user_id, action, target, detail, created_at)
OPERATION_LOGS = [
    (6, "收车入库", "车辆#1", "丰田卡罗拉，收购价85000", "2026-10-11 10:00:00"),
    (6, "检测通过", "车辆#3", "评分85", "2026-10-09 10:00:00"),
    (8, "整备项目登记", "车辆#5", "更换轮胎 1800元", "2026-10-08 10:00:00"),
    (9, "提交入库审核", "车辆#6", "", "2026-10-09 16:00:00"),
    (4, "创建销售订单", "车辆#10", "成交价188000", "2026-10-02 15:00:00"),
    (4, "交付完成", "车辆#10", "已售出", "2026-10-03 10:00:00"),
]


def _target_db_allowed() -> bool:
    """删除前校验：目标库路径必须来自 CAR_MGMT_DB_PATH 或默认 data/car_mgmt.db。"""
    env_path = os.environ.get("CAR_MGMT_DB_PATH")
    allowed = {os.path.abspath(db.DEFAULT_DB_PATH)}
    if env_path:
        allowed.add(os.path.abspath(env_path))
    return os.path.abspath(db.DB_PATH) in allowed


def load_demo_data() -> bool:
    """建表（幂等）并在 users 表为空时插入演示数据；不删库。

    返回 True 表示本次插入了演示数据；库中已有用户时跳过并返回 False。
    """
    db.init_db()
    existing = db.query_one("SELECT COUNT(*) AS c FROM users")["c"]
    if existing:
        print(f"库中已有 {existing} 个用户，跳过演示数据插入：{db.DB_PATH}")
        return False

    for dept_id, name in DEPARTMENTS:
        db.execute("INSERT INTO departments (id, name) VALUES (?, ?)", (dept_id, name))

    pw_hash = generate_password_hash(DEFAULT_PASSWORD)
    for username, name, role, dept_id, phone in USERS:
        db.execute(
            "INSERT INTO users (username, password_hash, name, role, dept_id, phone) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (username, pw_hash, name, role, dept_id, phone),
        )

    for v in VEHICLES:
        db.execute(
            "INSERT INTO vehicles (vin, brand, model, year, mileage, color, plate, "
            "buy_price, status, buyer_id, sales_id, customer_name, customer_phone, "
            "note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            v,
        )

    for insp in INSPECTIONS:
        db.execute(
            "INSERT INTO inspections (vehicle_id, inspector, score, issues, result, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            insp,
        )

    for vehicle_id, assignee_id, status, created_at, items in REWORK_DATA:
        order_id = db.execute(
            "INSERT INTO rework_orders (vehicle_id, assignee_id, status, created_at) "
            "VALUES (?, ?, ?, ?)",
            (vehicle_id, assignee_id, status, created_at),
        )
        for item, cost, supplier, pay_method, item_created_at in items:
            db.execute(
                "INSERT INTO rework_items (order_id, item, cost, supplier, pay_method, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (order_id, item, cost, supplier, pay_method, item_created_at),
            )
            db.execute(
                "INSERT INTO cost_ledger (vehicle_id, category, amount, note, created_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (vehicle_id, "整备费用", cost, item, assignee_id, item_created_at),
            )

    for s in SALES_ORDERS:
        db.execute(
            "INSERT INTO sales_orders (vehicle_id, sales_id, customer, phone, price, "
            "discount, finance_plan, transfer_status, delivery_status, contract_no, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            s,
        )

    for a in APPROVALS:
        db.execute(
            "INSERT INTO approvals (type, title, applicant_id, amount, reason, status, "
            "approver_id, comment, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            a,
        )

    for log in OPERATION_LOGS:
        db.execute(
            "INSERT INTO operation_logs (user_id, action, target, detail, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            log,
        )

    print("\n== 各表行数 ==")
    for table in TABLES:
        count = db.query_one(f"SELECT COUNT(*) AS c FROM {table}")["c"]
        print(f"  {table:15s} {count}")
    print("\n== 各表行数 ==")
    for table in TABLES:
        count = db.query_one(f"SELECT COUNT(*) AS c FROM {table}")["c"]
        print(f"  {table:15s} {count}")
    print("\n== 默认账号（密码均为 123456） ==")
    for username, name, role, _, _ in USERS:
        print(f"  {username:10s} {name} ({role})")
    return True


def _refuse(reason: str) -> int:
    print(f"拒绝执行：{reason}", file=sys.stderr)
    return 1


def main(argv: list[str]) -> int:
    app_env = os.environ.get("APP_ENV", "development")
    if app_env == "production":
        return _refuse(
            "APP_ENV=production 时禁止运行 seed（生产库永不灌演示数据/删库）。"
        )
    if "--confirm-reset" in argv:
        # 破坏性重置：删库重建，仅允许开发/演示/测试环境
        if app_env not in ALLOWED_RESET_ENVS:
            return _refuse(
                f"删除重建仅允许 APP_ENV={sorted(ALLOWED_RESET_ENVS)}，"
                f"当前为 {app_env}。"
            )
        if not _target_db_allowed():
            return _refuse(
                f"目标库路径 {db.DB_PATH} 不在允许范围 "
                f"(CAR_MGMT_DB_PATH 或默认 data/car_mgmt.db)，拒绝删除。"
            )
        if os.path.exists(db.DB_PATH):
            os.remove(db.DB_PATH)
            print(f"已删除旧库：{db.DB_PATH}")
        db.init_db()
        print(f"已重建空库：{db.DB_PATH}")
        load_demo_data()
        return 0
    # 默认：安全模式——建表（幂等）+ 库为空时灌演示数据，不删库
    load_demo_data()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
