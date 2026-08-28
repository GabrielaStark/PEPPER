"""Salidas legibles de Correlate: flow.md y reduction.md."""

from __future__ import annotations

from typing import Any, Dict, List

from pepper.correlate.reduce import ReductionReport
from pepper.session import Session

_SAMPLE_LIMIT = 25


def _clock(iso: str) -> str:
    return iso[11:23]


def render_flow(flow: Dict[str, Any]) -> str:
    lines = [
        f"# {flow['session_id']} — {flow['flow_name']}",
        "",
        f"Ventana observada: {_clock(flow['window']['start'])} → {_clock(flow['window']['end'])}",
        f"Eventos: {flow['stats']['events']} · asignados a una petición: {flow['stats']['assigned']} · sin asignar: {flow['stats']['unassigned']}",
        "",
    ]
    for trace in flow["traces"]:
        request = trace["request"]
        head = f"## {trace['correlation_id']}"
        if request.get("method"):
            head += f"  {request['method']} {request['path']}"
        if request.get("status") is not None:
            head += f" → {request['status']}"
        if request.get("duration_ms") is not None:
            head += f"  ({request['duration_ms']} ms)"
        lines += [head, "", "```text"]
        for event in trace["events"]:
            component = (event["component"] or event["source"])[:22].ljust(22)
            lines.append(
                f"{_clock(event['timestamp'])}  {component} {event['summary']}"
                f"\n{'':14}{event['event_id']}  ·  {event['basis']}"
            )
        lines += ["```", ""]

    if flow["unassigned"]:
        lines += ["## Sin asignar a ninguna petición", "", "```text"]
        for item in flow["unassigned"]:
            candidates = f"  candidatos: {', '.join(item['candidates'])}" if item["candidates"] else ""
            lines.append(
                f"{_clock(item['timestamp'])}  {item['source'][:22].ljust(22)} {item['summary']}"
                f"\n{'':14}{item['event_id']}  ·  {item['reason']}{candidates}"
            )
        lines += ["```", ""]
    return "\n".join(lines)


def render_reduction(report: ReductionReport, session: Session, raw_lines: int) -> str:
    dropped = len(report.drops)
    lines = [
        f"# Reducción — {session.session_id}",
        "",
        f"Líneas crudas: {raw_lines} · eventos parseados: {report.parsed} · sin parsear: {len(report.unparsed)}",
        f"Conservados: **{report.kept}** · descartados: {dropped}",
        "",
        "Nunca se descartan: eventos con severidad warn/error/fatal, excepciones, escrituras a base de datos y respuestas HTTP ≥ 400.",
    ]
    if report.protected_outside_window:
        lines.append(
            f"Evidencia protegida fuera de la ventana, conservada y marcada con `metadata.outside_window`: {report.protected_outside_window}."
        )
    lines += ["", "| Regla | Descripción | Alcance | Descartados |", "|---|---|---|---|"]
    for rule in report.rules:
        scope = rule.get("source") or "genérica"
        lines.append(f"| `{rule['id']}` | {rule['description']} | {scope} | {report.count(rule['id'])} |")

    lines += ["", "## Detalle de descartes", ""]
    for rule in report.rules:
        drops = [drop for drop in report.drops if drop.rule_id == rule["id"]]
        if not drops:
            continue
        lines.append(f"### `{rule['id']}` ({len(drops)})")
        lines.append("")
        for drop in drops[:_SAMPLE_LIMIT]:
            lines.append(f"- `{drop.raw_ref}` — {drop.summary}")
        if len(drops) > _SAMPLE_LIMIT:
            lines.append(f"- … y {len(drops) - _SAMPLE_LIMIT} más")
        lines.append("")

    if report.unparsed:
        lines += [f"## Líneas sin parsear ({len(report.unparsed)})", ""]
        lines.append("Se conservan en la evidencia cruda pero no participan en la correlación. Si son relevantes, el parser del perfil necesita cubrirlas.")
        lines.append("")
        for raw_ref, text in report.unparsed[:_SAMPLE_LIMIT]:
            lines.append(f"- `{raw_ref}` — {text[:140]}")
        if len(report.unparsed) > _SAMPLE_LIMIT:
            lines.append(f"- … y {len(report.unparsed) - _SAMPLE_LIMIT} más")
        lines.append("")
    return "\n".join(lines)
