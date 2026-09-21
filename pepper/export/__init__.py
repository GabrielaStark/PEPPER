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
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    errors = sorted(validator.iter_errors(discovery), key=lambda e: list(e.absolute_path))
    messages = []
    for error in errors[:_MAX_SCHEMA_ERRORS]:
        where = "/".join(str(part) for part in error.absolute_path) or "(raíz)"
        messages.append(f"schema · {where}: {error.message}")
    if len(errors) > _MAX_SCHEMA_ERRORS:
        messages.append(f"schema · … y {len(errors) - _MAX_SCHEMA_ERRORS} errores más")
    return messages


def count_lines(text: str) -> int:
    """Líneas como las cuenta un editor o `wc -l`: solo `\\n` separa (splitlines() partía en
    \\x0c, \\u2028, \\x85 y desfasaba cada raw_ref respecto a lo que el humano ve)."""
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _raw_line_counts(raw_dir: Path) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    if raw_dir.is_dir():
        for path in raw_dir.rglob("*"):
            if path.is_file():
                relative = path.relative_to(raw_dir).as_posix()
                counts[relative] = count_lines(path.read_text(encoding="utf-8", errors="replace"))
    return counts


_LEGACY_DATA_SUFFIXES = {".sql", ".dump", ".backup", ".bak", ".psql", ".dmp", ".gz", ".tar"}
_NOT_A_SOURCE = ("output/", "evidence/", "schemas/", "previous/", "README.md", "CLAUDE.md", "AGENTS.md",
                 "prompt.md", "session.json", "evidence-manifest.json")


def _legacy_ref(package_dir: Path, ref: str) -> Tuple[Optional[Path], Optional[int], Optional[str]]:
    """`legacy/<ruta>[:línea]` → (archivo, línea, error). Solo archivos bajo legacy/: un agente
    que escriba su propio 'respaldo' en output/ y lo cite (o cite la raíz, un directorio,
    el README o el manifest) no sostiene nada."""
    m = re.match(r"^(.+?):(\d+)$", ref)
    path_part, line = (m.group(1), int(m.group(2))) if m else (ref, None)
    if path_part.startswith(_NOT_A_SOURCE) or path_part in (".", "", "/") or path_part.startswith(("/", "..")):
        return None, None, f"{ref!r} no es una fuente: solo `map:<colección>:<nombre>` o un archivo bajo legacy/ (no output/, evidence/, la raíz ni los metadatos del paquete)"
    if not path_part.startswith("legacy/"):
        return None, None, f"{ref!r} no está bajo legacy/ ni es `map:…`"
    candidate = package_dir / path_part
    try:
        candidate.resolve().relative_to(package_dir.resolve())
    except ValueError:
        return None, None, f"{ref!r} sale del paquete"
    if not candidate.is_file():
        return None, None, f"{ref!r} no es un archivo del paquete" + (" (es un directorio)" if candidate.is_dir() else "")
    return candidate, line, None


def _file_lines(path: Path) -> int:
    try:
        return count_lines(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return 0


def _previous_document(package_dir: Path) -> Optional[Dict[str, Any]]:
    path = package_dir / "previous" / OUTPUT_JSON
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


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
                  package_session: Optional[str] = None, declared_sessions: Optional[Set[str]] = None,
                  events_present: bool = False, previous: Optional[Dict[str, Any]] = None) -> None:
    """Cada fuente resuelve a algo que existe en el paquete, según su tipo.

    Una fuente observada de OTRA sesión solo vale si esa sesión y esa misma fuente
    están en `previous/funcional.json` (amarrado por hash): sin previous/ no hay
    contra qué comprobarla, y "ya se verificó antes" era una frase, no un check."""
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
            if previous is None:
                report.errors.append(f"fuente {sid}: cita la sesión {other!r} pero el paquete no trae previous/{OUTPUT_JSON}: no hay contra qué comprobarla")
                return
            prev_sessions = {s.get("session_id") for s in previous.get("sessions") or [] if isinstance(s, dict)}
            if other not in prev_sessions:
                report.errors.append(f"fuente {sid}: la sesión {other!r} no aparece en previous/{OUTPUT_JSON}")
                return
            if not any(isinstance(s, dict) and s.get("kind") == "observado" and str(s.get("ref", "")).strip() == ref
                       for s in previous.get("sources") or []):
                report.errors.append(f"fuente {sid}: {ref!r} no está entre las fuentes observadas del documento anterior")
            return
        if _EVENT_REF_RE.match(ref):
            if events_present and ref not in event_ids:
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
    if kind in ("en_codigo", "en_base", "en_datos", "en_config", "en_doc"):
        m = _MAP_REF_RE.match(ref)
        if m or ref.startswith("map:"):
            if not m:
                report.errors.append(f"fuente {sid}: {ref!r} no tiene la forma map:<colección>:<nombre>")
            elif map_index is None:
                report.errors.append(f"fuente {sid}: cita el mapa ({ref}) pero el paquete no trae map/")
            elif m.group(2) not in map_index.get(m.group(1), set()):
                report.errors.append(f"fuente {sid}: {ref!r} no existe en map/system-map.json")
            return
        path, line, error = _legacy_ref(package_dir, ref)
        if error:
            external = kind in ("en_config", "en_doc") and not ref.startswith(("legacy/", "output/", "evidence/", ".", "/"))
            if external:
                # un manual o una configuración que no viene en el paquete: se acepta SOLO descrita
                if str(entry.get("description") or "").strip():
                    report.warnings.append(f"fuente {sid}: {ref!r} no está en el paquete; se acepta como cita externa descrita")
                else:
                    report.errors.append(f"fuente {sid}: {ref!r} no está en el paquete; una cita externa necesita `description` (qué documento es y dónde está)")
                return
            report.errors.append(f"fuente {sid}: {error}")
            return
        if kind in ("en_base", "en_datos") and path.suffix.lower() not in _LEGACY_DATA_SUFFIXES:
            report.errors.append(f"fuente {sid}: una fuente {kind} es `map:<colección>:<nombre>` o un respaldo bajo legacy/ (.sql, .dump…); {ref!r} no lo es")
            return
        if line is not None:
            total = _file_lines(path)
            if not 1 <= line <= total:
                report.errors.append(f"fuente {sid}: {ref!r} fuera de rango ({total} líneas)")
        return
    if kind == "humano":
        if not str(entry.get("description") or "").strip():
            report.errors.append(f"fuente {sid}: una fuente humana lleva `description` con el nombre o el rol de quien lo dijo")
        return


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


_OBSERVED_TAG_RE = re.compile(r"\[observado\s+([A-Za-z0-9_.-]+)\]")
_SECTION_RE = re.compile(r"^##\s+(\d{1,2})\.[^\n]*\n(.*?)(?=^##\s+\d{1,2}\.|\Z)", re.M | re.S)
_WORD_RE = re.compile(r"[0-9a-záéíóúüñ]+")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("`", "").replace("*", "")).strip().lower()


def _mentioned(name: str, normalized_text: str) -> bool:
    """Un nombre del JSON aparece en el .md: literal, o todas sus palabras significativas.

    `PostgreSQL (base solicitudes)` puede salir en el .md como «PostgreSQL … base … solicitudes»;
    lo que no se admite es que el nombre no esté en absoluto."""
    target = _normalize(name)
    if not target:
        return True
    if target in normalized_text:
        return True
    words = [w for w in _WORD_RE.findall(target) if len(w) >= 3]
    return bool(words) and all(w in normalized_text for w in words)


def _check_markdown_matches(discovery: Dict[str, Any], text: str, declared: Set[str], report: Report) -> None:
    """El .md corresponde al JSON validado: comprobación determinística de cobertura.

    El JSON es la espina validada (schema, fuentes, manifest); el .md es lo que la persona
    lee. Export no puede juzgar prosa, pero sí exigir que todo lo que el JSON afirma que
    existe —cada rol, capacidad, recorrido, estado, automatismo, integración, reporte,
    catálogo, sesión y desconocido— esté nombrado en el .md, y que el .md no cite
    sesiones que el JSON no declara. Un .md contradictorio en lo nombrable no pasa; uno
    inventado en lo demás sigue siendo trabajo del humano que revisa (revisión 2026-09-21)."""
    normalized = _normalize(text)
    sections: Dict[int, str] = {int(n): body for n, body in _SECTION_RE.findall(text)}

    system_name = str((discovery.get("system") or {}).get("name") or "").strip()
    title = next((line for line in text.splitlines() if line.startswith("# ")), "")
    if system_name and not _mentioned(system_name, _normalize(title)):
        report.errors.append(f"{OUTPUT_MD}: el título no nombra el sistema del JSON ({system_name!r}): {title.strip()!r}")

    def expect(label: str, names, where: str = "") -> None:
        scope = _normalize(sections.get(int(where), "")) if where else normalized
        for name in names:
            name = str(name or "").strip()
            if name and not _mentioned(name, scope):
                place = f"en la sección {where}" if where else "en ningún lado"
                report.errors.append(f"{OUTPUT_MD}: {label} {name!r} está en el JSON y no aparece {place} del .md")

    def names(key: str, field: str):
        return [item.get(field) for item in discovery.get(key) or [] if isinstance(item, dict)]

    expect("el rol", names("actors", "name"))
    expect("la capacidad", names("permissions", "capability"))
    expect("el recorrido", names("journeys", "name"))
    for cycle in discovery.get("states") or []:
        if isinstance(cycle, dict):
            expect("el estado", [st.get("name") for st in cycle.get("states") or [] if isinstance(st, dict)])
    expect("lo automático", names("automation", "name"))
    expect("el sistema externo", names("integrations", "name"))
    expect("el reporte", names("reports", "name"))
    expect("el catálogo", names("catalogs", "name"))
    expect("la sesión", sorted(s for s in declared if s))

    unknowns = [u.get("question") for u in discovery.get("unknowns") or [] if isinstance(u, dict)]
    if 12 in sections:
        expect("el desconocido", unknowns, where="12")
        numbered = len(re.findall(r"^\s*\d+\.\s", sections[12], flags=re.M))
        if numbered < len(unknowns):
            report.errors.append(
                f"{OUTPUT_MD}: la sección 12 numera {numbered} desconocido(s) y el JSON declara {len(unknowns)}")
    else:
        expect("el desconocido", unknowns)

    for cited in sorted(set(_OBSERVED_TAG_RE.findall(text))):
        if cited not in declared:
            report.errors.append(f"{OUTPUT_MD}: cita [observado {cited}] y esa sesión no está en `sessions` del JSON")


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
        if session_id and session_id not in declared:
            report.errors.append(f"sessions no incluye la sesión de este paquete ({session_id!r}); declaradas: {sorted(declared)}")
    previous = _previous_document(package_dir)

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
        _check_source(entry, package_dir, event_ids, raw_counts, map_index, report, session_id, declared,
                      events_present=events_path.is_file(), previous=previous)

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
    md_path = package_dir / "output" / OUTPUT_MD
    if not md_path.is_file():
        report.errors.append(f"falta output/{OUTPUT_MD}: el documento legible ES el entregable")
    else:
        # El .md es lo que el humano lee: doce secciones fijas y contenido de verdad, no una línea.
        text = md_path.read_text(encoding="utf-8", errors="replace")
        found = {int(n) for n in re.findall(r"^##\s+(\d{1,2})\.", text, flags=re.M)}
        missing = sorted(set(range(1, 13)) - found)
        if missing:
            report.errors.append(f"{OUTPUT_MD}: faltan las secciones {', '.join(map(str, missing))} de las doce fijas (## N. …)")
        if len(text.split()) < 300:
            report.errors.append(f"{OUTPUT_MD}: {len(text.split())} palabras no son el documento de un sistema")
        _check_markdown_matches(discovery, text, declared, report)
    if previous is not None:
        # Acumulativo de verdad: lo anterior no desaparece en silencio.
        prev_sessions = {s.get("session_id") for s in previous.get("sessions") or [] if isinstance(s, dict)}
        lost_sessions = sorted(s for s in prev_sessions if s and s not in declared)
        if lost_sessions:
            report.errors.append(f"sessions perdió sesiones del documento anterior: {', '.join(lost_sessions)}")
        ids_now = {r.get("id") for r in discovery.get("rules") or [] if isinstance(r, dict)}
        lost_rules = sorted(r.get("id") for r in previous.get("rules") or [] if isinstance(r, dict) and r.get("id") not in ids_now)
        if lost_rules:
            report.warnings.append(f"reglas del documento anterior que desaparecieron: {', '.join(map(str, lost_rules))} — si dejaron de ser ciertas, van a contradictions")
        q_now = {str(u.get("question", "")).strip() for u in discovery.get("unknowns") or [] if isinstance(u, dict)}
        lost_unknowns = [str(u.get("question", "")).strip()[:60] for u in previous.get("unknowns") or []
                         if isinstance(u, dict) and str(u.get("question", "")).strip() not in q_now]
        if lost_unknowns:
            report.warnings.append(f"desconocidos del documento anterior que desaparecieron sin resolverse a la vista: {'; '.join(lost_unknowns)}")

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
        "- El `.md` corresponde al JSON: nombra cada rol, capacidad, recorrido, estado, automatismo, integración, "
        "reporte, catálogo y sesión del JSON; la sección 12 lleva cada desconocido; ningún `[observado <sesión>]` "
        "cita una sesión que el JSON no declare. (Cobertura de lo nombrable: la prosa la revisa una persona.)",
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
        # La entrega a stark que el README promete y nadie escribía: docs/analysis/funcional.md
        analysis_dir = system_doc_dir.parent / "analysis"
        analysis_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(output_dir / OUTPUT_MD, analysis_dir / OUTPUT_MD)
    return report
