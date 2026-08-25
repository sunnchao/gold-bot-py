"""HTTP 响应辅助(镜像 apps/app-server/src/http/response.ts)。"""

from __future__ import annotations

from typing import Any

__all__ = ["JsonResponse", "error", "ok"]

JsonResponse = dict[str, Any]
"""{statusCode, headers?, body, rawBody?} 的宽松字典。"""


def error(status_code: int, message: str) -> JsonResponse:
    return {"statusCode": status_code, "body": {"status": "ERROR", "message": message}}


def ok(body: Any) -> JsonResponse:
    return {"statusCode": 200, "body": body}
