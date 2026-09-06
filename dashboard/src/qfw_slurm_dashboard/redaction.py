"""Secret redaction for retained operation and log output."""

from __future__ import annotations

import re
from typing import Any

_SECRET_PATTERNS = (
    re.compile(
        r"(?i)(api[_-]?key|refresh[_-]?token|access[_-]?token|"
        r"client[_-]?secret|authorization|password)"
        r"(\s*[:=]\s*)(?:bearer\s+)?"
        r"(?:\"[^\"]*\"|'[^']*'|[^\s,}\]]+)"
    ),
    re.compile(r"(?i)(bearer)\s+[A-Za-z0-9._~+/=-]+"),
)


def redact(value: str) -> str:
    text = str(value)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(
            lambda match: f"{match.group(1)}{match.group(2) if match.lastindex == 2 else ' '}"
            "[REDACTED]",
            text,
        )
    return text


def redact_payload(value: Any) -> Any:
    """Return a recursively redacted copy that preserves JSON structure."""

    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            lowered = str(key).lower().replace("-", "_")
            if lowered in {
                "api_key", "refresh_token", "access_token", "client_secret",
                "authorization", "password",
            }:
                result[key] = "[REDACTED]"
            else:
                result[key] = redact_payload(item)
        return result
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_payload(item) for item in value)
    if isinstance(value, str):
        return redact(value)
    return value
