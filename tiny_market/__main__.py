from __future__ import annotations

import logging
from wsgiref.simple_server import make_server

from .app import create_app


def main() -> None:
    app = create_app()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(f"Tweety running at http://{app.config.host}:{app.config.port}")
    with make_server(app.config.host, app.config.port, app) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
