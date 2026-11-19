"""Explicit public application files and page routes; never an arbitrary static/proxy mount."""

import re
from pathlib import Path

from starlette.requests import Request
from starlette.responses import Response

from .errors import BoundaryError

PAGES = (
    "/dashboard/",
    "/dashboard/login",
    "/dashboard/explorer",
    "/dashboard/portal",
    "/dashboard/state",
    "/dashboard/assertions/{id}",
    "/dashboard/portal/contexts/{id}",
    "/dashboard/portal/topics/{key}",
    "/dashboard/portal/unclassified/{dimension}",
)
PAGE_PATTERN = re.compile(
    r"/dashboard/(?:login|explorer|portal|state)?"
    r"|/dashboard/assertions/[0-9a-fA-F-]{36}"
    r"|/dashboard/portal/contexts/[0-9a-fA-F-]{36}"
    r"|/dashboard/portal/topics/[A-Za-z0-9_-]{1,512}"
    r"|/dashboard/portal/unclassified/(?:topics|contexts|facets)"
)
ASSET_PATTERN = re.compile(r"[A-Za-z0-9_-]+\.(?:js|css)")


class Assets:
    def __init__(self, directory: Path | None = None) -> None:
        directory = directory or Path(__file__).parent / "static"
        self.index: str | None = None
        self.files: dict[str, tuple[bytes, str]] = {}
        index = directory / "index.html"
        if index.is_file() and not index.is_symlink():
            self.index = index.read_text()
        asset_directory = directory / "assets"
        if asset_directory.is_dir():
            for path in asset_directory.iterdir():
                if path.is_file() and not path.is_symlink() and ASSET_PATTERN.fullmatch(path.name):
                    media_type = "text/javascript" if path.suffix == ".js" else "text/css"
                    self.files["/dashboard/assets/" + path.name] = (path.read_bytes(), media_type)

    def is_page(self, path: str) -> bool:
        return self.index is not None and PAGE_PATTERN.fullmatch(path) is not None

    def allows(self, path: str) -> bool:
        return self.is_page(path) or path in self.files

    def serve(self, request: Request, mode: str) -> Response:
        if request.query_params:
            raise BoundaryError(422, "invalid_input")
        path = request.url.path
        if path in self.files:
            content, media_type = self.files[path]
            return Response(content, media_type=media_type)
        if self.is_page(path):
            # This enum is public presentation metadata, never private config/host/token material.
            return Response(
                (self.index or "").replace("__KV_AUTH_MODE__", mode), media_type="text/html"
            )
        raise BoundaryError(404, "unknown_route")
