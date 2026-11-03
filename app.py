#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
二手车业务管理系统 MVP
Flask + SQLite(sqlite3 标准库) + Jinja2 + 原生 CSS/JS，无构建步骤。
运行：python app.py  （默认 http://127.0.0.1:5001）
"""
import csv
import io
import os
import secrets
import sys
import uuid
from datetime import date
from functools import wraps

from flask import (Flask, Response, abort, flash, g, redirect, render_template,
                   request, send_file, session)
from werkzeug.security import check_password_hash, generate_password_hash

# 车辆附件上传：演示环境落本地 static/uploads/，正式环境迁对象存储（OSS）。
# 演示站磁盘是临时的，附件随重启丢失；正式版上线前接入 OSS（见任务书 T15）。
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "uploads")
ALLOWED_IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "webp"}
ALLOWED_VIDEO_EXTS = {"mp4", "mov", "avi", "webm", "m4v"}

import db
import pricing

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 附件单次请求上限 32MB

# 运行环境：development / demo / testing / production，默认 development
APP_ENV = os.environ.get("APP_ENV", "development")

# 生产环境必须显式配置 SECRET_KEY，否则拒绝启动（不输出任何密钥内容）
_secret_key = os.environ.get("SECRET_KEY")
if APP_ENV == "production" and not _secret_key:
    print(
        "错误：APP_ENV=production 时必须设置 SECRET_KEY 环境变量，"
        "应用拒绝启动。请配置强随机密钥后重试。",
        file=sys.stderr,
    )
    raise SystemExit(1)
app.secret_key = _secret_key or "car-mgmt-mvp-secret-change-me"

# 注意：import 本模块时不建表。本地开发用 `python app.py`（__main__ 里显式建表）；
# 部署时由 scripts/init_db.py 等显式命令建表，避免多 worker 并发改结构。

# ---------------------------------------------------------------- 常量
ROLE_NAMES = {
    "admin": "超级管理员",
    "boss": "老板",
    "finance": "财务",
    "sales": "销售",
    "buyer": "收购",
    "rework": "整备",
}
STATUSES = ["待检测", "待整备", "整备中", "待入库审核", "在售", "已售出", "已退回"]
APPROVAL_TYPES = ["收购高价审批", "销售优惠审批", "报销审批"]
# (key, 显示名, url, 可见角色)
MENUS = [
    ("dashboard", "工作台", "/", ["admin", "boss", "finance", "sales", "buyer", "rework"]),
    ("vehicles", "车辆", "/vehicles", ["admin", "boss", "finance", "sales", "buyer"]),
    ("acquisition", "收购", "/acquisition", ["admin", "boss", "buyer"]),
    ("rework", "整备", "/rework", ["admin", "boss", "rework"]),
    ("sales", "销售", "/sales", ["admin", "boss", "sales"]),
    ("finance", "财务", "/finance", ["admin", "boss", "finance"]),
    ("ledger", "库存总账", "/ledger", ["admin", "boss", "finance", "buyer"]),
    ("approvals", "审批", "/approvals", ["admin", "boss", "finance", "sales", "buyer", "rework"]),
    ("reports", "报表", "/reports", ["admin", "boss", "finance"]),
    ("settings", "系统设置", "/settings", ["admin"]),
]

# ---------------------------------------------------------------- 辅助
def get_setting(key, default=""):
    r = db.query_one("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r else default


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    u = db.query_one(
        """SELECT u.*, d.name AS dept_name FROM users u
           LEFT JOIN departments d ON d.id = u.dept_id
           WHERE u.id = ? AND u.active = 1""",
        (uid,),
    )
    if not u:
        return None
    u["role_name"] = ROLE_NAMES.get(u["role"], u["role"])
    return u


def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if "user_id" not in session:
            return redirect("/login")
        return f(*a, **kw)

    return wrapper


def role_required(*roles):
    def deco(f):
        @wraps(f)
        def wrapper(*a, **kw):
            u = current_user()
            if not u or u["role"] not in roles:
                abort(403)
            return f(*a, **kw)

        return wrapper

    return deco


def _form_text(name):
    """表单文本字段：去空格，空则返回 ''（入库存空串，与既有列保持一致）。"""
    return request.form.get(name, "").strip()


def _form_float(name):
    """表单数字字段：空或非法返回 None。"""
    raw = request.form.get(name, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _form_int(name):
    value = _form_float(name)
    return int(value) if value is not None else None


def _form_wan(name):
    """表单“万元”字段：按万元录入，入库换算为元；空返回 None。"""
    value = _form_float(name)
    return value * 10000 if value is not None else None


def _num(value, default=0.0):
    """数字清洗：None、空串、文本/非法值一律转为 default，永不抛错。

    读库展示层统一用它：库里只要有一条脏数据（文本价格、空金额），
    未经清洗的算术/格式化会打崩整页（2026-11-03 线上 500 根因）。
    """
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _num_or_none(value):
    """可选金额清洗：能转数字则转 float，否则 None（模板显示 '-'）。"""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value):
    """可选整数字段清洗：非法值返回 None，不抛错。"""
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _setting_float(key, default):
    """系统参数取浮点：参数值被改成非法文本时回退默认值，不打崩页面。"""
    return _num(get_setting(key, str(default)), default)


def _save_vehicle_files(vid):
    """保存收车单上传的附件（车辆照片/证件资料/其他照片/收购车况视频）。

    文件名用 uuid 重命名后落盘，原始文件名只存库里做展示；类型白名单校验，
    不在白名单的文件直接跳过。失败不阻断建单（附件缺失可在详情页看到无文件）。
    """
    specs = [
        ("photos_vehicle", "vehicle", ALLOWED_IMAGE_EXTS),
        ("photos_cert", "cert", ALLOWED_IMAGE_EXTS),
        ("photos_other", "other", ALLOWED_IMAGE_EXTS),
        ("video_condition", "video", ALLOWED_VIDEO_EXTS),
    ]
    for field, kind, allowed in specs:
        for f in request.files.getlist(field):
            if not f or not f.filename:
                continue
            ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
            if ext not in allowed:
                continue
            subdir = os.path.join(UPLOAD_DIR, str(vid))
            os.makedirs(subdir, exist_ok=True)
            fname = f"{uuid.uuid4().hex}.{ext}"
            f.save(os.path.join(subdir, fname))
            db.execute(
                "INSERT INTO vehicle_files(vehicle_id, kind, path, original_name)"
                " VALUES(?, ?, ?, ?)",
                (vid, kind, f"uploads/{vid}/{fname}", f.filename[:100]),
            )


def log(action, target="", detail=""):
    u = g.get("user")
    db.log_action(u["id"] if u else None, action, target, detail)


def pending_count(u):
    if u["role"] in ("admin", "boss", "finance"):
        r = db.query_one("SELECT COUNT(*) c FROM approvals WHERE status='待审批'")
    else:
        r = db.query_one(
            "SELECT COUNT(*) c FROM approvals WHERE status='待审批' AND applicant_id=?",
            (u["id"],),
        )
    return r["c"]


VEHICLE_COLS = """v.*, u1.name AS buyer_name, u2.name AS sales_name,
  COALESCE((SELECT SUM(amount) FROM cost_ledger WHERE vehicle_id=v.id AND category='整备费用'),0) AS rework_cost,
  COALESCE((SELECT SUM(amount) FROM cost_ledger WHERE vehicle_id=v.id AND category='其他'),0) AS other_cost,
  COALESCE((SELECT SUM(amount) FROM cost_ledger WHERE vehicle_id=v.id AND phase='入库前'),0) AS pre_cost,
  COALESCE((SELECT SUM(amount) FROM cost_ledger WHERE vehicle_id=v.id AND phase='入库后'),0) AS post_cost,
  COALESCE((SELECT SUM(amount) FROM cost_ledger WHERE vehicle_id=v.id AND phase IS NULL),0) AS unphased_cost,
  COALESCE((SELECT SUM(amount) FROM cost_ledger WHERE vehicle_id=v.id),0) AS cost_total"""
VEHICLE_FROM = ("FROM vehicles v LEFT JOIN users u1 ON u1.id=v.buyer_id "
                "LEFT JOIN users u2 ON u2.id=v.sales_id")


# 车辆金额/计数字段：读库时统一清洗，脏数据只显示为 0/空，永不打崩页面。
_VEHICLE_MONEY_FIELDS = (
    "buy_price", "new_car_price", "purchase_tax", "addon_cost",
    "online_price", "installment_price", "showroom_price", "floor_price",
    "manager_price", "wholesale_price", "alliance_price", "weidian_deposit",
    "paid_amount", "rework_plan_cost", "insurance_commercial_amount",
    "pre_cost", "post_cost", "unphased_cost", "cost_total",
    "rework_cost", "other_cost",
)
_VEHICLE_INT_FIELDS = (
    "transfer_count", "key_count", "stock_age_warn_days",
    "booking_warn_days", "weidian_warn_days", "listing_warn_days",
)


def vehicle_rows(where="", args=(), order="ORDER BY v.id DESC"):
    rows = db.query(f"SELECT {VEHICLE_COLS} {VEHICLE_FROM} {where} {order}", args)
    for r in rows:
        for k in _VEHICLE_MONEY_FIELDS:
            if k in r:
                r[k] = _num(r[k])
        for k in _VEHICLE_INT_FIELDS:
            if k in r:
                r[k] = _int_or_none(r[k])
        # 总成本 = 收购价 + 全部费用台账（含新类目；旧口径仅含整备/其他两类）
        r["total_cost"] = r["buy_price"] + r["cost_total"]
        attach_pricing(r)
    return rows


def attach_pricing(r):
    """给车辆行附加定价链（pricing.compute_pricing）与库存时间。

    入库时间口径：采购日期 purchase_date，缺失时取建单日期（见 pricing.stock_in_date）。
    """
    r["stock_in_date"] = pricing.stock_in_date(r)
    r["days_in_stock"] = pricing.stock_days(r["stock_in_date"])
    r["pricing"] = pricing.compute_pricing(
        r["buy_price"], r["pre_cost"], r["post_cost"], r["days_in_stock"]
    )
    return r


def latest_sale(vid):
    """该车最新一笔销售订单（含返点列），无则返回 None。"""
    return db.query_one(
        "SELECT * FROM sales_orders WHERE vehicle_id=? ORDER BY id DESC", (vid,)
    )


def csv_response(filename, headers, rows):
    buf = io.StringIO()
    buf.write("\ufeff")  # Excel 打开中文不乱码
    w = csv.writer(buf)
    w.writerow(headers)
    w.writerows(rows)
    return Response(
        buf.getvalue().encode("utf-8"),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.before_request
def ensure_csrf_token():
    # 每个会话（登录/未登录均可）持有 CSRF token；GET /login 时也会生成，
    # 保证登录 POST 可以校验
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)


@app.before_request
def check_csrf():
    # 所有 POST 请求必须携带与会话一致的 CSRF token（表单字段或请求头）
    if request.method == "POST":
        submitted = request.form.get("csrf_token", "") or request.headers.get(
            "X-CSRF-Token", ""
        )
        if not submitted or submitted != session.get("csrf_token"):
            abort(400, description="CSRF 校验失败：缺少或无效的 csrf_token")


@app.before_request
def load_user():
    g.user = current_user()
    if "user_id" in session and g.user is None:
        # 孤儿会话：浏览器还留着旧库的登录态，新库里已无此账号
        # （演示库清空/重建后常见）。不清掉的话各页面取 g.user 字段直接 500，
        # 清掉后 login_required 会把人带回登录页重新登录。
        session.clear()
        g.user = None
    if g.user:
        g.menus = [
            {"key": k, "name": n, "url": u}
            for k, n, u, roles in MENUS
            if g.user["role"] in roles
        ]
        g.pending = pending_count(g.user)
    else:
        g.menus = []
        g.pending = 0


@app.context_processor
def inject_common():
    return {
        "user": getattr(g, "user", None),
        "menus": getattr(g, "menus", []),
        "pending_count": getattr(g, "pending", 0),
        "csrf_token": session.get("csrf_token", ""),
    }


@app.errorhandler(403)
def err_403(e):
    return ('<h1>403 无权限</h1><p>当前角色无权访问此功能。</p>'
            '<a href="/">返回工作台</a>'), 403


@app.errorhandler(404)
def err_404(e):
    return ('<h1>404 不存在</h1><p>请求的页面或记录不存在。</p>'
            '<a href="/">返回工作台</a>'), 404

# ---------------------------------------------------------------- 登录/退出
@app.route("/favicon.ico")
def favicon():
    # 浏览器默认请求根路径 /favicon.ico；以 SVG 图标响应（现代浏览器均支持）
    return send_file("static/favicon.svg", mimetype="image/svg+xml")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = db.query_one(
            "SELECT * FROM users WHERE username=? AND active=1",
            (request.form.get("username", "").strip(),),
        )
        if u and check_password_hash(u["password_hash"], request.form.get("password", "")):
            # 登录成功先清理旧会话（防会话固定），再写入新身份与新 CSRF token
            session.clear()
            session["user_id"] = u["id"]
            session["csrf_token"] = secrets.token_hex(32)
            g.user = current_user()
            log("登录", "账号", f"{u['name']} 登录系统")
            return redirect("/")
        return render_template("login.html", error="用户名或密码错误，或账号已停用")
    return render_template("login.html", error=None)


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    log("退出登录", "账号", "")
    session.clear()
    return redirect("/login")

# ---------------------------------------------------------------- 工作台
@app.route("/")
@login_required
def dashboard():
    u = g.user
    by_status = {s: 0 for s in STATUSES}
    for r in db.query("SELECT status, COUNT(*) c FROM vehicles GROUP BY status"):
        by_status[r["status"]] = r["c"]
    stats = {
        "total": sum(by_status.values()),
        "by_status": by_status,
        "sellable": by_status.get("在售", 0),
    }
    # F11：模板直接遍历该列表，避免把字典键当对象导致显示方法对象
    status_stats = [{"status": s, "count": by_status[s]} for s in STATUSES]
    capital = db.query_one(
        "SELECT COALESCE(SUM(buy_price),0) s FROM vehicles "
        "WHERE status IN ('待检测','待整备','整备中','待入库审核','在售')"
    )["s"]
    bought30 = db.query_one(
        "SELECT COUNT(*) c FROM vehicles WHERE created_at >= date('now','localtime','-30 days')"
    )["c"]
    sold30 = db.query_one(
        "SELECT COUNT(*) c FROM sales_orders WHERE delivery_status='已交付' "
        "AND created_at >= date('now','localtime','-30 days')"
    )["c"]

    profit_rows = None
    if u["role"] in ("admin", "boss", "finance"):
        income, cost = 0, 0
        for v in vehicle_rows("WHERE v.status='已售出'"):
            so = db.query_one(
                "SELECT price FROM sales_orders WHERE vehicle_id=? ORDER BY id DESC",
                (v["id"],),
            )
            if so:
                income += _num(so["price"])
            cost += v["total_cost"]
        buy_total = db.query_one("SELECT COALESCE(SUM(buy_price),0) s FROM vehicles")["s"]
        profit_rows = [
            {"dept": "销售部", "income": income, "cost": cost, "profit": income - cost},
            {"dept": "收购部", "income": 0, "cost": buy_total, "profit": -buy_total},
        ]

    my_orders = None
    if u["role"] == "sales":
        my_orders = db.query(
            """SELECT s.id, v.brand, v.model, v.plate, s.customer, s.price, s.created_at
               FROM sales_orders s JOIN vehicles v ON v.id=s.vehicle_id
               WHERE s.sales_id=? ORDER BY s.id DESC LIMIT 10""",
            (u["id"],),
        )
        for o in my_orders:
            o["price"] = _num(o["price"])

    my_reworks = None
    if u["role"] == "rework":
        # F15：携带 vehicle_id，模板用它构造车辆详情链接（此前误用工单 id）
        my_reworks = db.query(
            """SELECT o.id, o.vehicle_id, v.brand, v.model, v.plate, o.status
               FROM rework_orders o JOIN vehicles v ON v.id=o.vehicle_id
               WHERE o.assignee_id=? AND o.status != '已完成'
               ORDER BY o.id DESC""",
            (u["id"],),
        )

    recent_vehicles = db.query(
        """SELECT v.id, v.brand, v.model, v.plate, v.status, v.buy_price, v.created_at,
                  u1.name AS buyer_name
           FROM vehicles v LEFT JOIN users u1 ON u1.id=v.buyer_id
           ORDER BY v.id DESC LIMIT 8"""
    )
    for r in recent_vehicles:
        r["buy_price"] = _num(r["buy_price"])
    return render_template(
        "dashboard.html", active="dashboard", stats=stats, status_stats=status_stats,
        capital=capital, bought30=bought30, sold30=sold30, profit_rows=profit_rows,
        my_orders=my_orders, my_reworks=my_reworks, recent_vehicles=recent_vehicles,
    )

# ---------------------------------------------------------------- 车辆
@app.route("/vehicles")
@login_required
@role_required("admin", "boss", "finance", "sales", "buyer")
def vehicles():
    f_status = request.args.get("status", "")
    f_brand = request.args.get("brand", "")
    q = request.args.get("q", "").strip()
    where, args = [], []
    if g.user["role"] == "sales":
        f_status = "在售"  # 销售只看在售车辆
    if f_status:
        where.append("v.status=?")
        args.append(f_status)
    if f_brand:
        where.append("v.brand=?")
        args.append(f_brand)
    if q:
        where.append("(v.brand LIKE ? OR v.model LIKE ? OR v.plate LIKE ? "
                     "OR v.vin LIKE ? OR v.customer_name LIKE ?)")
        args += [f"%{q}%"] * 5
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    rows = vehicle_rows(clause, args)
    brands = [r["brand"] for r in db.query("SELECT DISTINCT brand FROM vehicles ORDER BY brand")]
    return render_template(
        "vehicles.html", active="vehicles", vehicles=rows, statuses=STATUSES,
        f_status=f_status, brands=brands, f_brand=f_brand, q=q,
    )

@app.route("/vehicles/<int:vid>")
@login_required
@role_required("admin", "boss", "finance", "sales", "buyer", "rework")
def vehicle_detail(vid):
    u = g.user
    # F15：整备人员仅可查看分配给自己的工单对应的车辆（对象范围校验）
    restricted = (u["role"] == "rework")
    if restricted:
        mine = db.query_one(
            "SELECT id FROM rework_orders WHERE vehicle_id=? AND assignee_id=?",
            (vid, u["id"]),
        )
        if not mine:
            abort(403)
    rows = vehicle_rows("WHERE v.id=?", (vid,))
    if not rows:
        abort(404)
    v = rows[0]
    # 附件：受限角色（整备）仅可见车辆照片，证件资料与收购车况视频不传给模板
    if restricted:
        files = db.query(
            "SELECT * FROM vehicle_files WHERE vehicle_id=? AND kind='vehicle'"
            " ORDER BY id",
            (vid,),
        )
    else:
        files = db.query(
            "SELECT * FROM vehicle_files WHERE vehicle_id=? ORDER BY id", (vid,)
        )
    inspections = db.query(
        "SELECT * FROM inspections WHERE vehicle_id=? ORDER BY id DESC", (vid,)
    )
    orders = db.query(
        """SELECT o.*, u.name AS assignee_name FROM rework_orders o
           LEFT JOIN users u ON u.id=o.assignee_id
           WHERE o.vehicle_id=? ORDER BY o.id""",
        (vid,),
    )
    for o in orders:
        o["items"] = db.query(
            "SELECT * FROM rework_items WHERE order_id=? ORDER BY id", (o["id"],)
        )
        for i in o["items"]:
            i["cost"] = _num(i["cost"])
        o["items_total"] = sum(i["cost"] for i in o["items"])
    if restricted:
        # 受限视图：后端不再查询/传递财务与隐私数据，模板也不渲染这些区块
        ledger, sales_orders, logs, timeline = [], [], [], []
        v["sold_price"] = None
        v["total_deal"] = None
        v["turnover_days"] = None
    else:
        so = latest_sale(vid)
        v["sold_price"] = _num_or_none(so["price"]) if so else None
        v["total_deal"] = pricing.total_deal_price(so)
        v["turnover_days"] = pricing.turnover_days(
            v["stock_in_date"],
            (so["created_at"] or "")[:10] or None if so else None,
        )
        ledger = db.query(
            """SELECT l.*, u.name AS created_by_name FROM cost_ledger l
               LEFT JOIN users u ON u.id=l.created_by
               WHERE l.vehicle_id=? ORDER BY l.id""",
            (vid,),
        )
        for l in ledger:
            l["amount"] = _num(l["amount"])
        sales_orders = db.query(
            """SELECT s.*, u.name AS sales_name FROM sales_orders s
               LEFT JOIN users u ON u.id=s.sales_id
               WHERE s.vehicle_id=? ORDER BY s.id DESC""",
            (vid,),
        )
        for s in sales_orders:
            for k in ("price", "discount", "loan_rebate", "gps_rebate",
                      "insurance_rebate", "warranty_profit", "other_income"):
                s[k] = _num(s[k])
        logs = db.query(
            """SELECT l.*, u.name AS name FROM operation_logs l
               LEFT JOIN users u ON u.id=l.user_id
               WHERE l.target=? ORDER BY l.created_at, l.id""",
            (f"车辆#{vid}",),
        )
        timeline = [{
            "time": v["created_at"],
            "text": f"收车入库：{v['brand']}{v['model']}（{v['buyer_name'] or ''}），"
                    f"收购价 {v['buy_price'] or 0:.0f} 元",
        }]
        for l in logs:
            timeline.append({
                "time": l["created_at"],
                "text": f"{l['action']}：{l['detail']}" if l["detail"] else l["action"],
            })
        timeline.sort(key=lambda x: x["time"])
    threshold = _setting_float("buy_high_price_threshold", 150000)
    return render_template(
        "vehicle_detail.html", active="vehicles", vehicle=v, inspections=inspections,
        rework_orders=orders, ledger=ledger, sales_orders=sales_orders,
        logs=logs, timeline=timeline, threshold=threshold, restricted=restricted,
        files=files,
        cost_phases=pricing.COST_PHASES,
        pre_cost_categories=pricing.PRE_COST_CATEGORIES,
        post_cost_categories=pricing.POST_COST_CATEGORIES,
        can_add_cost=(u["role"] in ("admin", "boss", "finance", "buyer")),
    )


@app.route("/vehicles/<int:vid>/costs", methods=["POST"])
@login_required
@role_required("admin", "boss", "finance", "buyer")
def vehicle_add_cost(vid):
    """费用分摊登记：入库前/入库后 + 表格类目 + 金额（只增不改，留痕）。

    费用明细表口径：按车归集，入库前费用进 AB、入库后费用进 AC，参与定价链。
    """
    v = db.query_one("SELECT id FROM vehicles WHERE id=?", (vid,))
    if not v:
        abort(404)
    phase = request.form.get("phase", "").strip()
    category = request.form.get("category", "").strip()
    amount = _form_float("amount") or 0
    note = request.form.get("note", "").strip()
    allowed = (
        pricing.PRE_COST_CATEGORIES if phase == "入库前"
        else pricing.POST_COST_CATEGORIES if phase == "入库后" else []
    )
    if phase not in pricing.COST_PHASES or category not in allowed or amount <= 0:
        flash("费用归属、类别与金额填写不正确", "error")
        return redirect(f"/vehicles/{vid}")
    db.execute(
        "INSERT INTO cost_ledger(vehicle_id,phase,category,amount,note,created_by)"
        " VALUES(?,?,?,?,?,?)",
        (vid, phase, category, amount, note, g.user["id"]),
    )
    log("费用登记", f"车辆#{vid}", f"{phase}/{category} {amount:.0f}元")
    flash("费用已登记，计入车辆成本", "success")
    return redirect(f"/vehicles/{vid}")


@app.route("/ledger")
@login_required
@role_required("admin", "boss", "finance", "buyer")
def ledger():
    """库存总账：客户管理表单「库存表」财务段的系统版。

    每车一行：打款价、入库前/后费用、车辆成本、定价链四种标价、
    成交价（合同价 + 返点）、收购/销售归属、库存与周转天数。
    """
    f_status = request.args.get("status", "")
    f_brand = request.args.get("brand", "")
    q = request.args.get("q", "").strip()
    where, args = [], []
    if f_status:
        where.append("v.status=?")
        args.append(f_status)
    if f_brand:
        where.append("v.brand=?")
        args.append(f_brand)
    if q:
        where.append("(v.brand LIKE ? OR v.model LIKE ? OR v.plate LIKE ? "
                     "OR v.vin LIKE ? OR v.customer_name LIKE ?)")
        args += [f"%{q}%"] * 5
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    rows = vehicle_rows(clause, args)
    for r in rows:
        sale = latest_sale(r["id"])
        r["sale_price"] = _num_or_none(sale["price"]) if sale else None
        r["total_deal"] = pricing.total_deal_price(sale)
        r["sale_channel"] = sale["sale_channel"] if sale else None
        r["sold_on"] = (sale["created_at"] or "")[:10] or None if sale else None
        r["turnover_days"] = pricing.turnover_days(r["stock_in_date"], r["sold_on"])
        r["vin6"] = pricing.vin_last6(r["vin"])
    brands = [r["brand"] for r in db.query("SELECT DISTINCT brand FROM vehicles ORDER BY brand")]
    totals = {
        k: sum((r["pricing"][k] or 0) for r in rows)
        for k in ("buy_price", "pre_cost", "post_cost", "cost",
                  "profit_value", "wholesale_price", "floor_price",
                  "showroom_price", "online_price")
    }
    totals["deal"] = sum((r["total_deal"] or 0) for r in rows)
    return render_template(
        "ledger.html", active="ledger", rows=rows, totals=totals,
        statuses=STATUSES, f_status=f_status, brands=brands,
        f_brand=f_brand, q=q,
    )


@app.route("/vehicles/<int:vid>/inspect", methods=["POST"])
@login_required
@role_required("admin", "boss", "buyer")
def inspect_vehicle(vid):
    v = db.query_one("SELECT * FROM vehicles WHERE id=?", (vid,))
    if not v:
        abort(404)
    if v["status"] != "待检测":
        flash("该车辆当前状态不是待检测，无法重复检测", "error")
        return redirect(f"/vehicles/{vid}")
    result = request.form.get("result", "")
    score = request.form.get("score", "").strip()
    db.execute(
        """INSERT INTO inspections(vehicle_id, inspector, score, issues, photos_note, result)
           VALUES(?,?,?,?,?,?)""",
        (vid, request.form.get("inspector", "").strip() or g.user["name"],
         int(score) if score.isdigit() else None,
         request.form.get("issues", "").strip(),
         request.form.get("photos_note", "").strip(), result),
    )
    target = f"车辆#{vid}"
    if result == "通过":
        db.execute("UPDATE vehicles SET status='待整备' WHERE id=?", (vid,))
        db.execute("INSERT INTO rework_orders(vehicle_id, status) VALUES(?,'待处理')", (vid,))
        log("检测通过", target, f"评分{score}；已自动生成整备工单")
        flash("检测通过，已自动生成整备工单", "success")
    else:
        db.execute("UPDATE vehicles SET status='已退回' WHERE id=?", (vid,))
        log("检测退回", target, request.form.get("issues", "").strip())
        flash("已记录为检测退回", "success")
    return redirect(f"/vehicles/{vid}")


@app.route("/vehicles/<int:vid>/stock_approve", methods=["POST"])
@login_required
@role_required("admin", "boss")
def stock_approve(vid):
    v = db.query_one("SELECT * FROM vehicles WHERE id=?", (vid,))
    if not v:
        abort(404)
    if v["status"] != "待入库审核":
        flash("该车辆当前不在待入库审核状态", "error")
        return redirect(f"/vehicles/{vid}")
    db.execute("UPDATE vehicles SET status='在售' WHERE id=?", (vid,))
    log("入库审核通过", f"车辆#{vid}", "车辆已入库，变为在售")
    flash("质检通过，车辆已入库变为在售", "success")
    return redirect(f"/vehicles/{vid}")


@app.route("/vehicles/<int:vid>/stock_reject", methods=["POST"])
@login_required
@role_required("admin", "boss")
def stock_reject(vid):
    v = db.query_one("SELECT * FROM vehicles WHERE id=?", (vid,))
    if not v:
        abort(404)
    if v["status"] != "待入库审核":
        flash("该车辆当前不在待入库审核状态", "error")
        return redirect(f"/vehicles/{vid}")
    db.execute("UPDATE vehicles SET status='整备中' WHERE id=?", (vid,))
    o = db.query_one(
        "SELECT id FROM rework_orders WHERE vehicle_id=? ORDER BY id DESC", (vid,)
    )
    if o:
        db.execute("UPDATE rework_orders SET status='进行中' WHERE id=?", (o["id"],))
    log("退回整改", f"车辆#{vid}", "入库审核未通过，退回整备")
    flash("已退回整改，车辆回到整备中", "success")
    return redirect(f"/vehicles/{vid}")

# ---------------------------------------------------------------- 收购
@app.route("/acquisition", methods=["GET", "POST"])
@login_required
@role_required("admin", "boss", "buyer")
def acquisition():
    threshold = _setting_float("buy_high_price_threshold", 150000)
    if request.method == "POST":
        # 大风车 APP「新建车辆」7 大类栏目（2026-11-01 客户要求）。
        # 金额口径：库内统一为“元”；表单中标“万元”的字段按万元录入、此处换算。
        buy_price = _form_wan("buy_price_wan") or 0
        vals = {
            # 1 基础信息（既有列：vin/brand/model/mileage/color/plate）
            "vin": _form_text("vin"),
            "brand": _form_text("brand"),
            "model": _form_text("model"),
            "mileage": _form_float("mileage"),
            "color": _form_text("color"),
            "plate": _form_text("plate"),
            "vehicle_type": _form_text("vehicle_type"),
            "first_plate_date": _form_text("first_plate_date"),
            "sales_mode": _form_text("sales_mode"),
            "evaluator": _form_text("evaluator"),
            "store": _form_text("store"),
            "note": _form_text("note"),
            # 2 车辆档案
            "vehicle_no": _form_text("vehicle_no"),
            "stock_status": _form_text("stock_status"),
            "stock_area": _form_text("stock_area"),
            "stock_location": _form_text("stock_location"),
            "stock_desc": _form_text("stock_desc"),
            "weidian_status": _form_text("weidian_status"),
            "use_type": _form_text("use_type"),
            "ownership": _form_text("ownership"),
            "engine_no": _form_text("engine_no"),
            "manufacture_date": _form_text("manufacture_date"),
            "license_vehicle_type": _form_text("license_vehicle_type"),
            "license_model": _form_text("license_model"),
            "registration_no": _form_text("registration_no"),
            "transfer_dmv": _form_text("transfer_dmv"),
            "interior_color": _form_text("interior_color"),
            "new_car_price": _form_wan("new_car_price"),
            "purchase_tax": _form_wan("purchase_tax"),
            "addon_cost": _form_float("addon_cost"),
            "addon_content": _form_text("addon_content"),
            "location": _form_text("location"),
            "stock_age_warn_days": _form_int("stock_age_warn_days"),
            "booking_warn_days": _form_int("booking_warn_days"),
            "weidian_warn_days": _form_int("weidian_warn_days"),
            "listing_warn_days": _form_int("listing_warn_days"),
            # 3 车况详情
            "condition_summary": _form_text("condition_summary"),
            "condition_internal": _form_text("condition_internal"),
            # 4 价格信息（万元字段已换算为元）
            "online_price": _form_wan("online_price"),
            "installment_price": _form_wan("installment_price"),
            "showroom_price": _form_wan("showroom_price"),
            "floor_price": _form_wan("floor_price"),
            "manager_price": _form_wan("manager_price"),
            "wholesale_price": _form_wan("wholesale_price"),
            "show_wholesale": _form_text("show_wholesale"),
            "alliance_price": _form_wan("alliance_price"),
            "weidian_deposit": _form_float("weidian_deposit"),
            # 5 评估采购信息
            "buy_price": buy_price,
            "purchase_type": _form_text("purchase_type"),
            "purchase_source": _form_text("purchase_source"),
            "paid_amount": _form_float("paid_amount"),
            "payee_company": _form_text("payee_company"),
            "purchase_date": _form_text("purchase_date"),
            "transfer_method": _form_text("transfer_method"),
            "pickup_method": _form_text("pickup_method"),
            "beihu_name": _form_text("beihu_name"),
            "beihu_phone": _form_text("beihu_phone"),
            "beihu_idcard": _form_text("beihu_idcard"),
            "beihu_company": _form_text("beihu_company"),
            "rework_plan_start": _form_text("rework_plan_start"),
            "rework_plan_end": _form_text("rework_plan_end"),
            "rework_plan_items": _form_text("rework_plan_items"),
            "rework_plan_cost": _form_float("rework_plan_cost"),
            # 6 车主信息（姓名/电话复用既有 customer_* 列）
            "owner_type": _form_text("owner_type"),
            "owner_source": _form_text("owner_source"),
            "customer_name": _form_text("customer_name"),
            "customer_phone": _form_text("customer_phone"),
            "payee_name": _form_text("payee_name"),
            "bank_card": _form_text("bank_card"),
            "bank_name": _form_text("bank_name"),
            "owner_idcard": _form_text("owner_idcard"),
            "other_account": _form_text("other_account"),
            "owner_address": _form_text("owner_address"),
            # 7 牌证信息
            "cert_registration": _form_text("cert_registration"),
            "cert_driving": _form_text("cert_driving"),
            "cert_owner_idcard": _form_text("cert_owner_idcard"),
            "cert_transfer_video": _form_text("cert_transfer_video"),
            "cert_invoice": _form_text("cert_invoice"),
            "cert_conformity": _form_text("cert_conformity"),
            "cert_env": _form_text("cert_env"),
            "cert_qualification": _form_text("cert_qualification"),
            "cert_plate_photo": _form_text("cert_plate_photo"),
            "transfer_count": _form_int("transfer_count"),
            "key_count": _form_int("key_count"),
            "insurance_compulsory": _form_text("insurance_compulsory"),
            "insurance_compulsory_expiry": _form_text("insurance_compulsory_expiry"),
            "insurance_commercial": _form_text("insurance_commercial"),
            "insurance_commercial_expiry": _form_text("insurance_commercial_expiry"),
            "insurance_commercial_amount": _form_float("insurance_commercial_amount"),
            "annual_inspection_expiry": _form_text("annual_inspection_expiry"),
            "cert_note": _form_text("cert_note"),
            "status": "待检测",
            "buyer_id": g.user["id"],
        }
        brand, model = vals["brand"], vals["model"]
        if not brand or not model or not vals["mileage"] or not vals["color"] \
                or buy_price <= 0 or not vals["store"]:
            flash("品牌、车型、表显里程、车身颜色、采购价、门店为必填项", "error")
            return redirect("/acquisition")
        cols = ", ".join(vals.keys())
        placeholders = ", ".join("?" * len(vals))
        vid = db.execute(
            f"INSERT INTO vehicles({cols}) VALUES({placeholders})",
            tuple(vals.values()),
        )
        _save_vehicle_files(vid)
        log("收车入库", f"车辆#{vid}", f"{brand}{model}，收购价{buy_price:.0f}元")
        if buy_price > threshold:
            db.execute(
                """INSERT INTO approvals(type,title,applicant_id,amount,reason,status)
                   VALUES('收购高价审批',?,?,?,?, '待审批')""",
                (f"高价收车：{brand}{model} {buy_price:.0f}元", g.user["id"], buy_price,
                 request.form.get("note", "").strip()),
            )
            log("发起审批", "审批", f"收购高价审批：{brand}{model}")
            flash(f"收车单已创建；收购价超过阈值{threshold:.0f}元，已自动生成高价审批单", "success")
        else:
            flash("收车单已创建，车辆进入待检测", "success")
        return redirect(f"/vehicles/{vid}")
    recent = db.query(
        """SELECT v.id, v.brand, v.model, v.plate, v.buy_price, v.status, v.created_at,
                  u.name AS buyer_name
           FROM vehicles v LEFT JOIN users u ON u.id=v.buyer_id
           ORDER BY v.id DESC LIMIT 50"""
    )
    for r in recent:
        r["buy_price"] = _num(r["buy_price"])
    staff = db.query("SELECT id, name FROM users WHERE active=1 ORDER BY id")
    return render_template(
        "acquisition.html", active="acquisition", vehicles=recent, threshold=threshold,
        staff=staff, today=date.today().isoformat(),
    )

# ---------------------------------------------------------------- 整备
@app.route("/rework")
@login_required
@role_required("admin", "boss", "rework")
def rework():
    f_status = request.args.get("status", "")
    where, args = [], []
    if f_status:
        where.append("o.status=?")
        args.append(f_status)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    orders = db.query(
        f"""SELECT o.*, v.brand, v.model, v.plate, u.name AS assignee_name
            FROM rework_orders o JOIN vehicles v ON v.id=o.vehicle_id
            LEFT JOIN users u ON u.id=o.assignee_id
            {clause} ORDER BY o.id DESC""",
        args,
    )
    for o in orders:
        o["items"] = db.query(
            "SELECT * FROM rework_items WHERE order_id=? ORDER BY id", (o["id"],)
        )
        for i in o["items"]:
            i["cost"] = _num(i["cost"])
        o["items_total"] = sum(i["cost"] for i in o["items"])
    return render_template(
        "rework.html", active="rework", orders=orders,
        statuses=["待处理", "进行中", "已完成"], f_status=f_status,
    )


@app.route("/rework/<int:oid>/start", methods=["POST"])
@login_required
@role_required("admin", "boss", "rework")
def rework_start(oid):
    o = db.query_one("SELECT * FROM rework_orders WHERE id=?", (oid,))
    if not o:
        abort(404)
    if o["status"] == "待处理":
        db.execute(
            "UPDATE rework_orders SET status='进行中', assignee_id=COALESCE(assignee_id,?) WHERE id=?",
            (g.user["id"], oid),
        )
        db.execute("UPDATE vehicles SET status='整备中' WHERE id=?", (o["vehicle_id"],))
        log("开始整备", f"车辆#{o['vehicle_id']}", f"整备工单#{oid}")
        flash("已开始整备", "success")
    return redirect("/rework")


@app.route("/rework/<int:oid>/add_item", methods=["POST"])
@login_required
@role_required("admin", "boss", "rework")
def rework_add_item(oid):
    o = db.query_one("SELECT * FROM rework_orders WHERE id=?", (oid,))
    if not o:
        abort(404)
    if o["status"] == "已完成":
        flash("工单已完成，不能再登记项目", "error")
        return redirect("/rework")
    item = request.form.get("item", "").strip()
    cost = _form_float("cost") or 0
    if not item or cost <= 0:
        flash("项目名称和费用为必填项", "error")
        return redirect("/rework")
    supplier = request.form.get("supplier", "").strip()
    pay_method = request.form.get("pay_method", "").strip()
    db.execute(
        "INSERT INTO rework_items(order_id,item,cost,supplier,pay_method) VALUES(?,?,?,?,?)",
        (oid, item, cost, supplier, pay_method),
    )
    # 费用自动写入成本台账（只增不改）；整备发生在入库后，归入“入库后”费用
    db.execute(
        "INSERT INTO cost_ledger(vehicle_id,phase,category,amount,note,created_by)"
        " VALUES(?,'入库后','整备费用',?,?,?)",
        (o["vehicle_id"], cost, item, g.user["id"]),
    )
    if o["status"] == "待处理":
        db.execute("UPDATE rework_orders SET status='进行中' WHERE id=?", (oid,))
        db.execute("UPDATE vehicles SET status='整备中' WHERE id=?", (o["vehicle_id"],))
    log("整备项目登记", f"车辆#{o['vehicle_id']}", f"{item} {cost:.0f}元（{supplier}）")
    flash("项目已登记，费用已计入车辆成本", "success")
    return redirect("/rework")


@app.route("/rework/<int:oid>/finish", methods=["POST"])
@login_required
@role_required("admin", "boss", "rework")
def rework_finish(oid):
    o = db.query_one("SELECT * FROM rework_orders WHERE id=?", (oid,))
    if not o:
        abort(404)
    if o["status"] != "进行中":
        flash("只有进行中的工单可以提交入库审核", "error")
        return redirect("/rework")
    db.execute("UPDATE rework_orders SET status='已完成' WHERE id=?", (oid,))
    db.execute("UPDATE vehicles SET status='待入库审核' WHERE id=?", (o["vehicle_id"],))
    log("提交入库审核", f"车辆#{o['vehicle_id']}", f"整备工单#{oid}完成")
    flash("已提交入库审核，等待质检", "success")
    return redirect("/rework")

# ---------------------------------------------------------------- 销售
@app.route("/sales", methods=["GET", "POST"])
@login_required
@role_required("admin", "boss", "sales")
def sales():
    if request.method == "POST":
        vid = request.form.get("vehicle_id", "")
        v = db.query_one("SELECT * FROM vehicles WHERE id=?", (vid,))
        if not v:
            flash("请选择车辆", "error")
            return redirect("/sales")
        if v["status"] != "在售":
            flash("该车辆当前不在售，不能创建销售订单", "error")
            return redirect("/sales")
        customer = request.form.get("customer", "").strip()
        price = _form_float("price") or 0
        if not customer or price <= 0:
            flash("客户姓名和成交价为必填项", "error")
            return redirect("/sales")
        rebates = {
            k: _form_float(k) or 0
            for k in ("loan_rebate", "gps_rebate", "insurance_rebate",
                      "warranty_profit", "other_income")
        }
        oid = db.execute(
            """INSERT INTO sales_orders(vehicle_id,sales_id,customer,phone,price,discount,
                                        finance_plan,contract_no,loan_rebate,gps_rebate,
                                        insurance_rebate,warranty_profit,other_income,
                                        sale_channel)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (vid, g.user["id"], customer, request.form.get("phone", "").strip(),
             price, _form_float("discount") or 0,
             request.form.get("finance_plan", "").strip(),
             request.form.get("contract_no", "").strip(),
             rebates["loan_rebate"], rebates["gps_rebate"],
             rebates["insurance_rebate"], rebates["warranty_profit"],
             rebates["other_income"],
             request.form.get("sale_channel", "").strip()),
        )
        db.execute("UPDATE vehicles SET sales_id=? WHERE id=?", (g.user["id"], vid))
        log("创建销售订单", f"车辆#{vid}", f"客户{customer}，成交价{price:.0f}元")
        flash("销售订单已创建", "success")
        return redirect("/sales")
    where, args = "", []
    if g.user["role"] == "sales":
        where, args = "WHERE s.sales_id=?", [g.user["id"]]
    orders = db.query(
        f"""SELECT s.*, v.brand, v.model, v.plate, u.name AS sales_name
            FROM sales_orders s JOIN vehicles v ON v.id=s.vehicle_id
            LEFT JOIN users u ON u.id=s.sales_id
            {where} ORDER BY s.id DESC""",
        args,
    )
    for o in orders:
        for k in ("price", "discount", "loan_rebate", "gps_rebate",
                  "insurance_rebate", "warranty_profit", "other_income"):
            o[k] = _num(o[k])
    sellable = db.query(
        "SELECT id, brand, model, plate, buy_price FROM vehicles "
        "WHERE status='在售' ORDER BY id DESC"
    )
    for v in sellable:
        v["buy_price"] = _num(v["buy_price"])
    return render_template(
        "sales.html", active="sales", orders=orders, sellable=sellable,
    )


@app.route("/sales/<int:oid>/transfer", methods=["POST"])
@login_required
@role_required("admin", "boss", "sales")
def sales_transfer(oid):
    s = db.query_one("SELECT * FROM sales_orders WHERE id=?", (oid,))
    if not s:
        abort(404)
    st = request.form.get("transfer_status", "")
    if st in ("未办理", "办理中", "已完成"):
        db.execute("UPDATE sales_orders SET transfer_status=? WHERE id=?", (st, oid))
        log("过户状态更新", f"车辆#{s['vehicle_id']}", f"过户进度：{st}")
        flash("过户进度已更新", "success")
    return redirect("/sales")


@app.route("/sales/<int:oid>/deliver", methods=["POST"])
@login_required
@role_required("admin", "boss", "sales")
def sales_deliver(oid):
    s = db.query_one("SELECT * FROM sales_orders WHERE id=?", (oid,))
    if not s:
        abort(404)
    if s["delivery_status"] == "已交付":
        flash("该订单已交付", "error")
        return redirect("/sales")
    db.execute(
        "UPDATE sales_orders SET delivery_status='已交付' WHERE id=?", (oid,)
    )
    db.execute("UPDATE vehicles SET status='已售出' WHERE id=?", (s["vehicle_id"],))
    log("交付完成", f"车辆#{s['vehicle_id']}", f"客户{s['customer']}，车辆已售出并剔除库存")
    flash("交车完成，车辆已售出", "success")
    return redirect("/sales")

# ---------------------------------------------------------------- 财务
@app.route("/finance")
@login_required
@role_required("admin", "boss", "finance")
def finance():
    rows = vehicle_rows(order="ORDER BY v.id DESC")
    for v in rows:
        so = db.query_one(
            "SELECT price FROM sales_orders WHERE vehicle_id=? ORDER BY id DESC",
            (v["id"],),
        )
        v["sale_price"] = _num_or_none(so["price"]) if so else None
        v["profit"] = (v["sale_price"] - v["total_cost"]) if v["sale_price"] else None
    totals = {
        "buy": sum(v["buy_price"] or 0 for v in rows),
        "rework": sum(v["rework_cost"] or 0 for v in rows),
        "other": sum(v["other_cost"] or 0 for v in rows),
        "total": sum(v["total_cost"] for v in rows),
        "sale": sum(v["sale_price"] or 0 for v in rows),
        "profit": sum(v["profit"] or 0 for v in rows),
    }
    rate = _setting_float("sales_commission_rate", 0.05)
    comm = {}
    for v in rows:
        if v["sale_price"]:
            so = db.query_one(
                """SELECT s.sales_id, u.name FROM sales_orders s
                   LEFT JOIN users u ON u.id=s.sales_id
                   WHERE s.vehicle_id=? ORDER BY s.id DESC""",
                (v["id"],),
            )
            if so and so["sales_id"]:
                c = comm.setdefault(
                    so["sales_id"],
                    {"name": so["name"], "income": 0, "cost": 0},
                )
                c["income"] += v["sale_price"]
                c["cost"] += v["total_cost"]
    commission_rows = []
    for c in comm.values():
        c["profit"] = c["income"] - c["cost"]
        c["commission"] = c["profit"] * rate
        commission_rows.append(c)
    return render_template(
        "finance.html", active="finance", rows=rows, totals=totals,
        commission_rows=commission_rows, commission_rate=rate,
    )

# ---------------------------------------------------------------- 审批
@app.route("/approvals", methods=["GET", "POST"])
@login_required
def approvals():
    if request.method == "POST":
        atype = request.form.get("type", "")
        title = request.form.get("title", "").strip()
        amount = _form_float("amount") or 0
        if atype not in APPROVAL_TYPES or not title:
            flash("审批类型和标题为必填项", "error")
            return redirect("/approvals")
        db.execute(
            "INSERT INTO approvals(type,title,applicant_id,amount,reason,status) "
            "VALUES(?,?,?,?,?,'待审批')",
            (atype, title, g.user["id"], amount, request.form.get("reason", "").strip()),
        )
        log("发起审批", "审批", f"{atype}：{title}")
        flash("审批已发起", "success")
        return redirect("/approvals")
    f_status = request.args.get("status", "")
    where, args = [], []
    if f_status:
        where.append("a.status=?")
        args.append(f_status)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    rows = db.query(
        f"""SELECT a.*, u1.name AS applicant_name, u2.name AS approver_name
            FROM approvals a LEFT JOIN users u1 ON u1.id=a.applicant_id
            LEFT JOIN users u2 ON u2.id=a.approver_id
            {clause} ORDER BY a.id DESC""",
        args,
    )
    for r in rows:
        r["amount"] = _num(r["amount"])
    can_approve = g.user["role"] in ("admin", "boss", "finance")
    return render_template(
        "approvals.html", active="approvals", approvals=rows,
        types=APPROVAL_TYPES, can_approve=can_approve, f_status=f_status,
    )


def _decide_approval(aid, ok):
    a = db.query_one("SELECT * FROM approvals WHERE id=?", (aid,))
    if not a:
        abort(404)
    if g.user["role"] not in ("admin", "boss", "finance"):
        abort(403)
    if a["status"] != "待审批":
        flash("该审批已处理，不能重复操作", "error")
        return redirect("/approvals")
    if a["applicant_id"] == g.user["id"]:
        flash("不能审批自己发起的申请", "error")
        return redirect("/approvals")
    comment = request.form.get("comment", "").strip()
    db.execute(
        "UPDATE approvals SET status=?, approver_id=?, comment=? WHERE id=?",
        ("已通过" if ok else "已驳回", g.user["id"], comment, aid),
    )
    log("审批通过" if ok else "审批驳回", "审批", f"{a['type']}：{a['title']}")
    flash("已处理", "success")
    return redirect("/approvals")


@app.route("/approvals/<int:aid>/approve", methods=["POST"])
@login_required
def approval_approve(aid):
    return _decide_approval(aid, True)


@app.route("/approvals/<int:aid>/reject", methods=["POST"])
@login_required
def approval_reject(aid):
    return _decide_approval(aid, False)

# ---------------------------------------------------------------- 报表
@app.route("/reports")
@login_required
@role_required("admin", "boss", "finance")
def reports():
    total = db.query_one("SELECT COUNT(*) c FROM vehicles")["c"]
    sold = db.query_one("SELECT COUNT(*) c FROM vehicles WHERE status='已售出'")["c"]
    sellable = db.query_one("SELECT COUNT(*) c FROM vehicles WHERE status='在售'")["c"]
    capital = db.query_one(
        "SELECT COALESCE(SUM(buy_price),0) s FROM vehicles "
        "WHERE status IN ('待检测','待整备','整备中','待入库审核','在售')"
    )["s"]
    return render_template(
        "reports.html", active="reports",
        summary={"total": total, "sold": sold, "sellable": sellable, "capital": capital},
    )


@app.route("/reports/vehicles.csv")
@login_required
@role_required("admin", "boss", "finance")
def report_vehicles_csv():
    rows = vehicle_rows(order="ORDER BY v.id")
    data = [[
        v["id"], v["brand"], v["model"], v["year"], v["mileage"], v["color"],
        v["plate"], v["vin"], f"{v['buy_price'] or 0:.0f}",
        f"{v['rework_cost'] or 0:.0f}", f"{v['other_cost'] or 0:.0f}",
        f"{v['total_cost']:.0f}", v["status"], v["buyer_name"] or "",
        v["sales_name"] or "", v["created_at"],
    ] for v in rows]
    return csv_response(
        "vehicles.csv",
        ["ID", "品牌", "型号", "年款", "里程(万km)", "颜色", "车牌", "VIN",
         "收购价", "整备费用", "其他费用", "总成本", "状态", "收购员", "销售员", "入库时间"],
        data,
    )


@app.route("/reports/sales.csv")
@login_required
@role_required("admin", "boss", "finance")
def report_sales_csv():
    rows = db.query(
        """SELECT s.*, v.brand, v.model, v.plate, u.name AS sales_name
           FROM sales_orders s JOIN vehicles v ON v.id=s.vehicle_id
           LEFT JOIN users u ON u.id=s.sales_id ORDER BY s.id"""
    )
    for r in rows:
        r["price"] = _num(r["price"])
        r["discount"] = _num(r["discount"])
    data = [[
        r["id"], f"{r['brand']}{r['model']}", r["plate"], r["customer"], r["phone"],
        f"{r['price']:.0f}", f"{r['discount']:.0f}", r["finance_plan"],
        r["contract_no"], r["transfer_status"], r["delivery_status"],
        r["sales_name"] or "", r["created_at"],
    ] for r in rows]
    return csv_response(
        "sales.csv",
        ["订单ID", "车辆", "车牌", "客户", "电话", "成交价", "优惠", "金融方案",
         "合同号", "过户进度", "交付状态", "销售员", "创建时间"],
        data,
    )


@app.route("/reports/finance.csv")
@login_required
@role_required("admin", "boss", "finance")
def report_finance_csv():
    rows = vehicle_rows(order="ORDER BY v.id")
    data = []
    for v in rows:
        so = db.query_one(
            "SELECT price FROM sales_orders WHERE vehicle_id=? ORDER BY id DESC",
            (v["id"],),
        )
        sale = _num(so["price"]) if so else 0
        profit = (sale - v["total_cost"]) if so else 0
        data.append([
            v["id"], f"{v['brand']}{v['model']}", v["plate"],
            f"{v['buy_price'] or 0:.0f}", f"{v['rework_cost'] or 0:.0f}",
            f"{v['other_cost'] or 0:.0f}", f"{v['total_cost']:.0f}",
            f"{sale:.0f}" if so else "", f"{profit:.0f}" if so else "",
            v["status"],
        ])
    return csv_response(
        "finance.csv",
        ["ID", "车辆", "车牌", "收购价", "整备费用", "其他费用", "总成本",
         "成交价", "利润", "状态"],
        data,
    )

# ---------------------------------------------------------------- 系统设置
@app.route("/settings")
@login_required
@role_required("admin")
def settings():
    users = db.query(
        """SELECT u.*, d.name AS dept_name FROM users u
           LEFT JOIN departments d ON d.id=u.dept_id ORDER BY u.id"""
    )
    for u in users:
        u["role_name"] = ROLE_NAMES.get(u["role"], u["role"])
    depts = db.query("SELECT * FROM departments ORDER BY id")
    settings = {r["key"]: r["value"] for r in db.query("SELECT * FROM settings")}
    roles = [(k, v) for k, v in ROLE_NAMES.items()]
    return render_template(
        "settings.html", active="settings", users=users, roles=roles,
        depts=depts, settings=settings,
    )


@app.route("/settings/users/add", methods=["POST"])
@login_required
@role_required("admin")
def settings_user_add():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    name = request.form.get("name", "").strip()
    role = request.form.get("role", "")
    if not username or not password or not name or role not in ROLE_NAMES:
        flash("用户名、密码、姓名、角色为必填项", "error")
        return redirect("/settings")
    if db.query_one("SELECT id FROM users WHERE username=?", (username,)):
        flash("用户名已存在", "error")
        return redirect("/settings")
    dept = request.form.get("dept_id", "").strip() or None
    db.execute(
        "INSERT INTO users(username,password_hash,name,role,dept_id,phone) VALUES(?,?,?,?,?,?)",
        (username, generate_password_hash(password), name, role, dept,
         request.form.get("phone", "").strip()),
    )
    log("新增用户", "账号", f"{username}（{name}）")
    flash("用户已创建", "success")
    return redirect("/settings")


@app.route("/settings/users/<int:uid>/toggle", methods=["POST"])
@login_required
@role_required("admin")
def settings_user_toggle(uid):
    if uid == g.user["id"]:
        flash("不能停用自己的账号", "error")
        return redirect("/settings")
    u = db.query_one("SELECT * FROM users WHERE id=?", (uid,))
    if not u:
        abort(404)
    db.execute("UPDATE users SET active=? WHERE id=?", (0 if u["active"] else 1, uid))
    log("停用用户" if u["active"] else "启用用户", "账号", u["username"])
    flash("已更新用户状态", "success")
    return redirect("/settings")


@app.route("/settings/update", methods=["POST"])
@login_required
@role_required("admin")
def settings_update():
    for key in ("company_name", "sales_commission_rate", "buy_high_price_threshold",
                "stock_standard"):
        db.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, request.form.get(key, "").strip()),
        )
    log("修改系统参数", "设置", "更新提成比例/阈值/入库标准等")
    flash("系统参数已保存", "success")
    return redirect("/settings")

# ---------------------------------------------------------------- 搜索
@app.route("/search")
@login_required
def search():
    return redirect(f"/vehicles?q={request.args.get('q', '').strip()}")


if __name__ == "__main__":
    db.init_db()
    print("二手车业务管理系统 MVP 启动：http://127.0.0.1:5001")
    app.run(host="127.0.0.1", port=5001, debug=False)
