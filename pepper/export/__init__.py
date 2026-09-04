"""Fase — Export: valida la salida del agente contra el contrato y la publica.

Si la validación falla, no se publica nada: la salida inválida se reporta, nunca
se corrige en silencio. Publica dos cosas: la salida de ESTA sesión
(`<out>/funcional.json|md` + `validation.md`) y el documento del SISTEMA
(`<system-doc>/funcional.json|md`), que es el mismo contenido: el discovery es
acumulativo, así que la última sesión válida es el documento vigente.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from pepper import SCHEMAS_DIR
from pepper import manifest as evidence_manifest
from pepper.correlate.events import read_jsonl

SCHEMA_NAME = "functional-discovery.schema.json"
OUTPUT_JSON = "funcional.json"
OUTPUT_MD = "funcional.md"
_MAX_SCHEMA_ERRORS = 20
# Qué colecciones llevan `sources` (directas o dentro de sus elementos anidados).
_SOURCED = ("actors", "permissions", "journeys", "states", "rules", "automation", "integrations",
            "reports", "catalogs", "volumes", "contradictions")
_MAP_REF_RE = re.compile(r"^map:(entrypoints|jobs|external_dependencies|data_stores|catalogs|distributions|classes|screens):(.+)$")
_RAW_REF_RE = re.compile(r"^(.+):(\d+)$")
_EVENT_REF_RE = re.compile(r"^E-\d+$")


@dataclass
class Report:
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    stats: Dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


def _schema_errors(discovery: Dict[str, Any]) -> List[str]:
    try:
        import jsonschema
    except ImportError:
        # Fail-closed: sin validación de forma no hay publicación.
        return ["jsonschema es obligatorio para Export: pip install jsonschema — sin él no se valida la forma y no se publica"]
    schema = json.loads((SCHEMAS_DIR / SCHEMA_NAME).read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(discovery), key=lambda e: list(e.absolute_path))
    messages = []
    for error in errors[:_MAX_SCHEMA_ERRORS]:
        where = "/".join(str(part) for part in error.absolute_path) or "(raíz)"
        messages.append(f"schema · {where}: {error.message}")
    if len(errors) > _MAX_SCHEMA_ERRORS:
        messages.append(f"schema · … y {len(errors) - _MAX_SCHEMA_ERRORS} errores más")
    return messages


def _raw_line_counts(raw_dir: Path) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    if raw_dir.is_dir():
        for path in raw_dir.rglob("*"):
            if path.is_file():
                relative = path.relative_to(raw_dir).as_posix()
                counts[relative] = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    return counts


def _verify_manifest(package_dir: Path, report: Report, external: Optional[Path]) -> None:
    """La evidencia, el mapa y el legacy del paquete deben ser bit a bit lo que Package copió.

    El manifest interno detecta modificación y fabricación dentro del paquete;
    uno externo (--manifest, conservado fuera del alcance del agente) protege
    además contra la edición del manifest mismo.
    """
    internal = package_dir / evidence_manifest.MANIFEST_NAME
    if not internal.is_file():
        report.errors.append(
            f"el paquete no tiene {evidence_manifest.MANIFEST_NAME}: sin manifest no hay integridad de evidencia — re-empaqueta con `pepper package`")
        return
    try:
        internal_manifest = evidence_manifest.load(internal)
    except ValueError as error:
        report.errors.append(str(error))
        return
    if external is None:
        report.errors.append(
            "falta el manifest externo: usa --manifest <package.evidence-manifest.json>; "
            "el manifest interno está al alcance del agente y no es una raíz de confianza"
        )
        return
    try:
        external.resolve().relative_to(package_dir.resolve())
    except ValueError:
        pass
    else:
        report.errors.append("el manifest externo debe estar FUERA del paquete y del directorio de trabajo del agente")
        return
    try:
        external_manifest = evidence_manifest.load(external)
    except (OSError, ValueError) as error:
        report.errors.append(f"manifest externo ilegible: {error}")
        return
    if internal_manifest != external_manifest:
        report.errors.append("el manifest interno no coincide con el manifest externo conservado fuera del paquete")
    for label, manifest in (("manifest externo", external_manifest), ("manifest del paquete", internal_manifest)):
        for error in evidence_manifest.verify(package_dir, manifest, scopes=["evidence", "legacy", "map", "previous"]):
            report.errors.append(f"{error} [{label}]")


def _map_index(package_dir: Path) -> Optional[Dict[str, Set[str]]]:
    """Nombres referenciables del mapa, por colección — o None si el paquete no trae mapa."""
    path = package_dir / "map" / "system-map.json"
    if not path.is_file():
        return None
    try:
        system_map = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    index: Dict[str, Set[str]] = {
        "entrypoints": {e.get("path", "") for e in system_map.get("entrypoints", [])},
        "jobs": {j.get("name", "") for j in system_map.get("jobs", [])},
        "external_dependencies": {d.get("name", "") for d in system_map.get("external_dependencies", [])},
        "data_stores": {d.get("name", "") for d in system_map.get("data_stores", [])},
        "catalogs": {c.get("table", "") for c in system_map.get("catalogs", [])},
        "distributions": {f"{d.get('table')}.{d.get('column')}" for d in system_map.get("distributions", [])},
        "classes": {c.get("name", "") for c in system_map.get("classes", [])},
        "screens": {s.get("path", "") for s in system_map.get("screens", [])},
    }
    # una clase se puede citar por nombre simple; una pantalla por su archivo
    index["classes"] |= {name.rsplit(".", 1)[-1] for name in index["classes"]}
    index["screens"] |= {path.rsplit("/", 1)[-1] for path in index["screens"]}
    return index


def _check_source(entry: Dict[str, Any], package_dir: Path, event_ids: Set[str], raw_counts: Dict[str, int],
                  map_index: Optional[Dict[str, Set[str]]], report: Report,
                  package_session: Optional[str] = None, declared_sessions: Optional[Set[str]] = None) -> None:
    """Cada fuente resuelve a algo que existe en el paquete, según su tipo.

    Una fuente observada de OTRA sesión (el documento es acumulativo) ya se
    verificó cuando esa sesión se exportó: aquí solo se exige que la sesión esté
    declarada en `sessions`. Lo de esta sesión se verifica contra la evidencia."""
    sid, kind, ref = entry.get("id"), entry.get("kind"), str(entry.get("ref", "")).strip()
    if not ref:
        report.errors.append(f"fuente {sid}: ref vacío")
        return
    if kind == "observado":
        other = entry.get("session_id")
        if other and package_session and other != package_session:
            if declared_sessions is not None and other not in declared_sessions:
                report.errors.append(f"fuente {sid}: cita la sesión {other!r}, que no está declarada en sessions")
            return
        if _EVENT_REF_RE.match(ref):
            if event_ids and ref not in event_ids:
                report.errors.append(f"fuente {sid}: event_id {ref!r} no existe en evidence/events.jsonl")
            return
        m = _RAW_REF_RE.match(ref)
        if not m:
            report.errors.append(f"fuente {sid}: una fuente observada es un event_id (E-0001) o archivo:línea de evidence/raw, no {ref!r}")
            return
        file_name, line = m.group(1), int(m.group(2))
        if file_name not in raw_counts:
            report.errors.append(f"fuente {sid}: raw_ref apunta a un archivo inexistente en evidence/raw: {file_name}")
        elif not 1 <= line <= raw_counts[file_name]:
            report.errors.append(f"fuente {sid}: raw_ref {ref!r} fuera de rango ({raw_counts[file_name]} líneas)")
        return
    if kind in ("en_codigo", "en_base", "en_datos"):
        m = _MAP_REF_RE.match(ref)
        if m:
            if map_index is None:
                report.errors.append(f"fuente {sid}: cita el mapa ({ref}) pero el paquete no trae map/")
            elif m.group(2) not in map_index.get(m.group(1), set()):
                report.errors.append(f"fuente {sid}: {ref!r} no existe en map/system-map.json")
            return
        if _exists_in_package(package_dir, ref):
            return
        report.errors.append(
            f"fuente {sid}: una fuente {kind} es `map:<colección>:<nombre>` (clase, pantalla, tabla, catálogo, "
            f"distribución tabla.columna, job, ruta) o un archivo del paquete (legacy/…[:línea]); {ref!r} no resuelve")
        return
    if kind in ("en_config", "en_doc"):
        if ref.startswith("map:"):
            m = _MAP_REF_RE.match(ref)
            if not m or map_index is None or m.group(2) not in map_index.get(m.group(1), set()):
                report.errors.append(f"fuente {sid}: {ref!r} no existe en el mapa")
        elif not _exists_in_package(package_dir, ref):
            report.warnings.append(f"fuente {sid}: {ref!r} no es un archivo del paquete; se acepta como cita externa")
        return
    # humano: se cita con nombre o rol; no es verificable por máquina


def _exists_in_package(package_dir: Path, ref: str) -> bool:
    path_part = ref
    m = _RAW_REF_RE.match(ref)
    if m and not Path(ref).exists():
        path_part = m.group(1)
    candidate = (package_dir / path_part)
    try:
        candidate.resolve().relative_to(package_dir.resolve())
    except ValueError:
        return False
    return candidate.is_file() or candidate.is_dir()


def _iter_sourced(discovery: Dict[str, Any]):
    """(ubicación, lista de refs) de toda entrada que declara `sources`, incluidas las anidadas."""
    summary = discovery.get("summary") or {}
    if isinstance(summary, dict):
        yield "summary", summary.get("sources") or []
    for key in _SOURCED:
        for index, item in enumerate(discovery.get(key) or []):
            if not isinstance(item, dict):
                continue
            yield f"{key}[{index}]", item.get("sources") or []
            for sub in ("steps", "transitions"):
                for j, nested in enumerate(item.get(sub) or []):
                    if isinstance(nested, dict) and nested.get("sources"):
                        yield f"{key}[{index}].{sub}[{j}]", nested["sources"]


def validate(package_dir: Path, external_manifest: Optional[Path] = None) -> Tuple[Optional[Dict[str, Any]], Report]:
    report = Report()
    _verify_manifest(package_dir, report, external_manifest)
    output = package_dir / "output" / OUTPUT_JSON
    if not output.is_file():
        report.errors.append(f"no existe {output}")
        return None, report
    try:
        discovery = json.loads(output.read_text(encoding="utf-8"))
    except ValueError as error:
        report.errors.append(f"{OUTPUT_JSON} no es JSON válido: {error}")
        return None, report
    if not isinstance(discovery, dict):
        report.errors.append(f"{OUTPUT_JSON} debe ser un objeto JSON")
        return None, report

    report.errors.extend(_schema_errors(discovery))

    session_path = package_dir / "session.json"
    session_id = None
    declared = {s.get("session_id") for s in discovery.get("sessions") or [] if isinstance(s, dict)}
    if session_path.is_file():
        session_id = json.loads(session_path.read_text(encoding="utf-8")).get("session_id")
        if session_id and declared and session_id not in declared:
            report.errors.append(f"sessions no incluye la sesión de este paquete ({session_id!r}); declaradas: {sorted(declared)}")

    events_path = package_dir / "evidence" / "events.jsonl"
    event_ids = {record.get("event_id") for record in read_jsonl(events_path)} if events_path.is_file() else set()
    if not events_path.is_file():
        report.warnings.append("el paquete no tiene evidence/events.jsonl; no se pudieron verificar los event_id")
    raw_counts = _raw_line_counts(package_dir / "evidence" / "raw")
    map_index = _map_index(package_dir)

    sources = discovery.get("sources") or []
    source_ids: Dict[str, int] = {}
    for index, entry in enumerate(sources):
        if not isinstance(entry, dict):
            continue
        sid = entry.get("id")
        if sid in source_ids:
            report.errors.append(f"sources[{index}]: id repetido {sid!r}")
        source_ids[sid] = index
        _check_source(entry, package_dir, event_ids, raw_counts, map_index, report, session_id, declared)

    referenced: Set[str] = set()
    for where, refs in _iter_sourced(discovery):
        for ref in refs:
            referenced.add(ref)
            if ref not in source_ids:
                report.errors.append(f"{where}: referencia a una fuente inexistente {ref!r}")
    unreferenced = [sid for sid in source_ids if sid not in referenced]
    if unreferenced:
        report.warnings.append(f"fuentes declaradas que ninguna afirmación usa: {', '.join(map(str, unreferenced))}")

    observed_kinds = {s.get("kind") for s in sources if isinstance(s, dict)}
    if event_ids and "observado" not in observed_kinds:
        report.warnings.append("ninguna fuente es 'observado' aunque el paquete trae evidencia de ejecución")
    if not discovery.get("unknowns"):
        report.errors.append("unknowns está vacío: en un legacy siempre hay algo que no se sabe; decláralo")
    if not (package_dir / "output" / OUTPUT_MD).is_file():
        report.errors.append(f"falta output/{OUTPUT_MD}: el documento legible ES el entregable")

    report.stats = {
        key: len(discovery.get(key) or [])
        for key in ("actors", "permissions", "journeys", "states", "rules", "automation", "integrations",
                    "reports", "catalogs", "volumes", "contradictions", "unknowns", "sources")
    }
    return discovery, report


def render_report(report: Report, package_dir: Path, published: bool = True) -> str:
    lines = [f"# Validación de export — {package_dir.name}", ""]
    if report.ok:
        lines.append("**Resultado: publicado.**" if published else "**Resultado: válido** (comprobación sin publicar).")
    else:
        lines.append(f"**Resultado: RECHAZADO** ({len(report.errors)} errores). No se publicó nada.")
    lines.append("")
    if report.errors:
        lines += ["## Errores", ""] + [f"- {error}" for error in report.errors] + [""]
    if report.warnings:
        lines += ["## Avisos", ""] + [f"- {warning}" for warning in report.warnings] + [""]
    if report.stats:
        lines += ["## Contenido", "", "| Sección | Elementos |", "|---|---|"]
        lines += [f"| {key} | {value} |" for key, value in report.stats.items()]
        lines.append("")
    lines += [
        "## Reglas aplicadas",
        "",
        f"- El JSON valida contra `schemas/{SCHEMA_NAME}`.",
        "- Toda afirmación cita fuentes declaradas; toda fuente resuelve según su tipo: observado → `event_id` o archivo:línea de la evidencia; "
        "en código/base/datos → un elemento del mapa (`map:<colección>:<nombre>`) o un archivo del paquete.",
        "- La evidencia, el mapa, el legacy y el discovery anterior conservan sus hashes (manifest interno = externo).",
        "- La sesión del paquete aparece en `sessions`; hay desconocidos declarados; existe el `.md` legible.",
        "",
    ]
    return "\n".join(lines)


def check(package_dir: Path, external_manifest: Optional[Path] = None) -> Report:
    """Solo valida (y deja output/validation.md); no publica. Para que el agente se auto-verifique."""
    _, report = validate(package_dir, external_manifest)
    output_dir = package_dir / "output"
    if output_dir.is_dir():
        (output_dir / "validation.md").write_text(render_report(report, package_dir, published=False), encoding="utf-8")
    return report


def publish(package_dir: Path, out_dir: Path, external_manifest: Optional[Path] = None,
            system_doc_dir: Optional[Path] = None) -> Report:
    discovery, report = validate(package_dir, external_manifest)
    output_dir = package_dir / "output"
    if output_dir.is_dir():
        (output_dir / "validation.md").write_text(render_report(report, package_dir), encoding="utf-8")
    if discovery is None or not report.ok:
        return report

    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(output_dir / OUTPUT_JSON, out_dir / OUTPUT_JSON)
    shutil.copy2(output_dir / OUTPUT_MD, out_dir / OUTPUT_MD)
    (out_dir / "validation.md").write_text(render_report(report, package_dir), encoding="utf-8")
    if system_doc_dir is not None:
        system_doc_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(output_dir / OUTPUT_JSON, system_doc_dir / OUTPUT_JSON)
        shutil.copy2(output_dir / OUTPUT_MD, system_doc_dir / OUTPUT_MD)
    return report
