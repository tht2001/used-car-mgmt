#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""演示环境初始化：建表（幂等）后，若库中没有用户则灌入演示数据。

生产环境（APP_ENV=production）拒绝执行。绝不删除已有数据。

运行：python scripts/init_demo.py（在 car-mgmt 目录下）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
from seed import load_demo_data


def main() -> int:
    app_env = os.environ.get("APP_ENV", "development")
    if app_env == "production":
        print(
            "拒绝执行：APP_ENV=production 时禁止初始化演示数据。",
            file=sys.stderr,
        )
        return 1
    db.init_db()
    load_demo_data()
    print(f"演示初始化完成：{db.DB_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
