"""Parsers: convierten líneas crudas en eventos normalizados.

Dos familias:

- `PatternParser` interpreta una especificación declarativa (JSON, contrato en
  schemas/parser.schema.json). Así los perfiles aportan parsers como datos y el
  núcleo no aprende ninguna tecnología.
- `HttpProxyParser` lee el formato propio del proxy de PEPPER (http.jsonl), que
  es del núcleo.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pepper.correlate.events import Event
from pepper.correlate.sql import sql_shape
from pepper.session import Session, parse_datetime

Unparsed = Tuple[str, str]  # (raw_ref, línea)

_PARAMETER_RE = re.compile(r"\$(\d+)\s*=\s*'((?:[^']|'')*)'")


def _last_segment(value: str) -> str:
    return value.rsplit(".", 1)[-1]


def _keyed_parameters(value: str) -> Optional[Dict[str, str]]:
    """`$1 = '1003', $2 = 'x'` → {"$1": "1003", "$2": "x"}; None si no hay pares."""
    pairs = _PARAMETER_RE.findall(value)
    if not pairs:
        return None
    return {f"${index}": text.replace("''", "'") for index, text in pairs}


TRANSFORMS = {
    "last_segment": _last_segment,
    "upper": str.upper,
    "lower": str.lower,
    "strip": str.strip,
    "int": int,
    "keyed_parameters": _keyed_parameters,
}


@lru_cache(maxsize=256)
def _compile(pattern: str) -> "re.Pattern[str]":
    return re.compile(pattern)


def _condition_holds(when: Dict[str, Any], groups: Dict[str, Any]) -> bool:
    value = groups.get(when["field"])
    value = "" if value is None else str(value)
    if "equals" in when:
        return value == when["equals"]
    if "matches" in when:
        return _compile(when["matches"]).search(value) is not None
    raise ValueError(f"condición sin 'equals' ni 'matches': {when}")


def _field_value(spec: Dict[str, Any], groups: Dict[str, Any]) -> Any:
    if "value" in spec:
        return spec["value"]
    value = groups.get(spec["from"])
    if value is None:
        return spec.get("default")
    if "transform" in spec:
        value = TRANSFORMS[spec["transform"]](value)
    if "map" in spec and value is not None:
        value = spec["map"].get(value, spec.get("default", value))
    return value


def _assign(event: Event, target: str, value: Any) -> None:
    if value is None:
        return
    if target.startswith("metadata."):
        event.metadata[target[len("metadata."):]] = value
    elif target in ("component", "operation", "correlation_id", "message", "severity"):
        setattr(event, target, value)
    else:
        raise ValueError(f"destino de campo no soportado: {target!r}")


class PatternParser:
    def __init__(self, spec: Dict[str, Any], spec_path: Optional[Path] = None):
        self.spec = spec
        self.name = spec_path.name if spec_path else spec.get("source", "parser")
        self.source: str = spec["source"]
        self.line_re = re.compile(spec["line_pattern"])
        timestamp = spec.get("timestamp", {})
        self.ts_group: str = timestamp.get("group", "timestamp")
        self.ts_format: Optional[str] = timestamp.get("format")
        self.fields: Dict[str, Any] = spec.get("fields", {})
        self.event_type_spec: Dict[str, Any] = spec.get("event_type", {})
        self.sql_spec: Dict[str, Any] = spec.get("sql", {})
        continuation = spec.get("continuation")
        self.continuation_re = re.compile(continuation["pattern"]) if continuation else None
        self.merge: Optional[Dict[str, Any]] = spec.get("merge_into_previous")
        self.noise: List[Dict[str, Any]] = spec.get("noise", [])
        self.affinity_keys: List[str] = spec.get("affinity", [])

    @classmethod
    def from_file(cls, path: Path) -> "PatternParser":
        return cls(json.loads(path.read_text(encoding="utf-8")), path)

    def parse_file(self, path: Path, raw_prefix: str, session: Session) -> Tuple[List[Event], List[Unparsed]]:
        events: List[Event] = []
        unparsed: List[Unparsed] = []
        last_by_key: Dict[Any, Event] = {}
        merge_key = self.merge.get("key") if self.merge else None

        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            raw_ref = f"{raw_prefix}:{number}"
            match = self.line_re.match(line)
            if not match:
                if self.continuation_re and events and self.continuation_re.match(line):
                    events[-1].message = (events[-1].message or "") + "\n" + line
                else:
                    unparsed.append((raw_ref, line))
                continue

            groups = match.groupdict()
            if self.merge and _condition_holds(self.merge["when"], groups):
                target = last_by_key.get(groups.get(merge_key)) if merge_key else (events[-1] if events else None)
                if target is not None:
                    for field, field_spec in self.merge.get("fields", {}).items():
                        _assign(target, field, _field_value(field_spec, groups))
                    continue

            event = self._build_event(groups, raw_ref, session)
            events.append(event)
            if merge_key:
                last_by_key[groups.get(merge_key)] = event
        return events, unparsed

    def _build_event(self, groups: Dict[str, Any], raw_ref: str, session: Session) -> Event:
        event = Event(
            timestamp=self._timestamp(groups, session),
            session_id=session.session_id,
            source=self.source,
            event_type=self._event_type(groups),
            raw_ref=raw_ref,
        )
        for field, field_spec in self.fields.items():
            _assign(event, field, _field_value(field_spec, groups))
        if event.event_type == "sql":
            self._shape_sql(event)
        return event

    def _timestamp(self, groups: Dict[str, Any], session: Session) -> datetime:
        raw = groups.get(self.ts_group)
        if raw is None:
            raise ValueError(f"{self.name}: el patrón no capturó el grupo {self.ts_group!r}")
        if self.ts_format:
            parsed = datetime.strptime(raw, self.ts_format)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=session.tz)
            return parsed
        return parse_datetime(raw, session.tz)

    def _event_type(self, groups: Dict[str, Any]) -> str:
        for rule in self.event_type_spec.get("rules", []):
            if _condition_holds(rule["when"], groups):
                return rule["value"]
        return self.event_type_spec.get("default", "log")

    def _shape_sql(self, event: Event) -> None:
        statement = event.message or ""
        prefix = self.sql_spec.get("strip_prefix")
        if prefix:
            statement = _compile(prefix).sub("", statement, count=1)
        statement = statement.strip()
        operation, table, extra = sql_shape(statement)
        event.message = statement
        event.operation = operation
        event.metadata["statement"] = statement
        if table:
            event.metadata["table"] = table
        event.metadata.update(extra)
        if operation == "TRANSACTION":
            event.event_type = "transaction"


class HttpProxyParser:
    """http.jsonl del proxy de PEPPER: una línea JSON por petición y por respuesta."""

    source = "http-proxy"
    name = "http-proxy (núcleo)"
    noise: List[Dict[str, Any]] = []
    affinity_keys: List[str] = []

    def parse_file(self, path: Path, raw_prefix: str, session: Session) -> Tuple[List[Event], List[Unparsed]]:
        events: List[Event] = []
        unparsed: List[Unparsed] = []
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            raw_ref = f"{raw_prefix}:{number}"
            try:
                record = json.loads(line)
                events.append(self._event(record, raw_ref, session))
            except (ValueError, KeyError) as error:
                unparsed.append((raw_ref, f"{line}  ← {error}"))
        return events, unparsed

    def _event(self, record: Dict[str, Any], raw_ref: str, session: Session) -> Event:
        operation = f"{record['method']} {record['path']}"
        direction = record.get("direction", "request")
        metadata: Dict[str, Any] = {"method": record["method"], "path": record["path"]}
        for key in ("client", "content_type", "body", "duration_ms"):
            if key in record:
                metadata[key] = record[key]

        if direction == "response":
            status = int(record["status"])
            metadata["status"] = status
            severity = "error" if status >= 500 else "warn" if status >= 400 else "info"
            event_type, message = "http_response", f"{operation} -> {status}"
        else:
            severity, event_type, message = "info", "http_request", operation

        return Event(
            timestamp=parse_datetime(record["ts"], session.tz),
            session_id=session.session_id,
            source=self.source,
            component="proxy",
            event_type=event_type,
            operation=operation,
            correlation_id=record.get("correlation_id"),
            message=message,
            severity=severity,
            raw_ref=raw_ref,
            metadata=metadata,
        )


BUILTIN_PARSERS = {HttpProxyParser.source: HttpProxyParser}
