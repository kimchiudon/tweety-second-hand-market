from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=5, factory=ClosingConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def initialize(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as connection:
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        migrate(connection)


def migrate(connection: sqlite3.Connection) -> None:
    user_columns = {row["name"] for row in connection.execute("PRAGMA table_info(users)")}
    if "nickname" not in user_columns:
        connection.execute("ALTER TABLE users ADD COLUMN nickname TEXT COLLATE NOCASE")
        connection.execute("UPDATE users SET nickname=username WHERE nickname IS NULL")
    connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_nickname_unique ON users(nickname COLLATE NOCASE)")

    product_columns = {row["name"] for row in connection.execute("PRAGMA table_info(products)")}
    if "image_filename" not in product_columns:
        connection.execute("ALTER TABLE products ADD COLUMN image_filename TEXT")
    connection.execute(
        """INSERT OR IGNORE INTO product_images(product_id,filename,position)
           SELECT id,image_filename,0 FROM products WHERE image_filename IS NOT NULL AND image_filename<>''"""
    )

    message_columns = {row["name"] for row in connection.execute("PRAGMA table_info(messages)")}
    if "product_id" not in message_columns:
        connection.execute("ALTER TABLE messages ADD COLUMN product_id INTEGER REFERENCES products(id) ON DELETE CASCADE")
    if "image_filename" not in message_columns:
        connection.execute("ALTER TABLE messages ADD COLUMN image_filename TEXT")
    if "read_at" not in message_columns:
        connection.execute("ALTER TABLE messages ADD COLUMN read_at TEXT")
    # 상품별 채팅 도입 전에 생성된 메시지는 열 수 있는 대화방이 없으므로
    # 읽지 않음 숫자에 영구적으로 남지 않도록 마이그레이션 시 정리한다.
    connection.execute("UPDATE messages SET read_at=CURRENT_TIMESTAMP WHERE product_id IS NULL AND read_at IS NULL")
    connection.execute(
        """INSERT OR IGNORE INTO message_images(message_id,filename,position)
           SELECT id,image_filename,0 FROM messages WHERE image_filename IS NOT NULL AND image_filename<>''"""
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_messages_product_direct ON messages(product_id, sender_id, recipient_id, created_at DESC)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_messages_unread ON messages(recipient_id, read_at, created_at DESC)")


@contextmanager
def transaction(path: Path, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
    connection = connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
