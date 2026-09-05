"""Secret redaction for retained operation and log output."""

from __future__ import annotations

import re

_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|refresh[_-]?token|authorization)(\s*[:=]\s*)\S+"),
    re.compile(r"(?i)(bearer)\s+[A-Za-z0-9._~+/=-]+"),
)


def redact(value: str) -> str:
    text = str(value)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]", text)
    return text
