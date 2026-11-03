"""定价链与费用分摊 — 客户管理表单（upload.xltx）规则的纯函数实现。

规则来源：库存表 AE:AN 列公式、业绩表 G 列公式、费用明细表 F:P / R:AF 列类目。
金额单位：元。档位表按表原文照录，IFS 语义为“首个匹配分支生效”。

已知疑点（待客户确认，见 docs/表格分析.md 第六节），此处不擅自改数：
- FLOOR_STD_TIERS 中 30–35 万档原文为 19000，破坏单调，疑似笔误；
- CYCLE_DISCOUNT_TIERS 中 141–150 天原文无分支，此处按封顶值 -14% 计；
- WHOLESALE_STD_TIERS 中 20–25 万档原文两分支重叠，IFS 首匹配取 8000。
"""

from __future__ import annotations

import datetime as _dt

# ---------------------------------------------------------------- 档位表
# (上限, 取值)，按上限升序；最后一条上限为 inf 兜底。x <= 上限 即命中。
PROFIT_RATE_TIERS = [  # 利润率 AE：按车辆成本
    (100000, 0.12),
    (150000, 0.10),
    (200000, 0.09),
    (250000, 0.08),
    (300000, 0.07),
    (350000, 0.06),
    (float("inf"), 0.06),
]

CYCLE_DISCOUNT_TIERS = [  # 周期降价 AF：按库存天数，每 10 天 -1%，封顶 -14%
    (10, 0.00),
    (20, -0.01),
    (30, -0.02),
    (40, -0.03),
    (50, -0.04),
    (60, -0.05),
    (70, -0.06),
    (80, -0.07),
    (90, -0.08),
    (100, -0.09),
    (110, -0.10),
    (120, -0.11),
    (130, -0.12),
    (140, -0.13),
    (float("inf"), -0.14),
]

SHOWCARD_TIERS = [  # 水牌价幅度 AH：按车辆成本
    (100000, 6000),
    (150000, 7000),
    (200000, 8000),
    (250000, 9000),
    (300000, 10000),
    (350000, 11000),
    (float("inf"), 12000),
]

WHOLESALE_STD_TIERS = [  # 批发底价标准 AI：按车辆成本
    (50000, 4000),
    (100000, 5000),
    (150000, 6000),
    (200000, 7000),
    (250000, 8000),
    (float("inf"), 9000),
]

FLOOR_STD_TIERS = [  # 销售底价标准 AJ：按车辆成本（19000 档原文照录，待确认）
    (100000, 4000),
    (150000, 5000),
    (200000, 6000),
    (250000, 7000),
    (300000, 8000),
    (350000, 19000),
    (float("inf"), 10000),
]

# 展厅标价 = 销售底价 + 固定加价（库存表 AM 列公式）
SHOWROOM_MARKUP = 5500

# ---------------------------------------------------------------- 费用类目
# 费用明细表：入库前 F:P（11 类），入库后 R:AF（15 类）。
PRE_COST_CATEGORIES = [
    "板车费", "中介费", "资料查询费", "检测费", "代驾费", "差旅费",
    "加油", "过路费", "维修费", "跟换配件", "其他",
]
POST_COST_CATEGORIES = [
    "洗车", "撕膜", "做漆", "维修", "保养", "停车", "跟换电瓶", "脚垫",
    "快递", "保险", "加油", "过户", "中介费", "税金", "其他",
]
COST_PHASES = ("入库前", "入库后")
ALL_COST_CATEGORIES = sorted(set(PRE_COST_CATEGORIES) | set(POST_COST_CATEGORIES))


def _num(value, default=0.0) -> float:
    """数字清洗：None、空串、文本一律转为 default，永不抛错。

    定价链跑在每辆车上，库里一条脏数据不能打崩整页（2026-11-03 线上 500 根因）。
    """
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _tier(tiers, x: float):
    """IFS 首匹配：返回首个 x <= 上限 的取值。"""
    for limit, value in tiers:
        if x <= limit:
            return value
    return tiers[-1][1]


def profit_rate(cost: float) -> float:
    return _tier(PROFIT_RATE_TIERS, cost)


def cycle_discount(stock_days: int) -> float:
    return _tier(CYCLE_DISCOUNT_TIERS, stock_days)


def showcard_amount(cost: float) -> float:
    return _tier(SHOWCARD_TIERS, cost)


def wholesale_std(cost: float) -> float:
    return _tier(WHOLESALE_STD_TIERS, cost)


def floor_std(cost: float) -> float:
    return _tier(FLOOR_STD_TIERS, cost)


def compute_pricing(buy_price: float, pre_cost: float, post_cost: float,
                    stock_days: int) -> dict:
    """定价链（库存表 AD:AN 列）。

    buy_price 打款价 AA；pre_cost 入库前费用 AB；post_cost 入库后费用 AC。
    返回含全部中间值与四种标价的字典，金额均为元（利润率/降价为小数）。
    """
    buy_price = _num(buy_price)
    pre_cost = _num(pre_cost)
    post_cost = _num(post_cost)
    stock_days = _num(stock_days)
    cost = buy_price + pre_cost + post_cost
    rate = profit_rate(cost)
    discount = cycle_discount(stock_days)
    profit_value = (rate + discount) * cost
    showcard = showcard_amount(cost)
    ws_std = wholesale_std(cost)
    fl_std = floor_std(cost)
    return {
        "buy_price": buy_price,
        "pre_cost": pre_cost,
        "post_cost": post_cost,
        "cost": cost,
        "profit_rate": rate,
        "cycle_discount": discount,
        "profit_value": profit_value,
        "showcard": showcard,
        "wholesale_std": ws_std,
        "floor_std": fl_std,
        "wholesale_price": ws_std + cost,        # 当期批发价 AK
        "floor_price": cost + profit_value,      # 销售底价 AL
        "showroom_price": cost + profit_value + SHOWROOM_MARKUP,  # 展厅标价 AM
        "online_price": showcard + profit_value + cost,           # 网络标价 AN
    }


# ---------------------------------------------------------------- 日期/编号
def stock_in_date(vehicle: dict) -> str | None:
    """入库时间 BB：优先取采购日期 purchase_date，否则取建单日期。

    表里入库时间为手工维护的日期；系统内以采购日期为口径（客户未另行定义时）。
    返回 'YYYY-MM-DD' 或 None。
    """
    d = (vehicle.get("purchase_date") or "").strip()
    if len(d) >= 10 and d[4] == "-" and d[7] == "-":
        return d[:10]
    created = (vehicle.get("created_at") or "").strip()
    return created[:10] if len(created) >= 10 else None


def stock_days(stock_in: str | None, today: _dt.date | None = None) -> int:
    """库存时间 BC = 今天 - 入库时间（天），负数按 0 计。"""
    if not stock_in:
        return 0
    try:
        start = _dt.date.fromisoformat(stock_in[:10])
    except ValueError:
        return 0
    today = today or _dt.date.today()
    return max(0, (today - start).days)


def turnover_days(stock_in: str | None, sold_on: str | None) -> int | None:
    """周转天数 BE = 出售时间 - 入库时间（天）；未售出返回 None。"""
    if not stock_in or not sold_on:
        return None
    try:
        start = _dt.date.fromisoformat(stock_in[:10])
        end = _dt.date.fromisoformat(sold_on[:10])
    except ValueError:
        return None
    return max(0, (end - start).days)


def vin_last6(vin: str | None) -> str:
    """车架号后 6 位（全表连接键口径）；无 VIN 返回 '-'。"""
    vin = (vin or "").strip()
    return vin[-6:] if vin else "-"


def total_deal_price(sale: dict | None) -> float | None:
    """合计成交价 AW = 合同销售价 + 贷款返点 + GPS 返点 + 保险返点 + 质保利润 + 其他。

    未成交返回 None。
    """
    if not sale:
        return None
    return sum(
        _num(sale.get(k))
        for k in ("price", "loan_rebate", "gps_rebate",
                  "insurance_rebate", "warranty_profit", "other_income")
    )
