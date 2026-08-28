"""Reducción determinística: descarta ruido y audita cada descarte.

Nunca se descarta evidencia protegida (`Event.is_protected`): errores,
excepciones, escrituras a base de datos y respuestas HTTP ≥ 400.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from pepper.correlate.events import Event
from pepper.session import Session

# Ruido que existe en cualquier stack. Lo específico lo aporta cada parser (spec.noise).
GENERIC_NOISE: List[Dict[str, Any]] = [
    {
        "id": "health-check",
        "description": "sondeos de salud (health, ping)",
        "event_types": ["http_request", "http_response", "log"],
        "matches": r"/(health|healthz|ping|actuator/health)(\?|\s|$)",
    },
    {
        "id": "connection-validation",
        "description": "validación de conexión a base de datos",
        "event_types": ["sql"],
        "matches": r"^SELECT\s+1\s*$",
    },
]

_OUTSIDE_WINDOW = {"id": "outside-window", "description": "fuera de la ventana observada"}
_DUPLICATE = {
    "id": "duplicate",
    "description": "línea de log repetida de forma consecutiva e idéntica en la misma fuente (solo eventos `log`; el SQL nunca se deduplica)",
}


@dataclass
class Drop:
    raw_ref: str
    rule_id: str
    summary: str


@dataclass
class ReductionReport:
    parsed: int = 0
    kept: int = 0
    rules: List[Dict[str, Any]] = field(default_factory=list)
    drops: List[Drop] = field(default_factory=list)
    unparsed: List[Tuple[str, str]] = field(default_factory=list)
    protected_outside_window: int = 0

    def count(self, rule_id: str) -> int:
        return sum(1 for drop in self.drops if drop.rule_id == rule_id)


def _matching_rule(event: Event, rules: List[Tuple[Dict[str, Any], Optional[str]]]) -> Optional[Dict[str, Any]]:
    text = event.message or ""
    for rule, source in rules:
        if source is not None and source != event.source:
            continue
        if "event_types" in rule and event.event_type not in rule["event_types"]:
            continue
        if re.search(rule["matches"], text):
            return rule
    return None


def _fingerprint(event: Event) -> Tuple[Any, ...]:
    # La metadata forma parte de la identidad: dos líneas iguales de threads distintos no son duplicados.
    return (
        event.source,
        event.event_type,
        event.component,
        event.operation,
        event.message,
        json.dumps(event.metadata, sort_keys=True, ensure_ascii=False, default=str),
    )


def reduce_events(
    events: List[Event],
    session: Session,
    source_noise: Dict[str, List[Dict[str, Any]]],
) -> Tuple[List[Event], ReductionReport]:
    rules: List[Tuple[Dict[str, Any], Optional[str]]] = [(rule, None) for rule in GENERIC_NOISE]
    for source, noise in source_noise.items():
        rules.extend((rule, source) for rule in noise)

    report = ReductionReport(parsed=len(events))
    report.rules = [_OUTSIDE_WINDOW] + [
        {"id": rule["id"], "description": rule.get("description", ""), "source": source}
        for rule, source in rules
    ] + [_DUPLICATE]

    kept: List[Event] = []
    # Último evento parseado (no solo conservado) por fuente: "consecutivo" se mide en la evidencia cruda.
    previous_by_source: Dict[str, Event] = {}
    for event in events:
        previous = previous_by_source.get(event.source)
        previous_by_source[event.source] = event

        inside = session.observed_start <= event.timestamp <= session.observed_end
        if not inside:
            if event.is_protected:
                event.metadata["outside_window"] = True
                report.protected_outside_window += 1
            else:
                report.drops.append(Drop(event.raw_ref, _OUTSIDE_WINDOW["id"], event.summary))
                continue

        if not event.is_protected:
            rule = _matching_rule(event, rules)
            if rule:
                report.drops.append(Drop(event.raw_ref, rule["id"], event.summary))
                continue
            duplicate = (
                event.event_type == "log"
                and previous is not None
                and _fingerprint(previous) == _fingerprint(event)
            )
            if duplicate:
                report.drops.append(Drop(event.raw_ref, _DUPLICATE["id"], event.summary))
                continue

        kept.append(event)

    report.kept = len(kept)
    return kept, report
