"""Create the first deployment administrator without storing its password in Git."""

from __future__ import annotations

import os
import sqlite3

from tiny_market.config import Config
from tiny_market.db import initialize, transaction
from tiny_market.security import USERNAME_RE, hash_password, validate_password


def main() -> None:
    username = os.getenv("TINY_MARKET_ADMIN_USERNAME", "").strip()
    password = os.getenv("TINY_MARKET_ADMIN_PASSWORD", "")
    if not username and not password:
        return
    if not username or not password:
        raise SystemExit("Both TINY_MARKET_ADMIN_USERNAME and TINY_MARKET_ADMIN_PASSWORD are required")
    if not USERNAME_RE.fullmatch(username):
        raise SystemExit("Invalid deployment administrator username")
    issues = validate_password(password)
    if issues:
        raise SystemExit("Deployment administrator password does not meet the password policy")

    config = Config.from_env()
    initialize(config.database_path)
    try:
        with transaction(config.database_path, immediate=True) as connection:
            existing = connection.execute("SELECT role FROM users WHERE username=?", (username,)).fetchone()
            if existing:
                if existing["role"] != "admin":
                    raise SystemExit("The deployment administrator username belongs to a non-admin account")
                return
            connection.execute(
                "INSERT INTO users(username,nickname,password_hash,role) VALUES (?,?,?, 'admin')",
                (username, username, hash_password(password)),
            )
    except sqlite3.IntegrityError as error:
        raise SystemExit("Could not create the deployment administrator") from error
    print(f"Created deployment administrator '{username}'")


if __name__ == "__main__":
    main()
