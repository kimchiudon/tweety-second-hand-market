from __future__ import annotations

import getpass
import sqlite3

from tiny_market.config import Config
from tiny_market.db import initialize, transaction
from tiny_market.security import USERNAME_RE, hash_password, validate_password


def main() -> None:
    config = Config.from_env()
    initialize(config.database_path)
    username = input("관리자 아이디: ").strip()
    if not USERNAME_RE.fullmatch(username):
        raise SystemExit("아이디는 3~24자의 영문, 숫자, 밑줄만 사용할 수 있습니다.")
    password = getpass.getpass("관리자 비밀번호: ")
    confirmation = getpass.getpass("비밀번호 확인: ")
    if password != confirmation:
        raise SystemExit("비밀번호가 일치하지 않습니다.")
    issues = validate_password(password)
    if issues:
        raise SystemExit(" ".join(issues))
    try:
        with transaction(config.database_path) as connection:
            connection.execute(
                "INSERT INTO users(username,nickname,password_hash,role) VALUES (?,?,?, 'admin')",
                (username, username, hash_password(password)),
            )
    except sqlite3.IntegrityError:
        raise SystemExit("이미 사용 중인 아이디입니다.") from None
    print(f"관리자 '{username}' 계정을 만들었습니다.")


if __name__ == "__main__":
    main()
