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
from functools import wraps

from flask import (Flask, Response, abort, flash, g, redirect, render_template,
                   request, session)
from werkzeug.security import check_password_hash, generate_password_hash

import db

app = Flask(__name__)

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
  COALESCE((SELECT SUM(amount) FROM cost_ledger WHERE vehicle_id=v.id AND category='其他'),0) AS other_cost"""
VEHICLE_FROM = ("FROM vehicles v LEFT JOIN users u1 ON u1.id=v.buyer_id "
                "LEFT JOIN users u2 ON u2.id=v.sales_id")


def vehicle_rows(where="", args=(), order="ORDER BY v.id DESC"):
    rows = db.query(f"SELECT {VEHICLE_COLS} {VEHICLE_FROM} {where} {order}", args)
    for r in rows:
        r["total_cost"] = (r["buy_price"] or 0) + (r["rework_cost"] or 0) + (r["other_cost"] or 0)
    return rows


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
                income += so["price"] or 0
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
        o["items_total"] = sum(i["cost"] or 0 for i in o["items"])
    if restricted:
        # 受限视图：后端不再查询/传递财务与隐私数据，模板也不渲染这些区块
        ledger, sales_orders, logs, timeline = [], [], [], []
        v["sold_price"] = None
    else:
        so = db.query_one(
            "SELECT price FROM sales_orders WHERE vehicle_id=? ORDER BY id DESC", (vid,)
        )
        v["sold_price"] = so["price"] if so else None
        ledger = db.query(
            """SELECT l.*, u.name AS created_by_name FROM cost_ledger l
               LEFT JOIN users u ON u.id=l.created_by
               WHERE l.vehicle_id=? ORDER BY l.id""",
            (vid,),
        )
        sales_orders = db.query(
            """SELECT s.*, u.name AS sales_name FROM sales_orders s
               LEFT JOIN users u ON u.id=s.sales_id
               WHERE s.vehicle_id=? ORDER BY s.id DESC""",
            (vid,),
        )
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
    threshold = float(get_setting("buy_high_price_threshold", "150000") or 150000)
    return render_template(
        "vehicle_detail.html", active="vehicles", vehicle=v, inspections=inspections,
        rework_orders=orders, ledger=ledger, sales_orders=sales_orders,
        logs=logs, timeline=timeline, threshold=threshold, restricted=restricted,
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
    threshold = float(get_setting("buy_high_price_threshold", "150000") or 150000)
    if request.method == "POST":
        brand = request.form.get("brand", "").strip()
        model = request.form.get("model", "").strip()
        buy_price = float(request.form.get("buy_price", "") or 0)
        if not brand or not model or buy_price <= 0:
            flash("品牌、型号、收购价为必填项", "error")
            return redirect("/acquisition")
        vid = db.execute(
            """INSERT INTO vehicles(vin,brand,model,year,mileage,color,plate,buy_price,
                                    status,buyer_id,customer_name,customer_phone,note)
               VALUES(?,?,?,?,?,?,?,?, '待检测',?,?,?,?)""",
            (request.form.get("vin", "").strip(), brand, model,
             request.form.get("year", "").strip() or None,
             request.form.get("mileage", "").strip() or None,
             request.form.get("color", "").strip(),
             request.form.get("plate", "").strip(), buy_price, g.user["id"],
             request.form.get("customer_name", "").strip(),
             request.form.get("customer_phone", "").strip(),
             request.form.get("note", "").strip()),
        )
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
    return render_template(
        "acquisition.html", active="acquisition", vehicles=recent, threshold=threshold,
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
        o["items_total"] = sum(i["cost"] or 0 for i in o["items"])
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
    cost = float(request.form.get("cost", "") or 0)
    if not item or cost <= 0:
        flash("项目名称和费用为必填项", "error")
        return redirect("/rework")
    supplier = request.form.get("supplier", "").strip()
    pay_method = request.form.get("pay_method", "").strip()
    db.execute(
        "INSERT INTO rework_items(order_id,item,cost,supplier,pay_method) VALUES(?,?,?,?,?)",
        (oid, item, cost, supplier, pay_method),
    )
    # 费用自动写入成本台账（只增不改）
    db.execute(
        "INSERT INTO cost_ledger(vehicle_id,category,amount,note,created_by) VALUES(?,'整备费用',?,?,?)",
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
        price = float(request.form.get("price", "") or 0)
        if not customer or price <= 0:
            flash("客户姓名和成交价为必填项", "error")
            return redirect("/sales")
        oid = db.execute(
            """INSERT INTO sales_orders(vehicle_id,sales_id,customer,phone,price,discount,
                                        finance_plan,contract_no)
               VALUES(?,?,?,?,?,?,?,?)""",
            (vid, g.user["id"], customer, request.form.get("phone", "").strip(),
             price, float(request.form.get("discount", "") or 0),
             request.form.get("finance_plan", "").strip(),
             request.form.get("contract_no", "").strip()),
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
    sellable = db.query(
        "SELECT id, brand, model, plate, buy_price FROM vehicles "
        "WHERE status='在售' ORDER BY id DESC"
    )
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
        v["sale_price"] = so["price"] if so else None
        v["profit"] = (v["sale_price"] - v["total_cost"]) if v["sale_price"] else None
    totals = {
        "buy": sum(v["buy_price"] or 0 for v in rows),
        "rework": sum(v["rework_cost"] or 0 for v in rows),
        "other": sum(v["other_cost"] or 0 for v in rows),
        "total": sum(v["total_cost"] for v in rows),
        "sale": sum(v["sale_price"] or 0 for v in rows),
        "profit": sum(v["profit"] or 0 for v in rows),
    }
    rate = float(get_setting("sales_commission_rate", "0.05") or 0.05)
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
        amount = float(request.form.get("amount", "") or 0)
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
    data = [[
        r["id"], f"{r['brand']}{r['model']}", r["plate"], r["customer"], r["phone"],
        f"{r['price'] or 0:.0f}", f"{r['discount'] or 0:.0f}", r["finance_plan"],
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
        sale = so["price"] if so else 0
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
