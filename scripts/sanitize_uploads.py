from __future__ import annotations

import secrets
from pathlib import Path

from tiny_market.config import Config
from tiny_market.db import connect, initialize, transaction
from tiny_market.security import sanitize_legacy_image


def main() -> None:
    config = Config.from_env()
    initialize(config.database_path)
    upload_dir = config.database_path.parent / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    with connect(config.database_path) as connection:
        names = {
            row[0]
            for row in connection.execute(
                """SELECT filename FROM product_images
                   UNION SELECT filename FROM message_images
                   UNION SELECT image_filename FROM products WHERE image_filename IS NOT NULL
                   UNION SELECT image_filename FROM messages WHERE image_filename IS NOT NULL"""
            )
            if row[0]
        }

    converted = 0
    for old_name in sorted(names):
        old_path = upload_dir / old_name
        if not old_path.is_file():
            continue
        extension, sanitized = sanitize_legacy_image(old_path.read_bytes())
        new_name = f"{secrets.token_hex(16)}.{extension}"
        new_path = upload_dir / new_name
        with new_path.open("xb") as output:
            output.write(sanitized)
        try:
            with transaction(config.database_path, immediate=True) as connection:
                connection.execute("UPDATE product_images SET filename=? WHERE filename=?", (new_name, old_name))
                connection.execute("UPDATE message_images SET filename=? WHERE filename=?", (new_name, old_name))
                connection.execute("UPDATE products SET image_filename=? WHERE image_filename=?", (new_name, old_name))
                connection.execute("UPDATE messages SET image_filename=? WHERE image_filename=?", (new_name, old_name))
        except Exception:
            new_path.unlink(missing_ok=True)
            raise
        old_path.unlink(missing_ok=True)
        converted += 1
    print(f"Sanitized {converted} existing upload(s).")


if __name__ == "__main__":
    main()
