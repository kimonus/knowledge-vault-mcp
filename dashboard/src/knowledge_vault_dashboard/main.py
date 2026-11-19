"""Independent entrypoint: no access logs, proxy headers or development reload."""

import argparse
import logging
from pathlib import Path

import uvicorn

from .app import create_app
from .config import Settings, load_settings


def run() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    settings = load_settings(args.config) if args.config else Settings()
    # httpx INFO events include URLs/query strings. No request data belongs in runtime logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    uvicorn.run(
        create_app(settings),
        # Dedicated container listener; routing/exposure is an operator deployment boundary.
        host="0.0.0.0",  # noqa: S104  # nosec B104
        port=8080,
        access_log=False,
        proxy_headers=False,
        server_header=False,
    )
