#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""创建首个管理员账号（一次性初始化命令）。

账号来源（优先级）：命令行参数 --username/--password，高于环境变量
ADMIN_USERNAME / ADMIN_PASSWORD。

- 若库中已存在任何 role='admin' 的账号：拒绝创建/覆盖，提示并退出 1；
  只有显式传入 --reset-password 时才重置该管理员的密码。
- 非破坏性：先建表（幂等），不删库、不灌演示数据。

运行示例（在 car-mgmt 目录下）：
  ADMIN_USERNAME=admin ADMIN_PASSWORD='强密码' python scripts/create_admin.py
  python scripts/create_admin.py --username admin --password '强密码'
  python scripts/create_admin.py --username admin --password '新密码' --reset-password
"""
import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
from werkzeug.security import generate_password_hash


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="创建首个管理员账号")
    parser.add_argument("--username", default=os.environ.get("ADMIN_USERNAME", ""),
                        help="管理员用户名（默认取环境变量 ADMIN_USERNAME）")
    parser.add_argument("--password", default=os.environ.get("ADMIN_PASSWORD", ""),
                        help="管理员密码（默认取环境变量 ADMIN_PASSWORD；缺省则交互输入）")
    parser.add_argument("--reset-password", action="store_true",
                        help="已存在管理员时，重置其密码（否则拒绝覆盖）")
    args = parser.parse_args(argv)

    username = args.username.strip()
    if not username:
        print("错误：未提供用户名（--username 或 ADMIN_USERNAME）。", file=sys.stderr)
        return 1
    password = args.password
    if not password:
        try:
            password = getpass.getpass("管理员密码（输入不回显）：")
        except (EOFError, KeyboardInterrupt):
            print("\n已取消。", file=sys.stderr)
            return 1
    if not password:
        print("错误：密码不能为空。", file=sys.stderr)
        return 1

    db.init_db()
    existing = db.query(
        "SELECT id, username FROM users WHERE role='admin' ORDER BY id"
    )
    if existing:
        names = ", ".join(r["username"] for r in existing)
        if not args.reset_password:
            print(
                f"拒绝执行：库中已存在管理员账号（{names}），不会覆盖密码。"
                "如需重置请显式传入 --reset-password。",
                file=sys.stderr,
            )
            return 1
        target = next((r for r in existing if r["username"] == username), existing[0])
        db.execute(
            "UPDATE users SET password_hash=?, active=1 WHERE id=?",
            (generate_password_hash(password), target["id"]),
        )
        print(f"已重置管理员 {target['username']} 的密码。")
        return 0

    db.execute(
        "INSERT INTO users (username, password_hash, name, role) VALUES (?, ?, ?, 'admin')",
        (username, generate_password_hash(password), "系统管理员"),
    )
    print(f"管理员账号已创建：{username}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
