from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    database_path: Path
    host: str
    port: int
    secure_cookie: bool
    debug: bool

    @classmethod
    def from_env(cls) -> "Config":
        environment = os.getenv("TINY_MARKET_ENV", "development").lower()
        database = Path(os.getenv("TINY_MARKET_DB", str(BASE_DIR / "market.db"))).resolve()
        # Hosting providers conventionally expose the assigned port as PORT.
        port_text = os.getenv("TINY_MARKET_PORT") or os.getenv("PORT", "8000")
        if not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
            raise ValueError("TINY_MARKET_PORT must be an integer from 1 to 65535")
        return cls(
            database_path=database,
            host=os.getenv("TINY_MARKET_HOST", "127.0.0.1"),
            port=int(port_text),
            secure_cookie=environment == "production",
            debug=environment == "development",
        )
