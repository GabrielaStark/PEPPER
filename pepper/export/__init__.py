"""Fase 4 — Export: valida la salida del agente contra el contrato y la publica.

Si la validación falla, no se publica nada: la salida inválida se reporta, nunca
se corrige en silencio.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pepper import SCHEMAS_DIR
from pepper import manifest as evidence_manifest
from pepper.correlate.events import read_jsonl

_EVIDENCE_LISTS = ("steps", "candidate_rules", "queries", "dependencies", "errors", "contradictions")
_DERIVED = {
    "flows.json": ("flow", "components", "steps"),
    "candidate-rules.json": ("flow", "candidate_rules"),
    "contradictions.json": ("flow", "contradictions"),
    "unknowns.json": ("flow", "unknowns"),
    "evidence-map.json": ("flow", "evidence", "queries", "dependencies"),
}
_MAX_SCHEMA_ERRORS = 20


@dataclass
class Report:
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    stats: Dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


def _schema_errors(discovery: Dict[str, Any]) -> Tuple[List[str], Optional[str]]:
    try:
        import jsonschema
    except ImportError:
        # Fail-closed (auditoría H-01): sin validación de forma no hay publicación.
        # D10 ("Export nunca publica inválidos") manda sobre D16 ("sin dependencias").
        return ["jsonschema es obligatorio para Export: pip install jsonschema — sin él no se valida la forma y no se publica"], None
    schema = json.loads((SCHEMAS_DIR / "runtime-discovery.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(discovery), key=lambda e: list(e.absolute_path))
    messages = []
    for error in errors[:_MAX_SCHEMA_ERRORS]:
        where = "/".join(str(part) for part in error.absolute_path) or "(raíz)"
        messages.append(f"schema · {where}: {error.message}")
    if len(errors) > _MAX_SCHEMA_ERRORS:
        messages.append(f"schema · … y {len(errors) - _MAX_SCHEMA_ERRORS} errores más")
    return messages, None


def _raw_line_counts(raw_dir: Path) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    if raw_dir.is_dir():
        for path in raw_dir.rglob("*"):
            if path.is_file():
                relative = path.relative_to(raw_dir).as_posix()
                counts[relative] = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    return counts


def _verify_manifest(package_dir: Path, report: Report, external: Optional[Path]) -> None:
    """La evidencia del paquete debe ser bit a bit la que Correlate produjo (C-02).

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
    manifests = [
        ("manifest externo", external_manifest),
        ("manifest del paquete", internal_manifest),
    ]
    for label, manifest in manifests:
        for error in evidence_manifest.verify(package_dir, manifest, scopes=["evidence", "legacy"]):
            report.errors.append(f"{error} [{label}]")


def validate(package_dir: Path, external_manifest: Optional[Path] = None) -> Tuple[Optional[Dict[str, Any]], Report]:
    report = Report()
    _verify_manifest(package_dir, report, external_manifest)
    output = package_dir / "output" / "runtime-discovery.json"
    if not output.is_file():
        report.errors.append(f"no existe {output}")
        return None, report
    try:
        discovery = json.loads(output.read_text(encoding="utf-8"))
    except ValueError as error:
        report.errors.append(f"runtime-discovery.json no es JSON válido: {error}")
        return None, report
    if not isinstance(discovery, dict):
        report.errors.append("runtime-discovery.json debe ser un objeto JSON")
        return None, report

    schema_errors, schema_warning = _schema_errors(discovery)
    report.errors.extend(schema_errors)
    if schema_warning:
        report.warnings.append(schema_warning)

    session_path = package_dir / "session.json"
    if session_path.is_file():
        session_id = json.loads(session_path.read_text(encoding="utf-8")).get("session_id")
        declared = (discovery.get("flow") or {}).get("session_id")
        if session_id and declared and declared != session_id:
            report.errors.append(f"flow.session_id es {declared!r} pero el paquete es de la sesión {session_id!r}")

    events_path = package_dir / "evidence" / "events.jsonl"
    event_ids = {record.get("event_id") for record in read_jsonl(events_path)} if events_path.is_file() else set()
    if not events_path.is_file():
        report.warnings.append("el paquete no tiene evidence/events.jsonl; no se pudieron verificar los event_id")
    raw_counts = _raw_line_counts(package_dir / "evidence" / "raw")

    evidence_entries = discovery.get("evidence") or []
    evidence_ids: Dict[str, int] = {}
    for index, entry in enumerate(evidence_entries):
        if not isinstance(entry, dict):
            continue
        entry_id = entry.get("id")
        if entry_id in evidence_ids:
            report.errors.append(f"evidence[{index}]: id repetido {entry_id!r}")
        evidence_ids[entry_id] = index
        event_id, raw_ref = entry.get("event_id"), entry.get("raw_ref")
        if not event_id and not raw_ref:
            report.errors.append(f"evidence {entry_id}: sin event_id ni raw_ref — no resuelve a nada")
        if event_id and events_path.is_file() and event_id not in event_ids:
            report.errors.append(f"evidence {entry_id}: event_id {event_id!r} no existe en evidence/events.jsonl")
        if raw_ref:
            file_name, _, line = raw_ref.rpartition(":")
            if not file_name or not line.isdigit():
                report.errors.append(f"evidence {entry_id}: raw_ref {raw_ref!r} no tiene forma archivo:línea")
            elif file_name not in raw_counts:
                report.errors.append(f"evidence {entry_id}: raw_ref apunta a un archivo inexistente en evidence/raw: {file_name}")
            elif not 1 <= int(line) <= raw_counts[file_name]:
                report.errors.append(f"evidence {entry_id}: raw_ref {raw_ref!r} fuera de rango ({raw_counts[file_name]} líneas)")

    referenced = set()
    for key in _EVIDENCE_LISTS:
        for index, item in enumerate(discovery.get(key) or []):
            refs = item.get("evidence") if isinstance(item, dict) else None
            for ref in refs or []:
                referenced.add(ref)
                if ref not in evidence_ids:
                    report.errors.append(f"{key}[{index}]: referencia a evidencia inexistente {ref!r}")
    for index, component in enumerate(discovery.get("components") or []):
        for ref in (component.get("observed_in") if isinstance(component, dict) else None) or []:
            referenced.add(ref)
            if ref not in evidence_ids:
                report.errors.append(f"components[{index}]: referencia a evidencia inexistente {ref!r}")
    unreferenced = [entry_id for entry_id in evidence_ids if entry_id not in referenced]
    if unreferenced:
        report.warnings.append(f"evidencia declarada pero no referenciada por ninguna conclusión: {', '.join(map(str, unreferenced))}")

    if not (package_dir / "output" / "runtime-discovery.md").is_file():
        report.warnings.append("falta output/runtime-discovery.md (la versión legible); se publica solo el JSON")

    report.stats = {
        key: len(discovery.get(key) or [])
        for key in ("components", "steps", "candidate_rules", "queries", "dependencies", "errors", "contradictions", "unknowns", "evidence")
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
        "- El JSON valida contra `schemas/runtime-discovery.schema.json`.",
        "- Toda conclusión referencia evidencia existente; toda evidencia resuelve a un `event_id` de `events.jsonl` o a un `raw_ref` real (archivo:línea).",
        "- Las confianzas están dentro del vocabulario: confirmada, fuertemente_sustentada, candidata, desconocida, contradicha.",
        "- La sesión declarada coincide con la del paquete.",
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


def publish(package_dir: Path, out_dir: Path, external_manifest: Optional[Path] = None) -> Report:
    discovery, report = validate(package_dir, external_manifest)
    output_dir = package_dir / "output"
    if output_dir.is_dir():
        (output_dir / "validation.md").write_text(render_report(report, package_dir), encoding="utf-8")
    if discovery is None or not report.ok:
        return report

    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(output_dir / "runtime-discovery.json", out_dir / "runtime-discovery.json")
    if (output_dir / "runtime-discovery.md").is_file():
        shutil.copy2(output_dir / "runtime-discovery.md", out_dir / "runtime-discovery.md")
    for name, keys in _DERIVED.items():
        derived = {key: discovery.get(key) for key in keys if key in discovery}
        derived["schema_version"] = discovery.get("schema_version")
        (out_dir / name).write_text(json.dumps(derived, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    evidence_out = out_dir / "evidence"
    evidence_out.mkdir(exist_ok=True)
    for name in ("events.jsonl", "flow.json"):
        source = package_dir / "evidence" / name
        if source.is_file():
            shutil.copy2(source, evidence_out / name)
    (out_dir / "validation.md").write_text(render_report(report, package_dir), encoding="utf-8")
    return report
