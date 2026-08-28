"""El evento normalizado: la forma común de toda evidencia (schemas/event.schema.json)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

PROTECTED_SEVERITIES = ("warn", "error", "fatal")
WRITE_OPERATIONS = ("INSERT", "UPDATE", "DELETE", "PROCEDURE", "DDL")


@dataclass
class Event:
    timestamp: datetime
    session_id: str
    source: str
    event_type: str
    raw_ref: str
    component: Optional[str] = None
    operation: Optional[str] = None
    correlation_id: Optional[str] = None
    message: Optional[str] = None
    severity: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    event_id: Optional[str] = None
    ingest_order: int = 0

    @property
    def is_protected(self) -> bool:
        """Evidencia que la reducción nunca descarta: errores, excepciones y escrituras."""
        if self.severity in PROTECTED_SEVERITIES:
            return True
        if self.event_type == "exception":
            return True
        if self.event_type == "sql" and self.operation in WRITE_OPERATIONS:
            return True
        return False

    @property
    def summary(self) -> str:
        text = self.message or self.operation or self.event_type
        first_line = text.splitlines()[0] if text else ""
        return first_line if len(first_line) <= 140 else first_line[:137] + "..."

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "event_id": self.event_id,
            "timestamp": self.timestamp.isoformat(timespec="milliseconds"),
            "session_id": self.session_id,
            "source": self.source,
            "component": self.component,
            "event_type": self.event_type,
            "operation": self.operation,
            "correlation_id": self.correlation_id,
            "message": self.message,
            "raw_ref": self.raw_ref,
            "severity": self.severity,
            "metadata": self.metadata,
        }
        # correlation_id se conserva aunque sea null: "no se propagó" es información.
        return {key: value for key, value in data.items() if value is not None or key == "correlation_id"}


def write_jsonl(events: Iterable[Event], path: Path) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    records = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records
