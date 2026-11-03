#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""启动初始化（交接/空白默认）：建表（幂等）+ 按环境变量建首个管理员。

- 默认不灌演示数据：演示数据仅在 SEED_DEMO=1 时灌入（本地演示/开发用）。
- 管理员：提供 ADMIN_USERNAME / ADMIN_PASSWORD 时创建首个管理员；
  库中已有管理员时跳过，绝不覆盖。
- 生产环境（APP_ENV=production）拒绝执行。绝不删除已有数据。

运行：python scripts/init_demo.py（在 car-mgmt 目录下）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
from create_admin import ensure_admin_from_env
from seed import load_demo_data


def main() -> int:
    app_env = os.environ.get("APP_ENV", "development")
    if app_env == "production":
        print(
            "拒绝执行：APP_ENV=production 时禁止运行启动初始化。",
            file=sys.stderr,
        )
        return 1
    db.init_db()
    if os.environ.get("SEED_DEMO") == "1":
        load_demo_data()
    else:
        print("跳过演示数据：默认交付空白系统（本地演示请设 SEED_DEMO=1）。")
    status = ensure_admin_from_env()
    if status == "skipped":
        print("警告：未设置 ADMIN_USERNAME/ADMIN_PASSWORD，未创建管理员账号。")
    elif status == "exists":
        print("库中已有管理员账号，跳过创建。")
    else:
        print(f"管理员账号已创建：{status.split(':', 1)[1]}")
    print(f"启动初始化完成：{db.DB_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
