#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""显式建表命令：只创建不存在的表（幂等），不删库、不灌数据。

部署步骤（如 render.yaml 的 startCommand）显式调用，避免多个 worker
在 import 时并发改结构。

使用目标库：环境变量 CAR_MGMT_DB_PATH，未设置时为 data/car_mgmt.db。

运行：python scripts/init_db.py（在 car-mgmt 目录下）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db


def main() -> int:
    db.init_db()
    print(f"建表完成（幂等）：{db.DB_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
