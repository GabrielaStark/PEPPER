"""Sesión de observación: la ventana del flujo y sus colectores (session.json)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

_OFFSET_RE = re.compile(r"^([+-])(\d{2}):(\d{2})$")


def parse_timezone(value: str) -> timezone:
    if value in ("Z", "UTC"):
        return timezone.utc
    match = _OFFSET_RE.match(value)
    if not match:
        raise ValueError(f"timezone inválida: {value!r} (se espera ±HH:MM, Z o UTC)")
    sign = 1 if match.group(1) == "+" else -1
    offset = timedelta(hours=int(match.group(2)), minutes=int(match.group(3)))
    return timezone(sign * offset)


def parse_datetime(value: str, default_tz: timezone) -> datetime:
    """ISO 8601 → datetime con zona. Si el valor no trae zona, se asume la de la sesión."""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=default_tz)
    return parsed


@dataclass
class Collector:
    source: str
    file: str
    kind: str = "profile"
    note: str = ""


@dataclass
class Session:
    session_id: str
    flow_name: str
    observed_start: datetime
    observed_end: datetime
    tz: timezone
    collectors: List[Collector]
    profile_id: Optional[str] = None
    path: Optional[Path] = None

    @classmethod
    def load(cls, path: Path) -> "Session":
        data = json.loads(path.read_text(encoding="utf-8"))
        required = ("session_id", "observed_start", "observed_end", "collectors")
        missing = [key for key in required if key not in data]
        if missing:
            raise ValueError(f"{path}: faltan campos en session.json: {', '.join(missing)}")

        tz = parse_timezone(data.get("timezone", "Z"))
        collectors = [
            Collector(
                source=item["source"],
                file=item["file"],
                kind=item.get("kind", "profile"),
                note=item.get("note", ""),
            )
            for item in data["collectors"]
        ]
        environment = data.get("environment") or {}
        return cls(
            session_id=data["session_id"],
            flow_name=data.get("flow_name", data["session_id"]),
            observed_start=parse_datetime(data["observed_start"], tz),
            observed_end=parse_datetime(data["observed_end"], tz),
            tz=tz,
            collectors=collectors,
            profile_id=environment.get("profile_id"),
            path=path,
        )
