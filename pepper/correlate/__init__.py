"""Fase 2 — Correlate: evidencia cruda → eventos normalizados, reducidos y correlacionados.

Entrada: un directorio de evidencia con `session.json` y los archivos que
declaran sus colectores. Salida: `events.jsonl`, `flow.json`, `flow.md`,
`reduction.md`, más una copia de `session.json` y de la evidencia cruda en `raw/`
para que el resultado sea autocontenido.

Determinístico: misma evidencia → mismos bytes.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from pepper.correlate.correlate import correlate
from pepper.correlate.events import Event, write_jsonl
from pepper.correlate.parsers import BUILTIN_PARSERS, PatternParser
from pepper.correlate.reduce import reduce_events
from pepper.correlate.render import render_flow, render_reduction
from pepper.profiles import Profile, load_profile
from pepper.session import Session


class MissingParsers(ValueError):
    pass


def resolve_parsers(session: Session, profile: Optional[Profile]) -> Dict[str, Any]:
    parsers: Dict[str, Any] = {}
    missing: List[str] = []
    for collector in session.collectors:
        if collector.source in parsers:
            continue
        if collector.source in BUILTIN_PARSERS:
            parsers[collector.source] = BUILTIN_PARSERS[collector.source]()
            continue
        spec_path = profile.parser_spec_for(collector.source) if profile else None
        if spec_path is None:
            missing.append(collector.source)
        elif not spec_path.is_file():
            raise FileNotFoundError(f"el perfil {profile.id} declara un parser que no existe: {spec_path}")
        else:
            parsers[collector.source] = PatternParser.from_file(spec_path)
    if missing:
        hint = f"el perfil {profile.id}" if profile else "ningún perfil cargado (usa --profile)"
        raise MissingParsers(
            f"sin parser para las fuentes: {', '.join(missing)} — {hint}. "
            "Declara el colector con `parser` en profile.json o usa una fuente genérica."
        )
    return parsers


def _assign_ids(events: List[Event]) -> None:
    width = max(3, len(str(len(events))))
    for index, event in enumerate(events, 1):
        event.event_id = f"E-{index:0{width}d}"


def run(evidence_dir: Path, out_dir: Path, profile_ref: Optional[str] = None, tolerance_ms: int = 500) -> Dict[str, Any]:
    session_path = evidence_dir / "session.json"
    if not session_path.is_file():
        raise FileNotFoundError(f"no existe {session_path}")
    session = Session.load(session_path)

    profile_ref = profile_ref or session.profile_id
    profile = load_profile(profile_ref) if profile_ref else None
    parsers = resolve_parsers(session, profile)

    events: List[Event] = []
    unparsed = []
    source_noise: Dict[str, List[Dict[str, Any]]] = {}
    affinity_keys: List[str] = []
    raw_lines = 0
    for collector in session.collectors:
        path = evidence_dir / collector.file
        if not path.is_file():
            raise FileNotFoundError(f"el colector {collector.source} apunta a un archivo inexistente: {path}")
        raw_lines += sum(1 for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip())
        parser = parsers[collector.source]
        parsed, bad = parser.parse_file(path, collector.file, session)
        for event in parsed:
            event.ingest_order = len(events)
            events.append(event)
        unparsed.extend(bad)
        if parser.noise:
            source_noise[collector.source] = parser.noise
        for key in parser.affinity_keys:
            if key not in affinity_keys:
                affinity_keys.append(key)

    events.sort(key=lambda e: (e.timestamp, e.ingest_order))
    kept, report = reduce_events(events, session, source_noise)
    report.unparsed = unparsed
    _assign_ids(kept)
    flow = correlate(kept, session, affinity_keys, tolerance_ms)

    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(kept, out_dir / "events.jsonl")
    (out_dir / "flow.json").write_text(json.dumps(flow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "flow.md").write_text(render_flow(flow), encoding="utf-8")
    (out_dir / "reduction.md").write_text(render_reduction(report, session, raw_lines), encoding="utf-8")
    shutil.copy2(session_path, out_dir / "session.json")

    raw_dir = out_dir / "raw"
    if raw_dir.exists():
        shutil.rmtree(raw_dir)
    raw_dir.mkdir()
    for collector in session.collectors:
        target = raw_dir / collector.file
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(evidence_dir / collector.file, target)

    return {
        "session_id": session.session_id,
        "profile": profile.id if profile else None,
        "raw_lines": raw_lines,
        "parsed": report.parsed,
        "unparsed": len(unparsed),
        "kept": report.kept,
        "dropped": len(report.drops),
        "traces": len(flow["traces"]),
        "unassigned": len(flow["unassigned"]),
        "out_dir": str(out_dir),
    }
