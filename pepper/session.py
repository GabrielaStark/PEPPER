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
    """ISO 8601 → datetime con zona. Si el valor no trae zona, se asume la de la sesión.

    Acepta fracciones de más de 6 dígitos (docker logs --timestamps emite
    nanosegundos) truncando a microsegundos: fromisoformat no las soporta.
    """
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    value = re.sub(r"(\.\d{6})\d+", r"\1", value)
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

        if "timezone" not in data and not re.search(r"(Z|[+-]\d{2}:?\d{2})$", str(data["observed_start"])):
            raise ValueError(f"{path}: session.json no declara timezone y observed_start no trae zona: sin zona las fuentes no se alinean")
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
        start, end = parse_datetime(data["observed_start"], tz), parse_datetime(data["observed_end"], tz)
        if end < start:
            raise ValueError(f"{path}: observed_end ({data['observed_end']}) es anterior a observed_start ({data['observed_start']})")
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
