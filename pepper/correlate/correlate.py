"""Correlación: agrupa eventos por petición y registra qué clave sustenta cada enlace.

Prioridad de claves, de más fuerte a más débil:
  1. correlation_id explícito (normalmente inyectado por el proxy de PEPPER)
  2. afinidad (thread, pid, ...) dentro de la ventana temporal de una petición
  3. ventana temporal sola
Lo que no se puede asignar con confianza queda como `unassigned`, con la razón.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

from pepper.correlate.events import Event
from pepper.session import Session

FLOW_SCHEMA_VERSION = "0.1.0"


@dataclass
class Trace:
    correlation_id: str
    start: datetime
    end: datetime
    open_ended: bool
    events: List[Event] = field(default_factory=list)
    basis: Dict[str, str] = field(default_factory=dict)
    affinity: Dict[str, Set[str]] = field(default_factory=dict)

    def contains(self, moment: datetime, tolerance: timedelta) -> Optional[str]:
        if self.start <= moment <= self.end:
            return "ventana temporal"
        if self.start - tolerance <= moment <= self.end + tolerance:
            return f"ventana temporal (±{int(tolerance.total_seconds() * 1000)} ms)"
        return None

    def matching_affinity(self, event: Event, keys: List[str]) -> Optional[str]:
        for key in keys:
            value = event.metadata.get(key)
            if value is not None and str(value) in self.affinity.get(key, set()):
                return f"{key} {value}"
        return None

    def learn_affinity(self, event: Event, keys: List[str]) -> None:
        for key in keys:
            value = event.metadata.get(key)
            if value is not None:
                self.affinity.setdefault(key, set()).add(str(value))

    def assign(self, event: Event, basis: str, keys: List[str]) -> None:
        self.events.append(event)
        self.basis[event.raw_ref] = basis
        self.learn_affinity(event, keys)

    def request_summary(self) -> Dict[str, Any]:
        request = next((e for e in self.events if e.event_type == "http_request"), None)
        response = next((e for e in reversed(self.events) if e.event_type == "http_response"), None)
        summary: Dict[str, Any] = {}
        if request:
            summary["method"] = request.metadata.get("method")
            summary["path"] = request.metadata.get("path")
        if response:
            summary["status"] = response.metadata.get("status")
            if "duration_ms" in response.metadata:
                summary["duration_ms"] = response.metadata["duration_ms"]
        summary["started"] = self.start.isoformat(timespec="milliseconds")
        summary["ended"] = None if self.open_ended else self.end.isoformat(timespec="milliseconds")
        return summary


def _anchor_traces(events: List[Event], session: Session) -> Dict[str, Trace]:
    traces: Dict[str, Trace] = {}
    for event in events:
        if not event.correlation_id:
            continue
        trace = traces.get(event.correlation_id)
        if trace is None:
            traces[event.correlation_id] = Trace(
                correlation_id=event.correlation_id,
                start=event.timestamp,
                end=event.timestamp,
                open_ended=True,
            )
            trace = traces[event.correlation_id]
        trace.start = min(trace.start, event.timestamp)
        trace.end = max(trace.end, event.timestamp)
        if event.event_type == "http_response":
            trace.open_ended = False
    for trace in traces.values():
        if trace.open_ended:
            trace.end = session.observed_end
    return traces


def correlate(
    events: List[Event],
    session: Session,
    affinity_keys: List[str],
    tolerance_ms: int = 500,
) -> Dict[str, Any]:
    tolerance = timedelta(milliseconds=tolerance_ms)
    traces = _anchor_traces(events, session)
    ordered_traces = sorted(traces.values(), key=lambda t: (t.start, t.correlation_id))
    unassigned: List[Dict[str, Any]] = []

    for event in events:
        if event.correlation_id and event.correlation_id in traces:
            traces[event.correlation_id].assign(event, "correlation_id", affinity_keys)
            continue

        candidates = []
        for trace in ordered_traces:
            basis = trace.contains(event.timestamp, tolerance)
            if basis:
                candidates.append((trace, basis))

        if not candidates:
            unassigned.append(_unassigned(event, "fuera de toda petición observada", []))
            continue

        if len(candidates) == 1:
            trace, basis = candidates[0]
            affinity = trace.matching_affinity(event, affinity_keys)
            basis = f"{basis} + {affinity}" if affinity else basis
            trace.assign(event, basis, affinity_keys)
            _mark_inferred(event, trace.correlation_id, basis)
            continue

        by_affinity = [
            (trace, trace.matching_affinity(event, affinity_keys))
            for trace, _ in candidates
        ]
        by_affinity = [(trace, affinity) for trace, affinity in by_affinity if affinity]
        if len(by_affinity) == 1:
            trace, affinity = by_affinity[0]
            basis = f"{affinity} (ventanas concurrentes)"
            trace.assign(event, basis, affinity_keys)
            _mark_inferred(event, trace.correlation_id, basis)
            continue

        unassigned.append(_unassigned(
            event,
            f"ambiguo: {len(candidates)} peticiones concurrentes y sin afinidad que lo resuelva",
            [trace.correlation_id for trace, _ in candidates],
        ))

    assigned = sum(len(trace.events) for trace in ordered_traces)
    return {
        "schema_version": FLOW_SCHEMA_VERSION,
        "session_id": session.session_id,
        "flow_name": session.flow_name,
        "window": {
            "start": session.observed_start.isoformat(timespec="milliseconds"),
            "end": session.observed_end.isoformat(timespec="milliseconds"),
        },
        "traces": [_trace_dict(trace) for trace in ordered_traces],
        "unassigned": unassigned,
        "stats": {"events": len(events), "assigned": assigned, "unassigned": len(unassigned)},
    }


def _mark_inferred(event: Event, correlation_id: str, basis: str) -> None:
    event.metadata["inferred_correlation_id"] = correlation_id
    event.metadata["correlation_basis"] = basis


def _trace_dict(trace: Trace) -> Dict[str, Any]:
    ordered = sorted(trace.events, key=lambda e: (e.timestamp, e.ingest_order))
    return {
        "correlation_id": trace.correlation_id,
        "request": trace.request_summary(),
        "events": [
            {
                "event_id": event.event_id,
                "timestamp": event.timestamp.isoformat(timespec="milliseconds"),
                "source": event.source,
                "component": event.component,
                "event_type": event.event_type,
                "summary": event.summary,
                "basis": trace.basis[event.raw_ref],
            }
            for event in ordered
        ],
    }


def _unassigned(event: Event, reason: str, candidates: List[str]) -> Dict[str, Any]:
    return {
        "event_id": event.event_id,
        "timestamp": event.timestamp.isoformat(timespec="milliseconds"),
        "source": event.source,
        "summary": event.summary,
        "reason": reason,
        "candidates": candidates,
    }
