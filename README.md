# 二手车业务管理系统 MVP

浏览器访问的管理后台：员工打开网址、登录后进入工作台，无需逐台电脑安装软件。
技术栈：Python 3.12 + Flask + SQLite（标准库 sqlite3，无 ORM）+ Jinja2 + 原生 CSS/JS，
无 npm、无构建步骤。pip 依赖只有 `flask`。

## 快速运行（本地开发）

```bash
cd car-mgmt
pip install -r requirements.txt   # Windows: py -m pip install -r requirements.txt
python scripts/init_db.py         # 建表（幂等，不删库）
python scripts/init_demo.py       # 库为空时灌演示数据（已有数据则跳过）
python app.py                     # 启动，浏览器打开 http://127.0.0.1:5001
```

- 数据库文件：`data/car_mgmt.db`（`python app.py` 的 `__main__` 入口会显式建表）。
- 演示账号见下表（密码均为 123456）；**生产环境永不灌演示账号**，
  首个管理员用 `scripts/create_admin.py` 建立（见下）。

## 环境变量

| 变量 | 说明 | 默认值 |
|---|---|---|
| `APP_ENV` | 运行环境：`development` / `demo` / `testing` / `production` | `development` |
| `SECRET_KEY` | 会话密钥。`APP_ENV=production` 时**必填**，未设置应用拒绝启动；其他环境可不设（用内置演示密钥） | 无（非生产回退演示密钥） |
| `CAR_MGMT_DB_PATH` | SQLite 库文件路径（测试/多环境隔离用） | `data/car_mgmt.db` |

`.env.example` 有占位示例（无真实密钥），复制为 `.env` 后填写；`.env` 不要提交。

## 生产启动（永不 seed）

1. 设置环境变量：`APP_ENV=production`、`SECRET_KEY=<强随机密钥>`、
   `CAR_MGMT_DB_PATH=<生产库路径>`。
2. 建表：`python scripts/init_db.py`（幂等，部署步骤显式调用；应用 import 时不建表）。
3. 建首个管理员：`ADMIN_USERNAME=admin ADMIN_PASSWORD='<强密码>' python scripts/create_admin.py`
   （库中已有 admin 时拒绝覆盖；重置需显式 `--reset-password`）。
4. 启动：`gunicorn app:app --bind 0.0.0.0:$PORT --workers 2`
   （`APP_ENV=production` 且未设 `SECRET_KEY` 时应用直接拒绝启动）。

`seed.py` / `scripts/init_demo.py` 在 `APP_ENV=production` 下**任何参数都拒绝执行**；
删除重建演示库必须同时满足 `APP_ENV ∈ {development, demo, testing}` 且带 `--confirm-reset`。
`Procfile` / `render.yaml` 的启动命令已移除 seed（render.yaml 仅用于客户演示，`APP_ENV=demo`）。

## 默认账号（密码统一 123456）

| 账号    | 姓名      | 角色     |
|---------|-----------|----------|
| admin   | 超级管理员 | admin（全部权限） |
| boss    | 老板       | boss（全部业务+审批） |
| caiwu   | 财务-小陈  | finance（财务/审批/报表） |
| sales1 / sales2 | 销售-小刘 / 销售-小杨 | sales（在售车辆+自己的订单） |
| buyer1 / buyer2 | 收购-李明 / 收购-王芳 | buyer（收购/检测） |
| rework1 / rework2 | 整备-赵师傅 / 整备-孙师傅 | rework（整备工单） |

## 核心流程（已实现闭环）

1. **收购**：收购页创建收车单 → 车辆进入「待检测」；收购价超过阈值（默认 15 万，
   系统设置可改）自动生成「收购高价审批」。
2. **检测**：车辆详情页录入评分/隐患 → 通过则进入「待整备」并**自动生成整备工单**；
   退回则变为「已退回」。
3. **整备**：整备页开始工单 → 登记项目+费用+供应商 → 费用**自动写入成本台账**
   （ledger 只增不改，留痕）→ 完成提交入库审核 → 老板/管理员质检通过变为「在售」，
   或退回整改。
4. **销售**：在售车辆创建销售订单（客户/成交价/优惠/金融方案/过户进度）→
   交车核验 → 交付完成变为「已售出」并剔除库存。
5. **财务**：单车成本 = 收购价 + 整备费用 + 其他费用；部门利润表（销售部/收购部）；
   销售提成 =（成交价 − 车辆总成本）× 提成比例（默认 5%，系统设置可改），
   仅已售出车辆参与计算。规则见财务页下方说明。
6. **审批**：发起 / 通过 / 驳回，全程留痕；不能审批自己发起的单；顶部显示待办角标。
7. **报表**：车辆台账 / 销售报表 / 财务汇总，均可导出 CSV（带 BOM，Excel 打开不乱码）。
8. **系统设置**（仅 admin）：用户增删、启用/禁用；提成比例、高价阈值、入库标准文本配置。

车辆详情页含**全生命周期时间线**（收购→检测→整备→入库→销售→交付）+ 成本明细 + 操作记录。
所有 POST 操作均写入 `operation_logs`；未登录自动跳登录；越权返回 403。
手机端采用响应式 CSS 简单适配（保守方案：小屏时边栏变顶部导航）。

## MVP 范围（本期已做）

工作台看板、车辆全生命周期管理、收购/检测、整备工单与费用台账、销售订单与交付、
单车成本核算、部门利润与提成、审批中心、报表导出、用户与参数设置、按角色菜单/数据隔离。

## 二期清单（本期未做）

- 物流管理模块、固定资产管理模块
- AI 智能体（智能定价 / 车况问答 / 经营分析）
- 微信登录、登录 IP 限制
- 员工微信小程序（现场拍照、查车、审批）、客户小程序（看车、预约、查售后）
- 图片/附件上传（当前检测照片仅文本备注字段 `photos_note`）
- 密码强度策略、登录限流、操作审计导出（CSRF 防护与退出改 POST 已在批次 A 完成）

## 目录结构

```
car-mgmt/
  app.py            # Flask 应用与全部路由（含 CSRF、APP_ENV/SECRET_KEY 启动校验）
  db.py             # 建表、连接、通用查询、操作日志（DB_PATH 支持 CAR_MGMT_DB_PATH 覆盖）
  seed.py           # 演示数据：load_demo_data()（安全）+ 受控的 --confirm-reset 重建
  scripts/
    init_db.py      # 显式建表（幂等），部署步骤调用
    init_demo.py    # 建表后库为空则灌演示数据（生产拒绝）
    create_admin.py # 创建首个管理员（防覆盖，需 --reset-password 才重置）
  tests/            # unittest 回归测试（临时库，见下）
  docs/任务书.md     # 《二手车管理系统 MVP 改进与开发任务书》
  requirements.txt  # flask + gunicorn
  .env.example      # 环境变量占位示例
  Procfile / render.yaml  # 启动命令（无 seed；render.yaml 仅演示，APP_ENV=demo）
  README.md
  data/car_mgmt.db  # SQLite 数据文件（运行后生成，git 不跟踪）
  templates/        # 12 个 Jinja2 模板
  static/           # style.css（深色主题+响应式）、app.js（含 CSRF token 注入）
```

## 测试（批次 A）

```bash
cd car-mgmt
python -m unittest discover -s tests -v
```

用例全部使用 `/tmp` 下临时数据库（`CAR_MGMT_DB_PATH` + `APP_ENV=testing`），
绝不触碰 `data/car_mgmt.db`：启动不清库、生产缺密钥拒绝启动、生产 seed 被拒绝、
CSRF 拦截与放行、工作台状态分布、整备受限详情（403/财务信息隐藏/链接修正）。

## 验证记录（2026-10-12）

- `pip install -r requirements.txt` → Flask 3.1.3 安装成功
- `python seed.py` → 12 台车 / 7 条检测 / 4 个整备工单 / 2 个销售订单 / 4 条审批，账实相符
- `python app.py`（127.0.0.1:5001）+ curl 全链路测试：
  - 登录、各页面（工作台/车辆/详情/收购/整备/销售/财务/审批/报表/设置）与 3 个 CSV 导出全部 200，无 500
  - E2E：收车(160000超阈值→自动生成高价审批)→检测通过(自动建整备工单)→整备登记500元(自动进台账)→
    入库审核→创建销售订单175000→交付→车辆「已售出」，总成本 160500，终态断言通过
  - 审批流：boss 通过高价审批单；销售员访问财务/设置返回 403；未登录访问返回 302 跳登录
- 测试后已 `pkill` 关闭服务，数据库已用 `seed.py` 重置为干净种子数据
