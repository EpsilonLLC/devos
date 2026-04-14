from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sanitize_string_field(value: str) -> str:
    return value.strip()
