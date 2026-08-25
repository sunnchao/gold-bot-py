"""镜像 apps/app-server/src/app.ts staticDashboardResponse。"""

from __future__ import annotations

import posixpath
from pathlib import Path
from urllib.parse import unquote

from backend.api.http.response import JsonResponse

__all__ = ["static_dashboard_response"]


def static_dashboard_response(method: str, path: str, release_root: str | Path) -> JsonResponse | None:
    dist_dir = Path(release_root) / "apps" / "app-web" / "dist"
    if not dist_dir.is_dir():
        return None
    if method not in {"GET", "HEAD"}:
        return {
            "statusCode": 405,
            "headers": {
                "Allow": "GET, HEAD",
                "Content-Type": "text/plain; charset=utf-8",
            },
            "body": None,
            "rawBody": "method not allowed\n",
        }

    target = _resolve_dashboard_file(dist_dir, path)
    if target is None:
        return None
    return {
        "statusCode": 200,
        "headers": {"Content-Type": _content_type_for_path(target)},
        "body": None,
        "rawBody": b"" if method == "HEAD" else target.read_bytes(),
    }


def _resolve_dashboard_file(dist_dir: Path, request_path: str) -> Path | None:
    cleaned = _clean_dashboard_path(request_path)
    if cleaned is None:
        return None
    if cleaned == "":
        candidates = [dist_dir / "index.html"]
    else:
        candidates = [
            dist_dir / cleaned,
            dist_dir / cleaned / "index.html",
            dist_dir / f"{cleaned}.html",
        ]
        if cleaned.startswith("accounts/"):
            candidates.extend(
                [
                    dist_dir / "accounts" / "__dynamic__" / "index.html",
                    dist_dir / "accounts" / "__dynamic__.html",
                ]
            )
        if Path(cleaned).suffix == "":
            candidates.append(dist_dir / "index.html")
    for candidate in candidates:
        if candidate.is_file() and _is_inside(dist_dir, candidate):
            return candidate
    return None


def _clean_dashboard_path(request_path: str) -> str | None:
    try:
        decoded = unquote(request_path)
    except Exception:
        return None
    cleaned = posixpath.normpath(f"/{decoded}").lstrip("/\\")
    if cleaned == ".":
        return ""
    if cleaned == ".." or cleaned.startswith("../") or cleaned.startswith("..\\"):
        return None
    return cleaned


def _content_type_for_path(path: Path) -> str:
    match path.suffix.lower():
        case ".html":
            return "text/html; charset=utf-8"
        case ".css":
            return "text/css; charset=utf-8"
        case ".js":
            return "application/javascript; charset=utf-8"
        case ".json":
            return "application/json"
        case ".txt":
            return "text/plain; charset=utf-8"
        case _:
            return "application/octet-stream"


def _is_inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True
