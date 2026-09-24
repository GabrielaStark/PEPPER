"""Fase — Package: arma el paquete controlado, la carpeta autocontenida sobre la que trabaja el agente.

Contenido:
  README.md, CLAUDE.md, AGENTS.md   puertas de entrada (todas llevan a prompt.md)
  prompt.md                          la skill discovery-funcional, sin frontmatter
  session.json
  evidence/                          events.jsonl, flow.json, flow.md, reduction.md, raw/
  map/                               lo que el sistema ES: system-map.json + surface/db/catalogs/screens/code.md
  previous/                          funcional.json del discovery anterior, si lo hay (se extiende, no se repite)
  legacy/                            artefactos del legacy (source, configuration, docs, ...)
  schemas/functional-discovery.schema.json
  output/                            aquí escribe el agente: funcional.json y funcional.md
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from pepper import SCHEMAS_DIR, SKILLS_DIR
from pepper import manifest as evidence_manifest
from pepper.sensitive import summarize as summarize_sensitive
from pepper.workspace import is_tool_path, tool_paths

_EVIDENCE_FILES = ("events.jsonl", "flow.json", "flow.md", "reduction.md")
from pepper.sensitive import IGNORED_DIRS, IGNORED_SUFFIXES

# Lo que se copia se escanea y lo que no se escanea no se copia: la misma lista que el escáner.
_LEGACY_IGNORE = shutil.ignore_patterns(*IGNORED_DIRS, *[f"*{suffix}" for suffix in IGNORED_SUFFIXES], ".DS_Store")
SCHEMA_NAME = "functional-discovery.schema.json"
OUTPUT_JSON = "funcional.json"
OUTPUT_MD = "funcional.md"


def _legacy_ignore(legacy_dir: Path):
    """Ignora build/VCS y, si PEPPER está instalado encima del repo del legacy, su propia herramienta."""
    tool = tool_paths(legacy_dir)

    def ignore(directory: str, names: List[str]) -> List[str]:
        ignored = set(_LEGACY_IGNORE(directory, names))
        ignored.update(name for name in names if is_tool_path(Path(directory) / name, tool))
        if Path(directory).resolve() == legacy_dir.resolve():
            ignored.update(name for name in names if name.startswith("."))
            ignored.update({"pepper-out", "evidence", "legacy"})
        return sorted(ignored)

    return ignore


DISCOVERY_SKILL = SKILLS_DIR / "discovery-funcional" / "SKILL.md"


def strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    return parts[2].lstrip("\n") if len(parts) == 3 else text


def _adapter(has_map: bool, has_previous: bool, data_mode: str) -> str:
    lines = [
        "# Instrucciones para el agente",
        "",
        "Lee `prompt.md` y síguelo al pie de la letra.",
        "",
    ]
    if has_map:
        lines += ["Hay mapa del sistema en `map/`: es tu fuente principal para roles, pantallas, estados, "
                  "catálogos, reglas en la base y jobs. La evidencia de `evidence/` confirma lo que se vio ejecutar.", ""]
    else:
        lines += ["No hay mapa del sistema (`map/`): solo tienes la ejecución. Declara en `unknowns` todo lo que un mapa habría respondido.", ""]
    if has_previous:
        lines += ["Hay un discovery anterior en `previous/funcional.json`: **extiéndelo**. Conserva lo que sigue siendo cierto, "
                  "corrige lo que esta sesión contradiga (y dilo en `contradictions`), y agrega lo nuevo.", ""]
    lines += [
        "Trabajas en modo **solo lectura** sobre este paquete. Tu único destino de escritura es `output/`:",
        f"`output/{OUTPUT_JSON}` (válido contra `schemas/{SCHEMA_NAME}`) y `output/{OUTPUT_MD}`.",
        "",
    ]
    if data_mode == "local":
        lines += [
            "**Frontera obligatoria:** este paquete fue clasificado para análisis LOCAL.",
            "No lo abras con Claude Code, Codex ni otro agente que envíe contenido a un modelo remoto.",
            "",
        ]
    return "\n".join(lines)


def _readme(session: Dict[str, Any], flow: Dict[str, Any], legacy_dirs: List[str],
            data_mode: str, sensitive_count: int, unscanned_count: int,
            overrides: List[str], has_map: bool, has_previous: bool) -> str:
    stats = flow.get("stats", {})
    lines = [
        f"# Paquete controlado — {session.get('session_id')}",
        "",
        f"Flujo observado: **{session.get('flow_name', session.get('session_id'))}**",
        f"Ventana: {session.get('observed_start')} → {session.get('observed_end')}",
        "",
        "Empieza por `prompt.md` (`CLAUDE.md` y `AGENTS.md` llevan ahí).",
        "",
        "> ⚠️ **Frontera de datos**: este paquete contiene evidencia y artefactos del legacy",
        "> (potencialmente datos personales y credenciales por ubicación). Analizarlo con un",
        "> agente en la nube (Claude Code/Codex con modelo remoto) implica **procesamiento",
        "> externo**. Esa decisión es del humano responsable del dato, no del agente.",
        "",
        "## Clasificación de datos",
        "",
        f"- Modo autorizado al crear el paquete: **{data_mode}**",
        f"- Hallazgos sensibles detectados por ubicación: **{sensitive_count}**",
        f"- Archivos no inspeccionables automáticamente: **{unscanned_count}**",
        f"- Autorización de una persona: **{', '.join(overrides) if overrides else 'ninguna (no hizo falta)'}**",
        "",
        "Lo detectado en texto viaja SUSTITUIDO: las credenciales como `[CREDENCIAL]` y los datos de",
        "personas con un seudónimo estable (`[CURP-…]`, `[CORREO-…]`, `[RFC-…]`): el mismo valor es el",
        "mismo seudónimo en todo el paquete. Sigue a la persona por su seudónimo; nunca intentes",
        "reconstruir el valor. Lo que no tiene patrón (un nombre propio) no se detecta: no lo copies",
        "al documento.",
        "",
        "## Qué hay",
        "",
    ]
    if has_map:
        lines += [
            "- `map/` — **lo que el sistema ES**, sacado del artefacto y del respaldo: `surface.md` (rutas, jobs, hosts), "
            "`db.md` (tablas, triggers y funciones con cuerpo, vistas), `catalogs.md` (roles, menús, estados, tipos, "
            "parámetros; distribuciones reales), `screens.md` (pantallas con campos, botones y mensajes), "
            "`code.md` (clases con métodos, constantes y cadenas). `system-map.json` es lo mismo, estructurado.",
        ]
    else:
        lines.append("- sin `map/`: no se corrió `pepper map`; el análisis se limita a la ejecución.")
    if has_previous:
        lines.append("- `previous/funcional.json` — el documento del sistema hasta la sesión anterior; se extiende.")
    lines += [
        "- `evidence/flow.md` — la secuencia observada, legible: cada petición con su acción y lo que disparó",
        f"- `evidence/flow.json` — lo mismo, estructurado: {len(flow.get('traces', []))} peticiones, "
        f"{stats.get('assigned', 0)} eventos asignados, {stats.get('unassigned', 0)} sin asignar",
        f"- `evidence/events.jsonl` — los {stats.get('events', 0)} eventos normalizados de la ventana (uno por línea)",
        "- `evidence/reduction.md` — qué se descartó como ruido y por qué",
        "- `evidence/raw/` — la evidencia cruda; cada evento la referencia con `raw_ref` (archivo:línea)",
        "",
    ]
    if session.get("synthetic"):
        lines += [
            "> **Evidencia sintética.** " + session.get("synthetic_note", "Construida a mano para pruebas."),
            "",
        ]
    lines += ["## Legacy", ""]
    if legacy_dirs:
        lines += [f"- `legacy/{name}`" for name in legacy_dirs]
    else:
        lines.append("Sin artefactos del legacy en este paquete.")
    lines += [
        "",
        "## Salida",
        "",
        f"Escribe en `output/`: `{OUTPUT_JSON}` (contrato en `schemas/{SCHEMA_NAME}`) y `{OUTPUT_MD}`.",
        "",
    ]
    return "\n".join(lines)


# La palabra clave puede ir en cualquier parte de la línea ("Contraseña de la base: x",
# "clave del usuario admin = x"): se redacta todo lo que sigue al separador.
_CREDENTIAL_LINE_RE = re.compile(r"(?im)^(.*?\b(?:pass\w*|contrase\w*|clave|secret\w*|token|pwd|psw)\b[^:=\n]{0,40}[:=]\s*)(\S.*)$")


def _assert_no_symlinks(root: Path, label: str, ignore=None) -> None:
    """Rechaza enlaces a cualquier profundidad sin seguirlos."""
    if root.is_symlink():
        raise ValueError(f"{label} es un symlink: PEPPER no sigue enlaces al empaquetar ({root})")
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        ignored = set(ignore(directory, dirnames + filenames)) if ignore else set()
        if Path(directory) == root and label == "legacy":
            ignored.update(name for name in dirnames + filenames if name.startswith("."))
            ignored.update({"pepper-out", "evidence", "legacy"})
        dirnames[:] = [name for name in dirnames if name not in ignored]
        for name in sorted(item for item in dirnames + filenames if item not in ignored):
            path = Path(directory) / name
            if path.is_symlink():
                relative = path.relative_to(root).as_posix()
                raise ValueError(
                    f"{label}/{relative} es un symlink: elimínalo o copia el archivo real; "
                    "PEPPER no puede demostrar que permanezca dentro del paquete"
                )


def _outside_package(path: Path, out_dir: Path) -> bool:
    try:
        path.resolve().relative_to(out_dir.resolve())
    except ValueError:
        return True
    return False


def _redact_notes(package_legacy: Path) -> List[str]:
    """Redacta valores tipo credencial en las notas del humano copiadas al paquete.

    Se redacta en la COPIA; el original en legacy/ no se toca. Devuelve los archivos donde redactó.
    """
    touched: List[str] = []
    if not package_legacy.is_dir():
        return touched
    for path in package_legacy.rglob("*"):
        if path.is_symlink() or not path.is_file() or path.suffix.lower() not in (".md", ".txt", ""):
            continue
        if path.stat().st_size > 1_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeError, OSError):
            continue
        redacted, n = _CREDENTIAL_LINE_RE.subn(r"\1[REDACTADO POR PEPPER]", text)
        if n:
            path.write_text(redacted, encoding="utf-8")
            touched.append(path.name)
    return touched


def _copy_map(system_map: Path, out_dir: Path) -> str:
    """system-map.json + la carpeta map/ legible, rendida SIEMPRE desde el JSON que viaja.

    Copiar un `map/*.md` preexistente entregaba al agente un mapa viejo, editado o
    contradictorio con el JSON canónico, con ambos amarrados por el manifest como si
    correspondieran (auditoría 2026-09-21, P1-05). El render es determinístico: mismo JSON,
    mismos bytes."""
    if not system_map.is_file():
        raise FileNotFoundError(f"mapa inexistente: {system_map} (córrelo con `pepper map`)")
    from pepper.inspect import render_map

    target = out_dir / "map"
    target.mkdir()
    shutil.copy2(system_map, target / "system-map.json")
    data = json.loads((target / "system-map.json").read_text(encoding="utf-8"))
    for name, text in render_map(data).items():
        (target / name).write_text(text, encoding="utf-8")
    return (f"map/ ({len(data.get('screens', []))} pantallas, {len(data.get('classes', []))} clases, "
            f"{len(data.get('catalogs', []))} catálogos)")


def assemble(correlated_dir: Path, out_dir: Path, legacy_dir: Optional[Path] = None,
             data_mode: str = "remote", authorization: Optional[Path] = None,
             manifest_out: Optional[Path] = None,
             system_map: Optional[Path] = None,
             previous: Optional[Path] = None) -> Dict[str, Any]:
    """Arma el paquete. En modo remoto, lo detectado tiene que caber en `authorization` (D24 con
    alcance, `pepper.boundary`): si no cabe se escribe una propuesta y no se arma nada; si cabe, se
    excluye el material de llave y lo detectado en texto viaja sustituido."""
    from pepper import boundary
    if data_mode not in ("local", "remote"):
        raise ValueError("data_mode debe ser 'local' o 'remote'")
    for name in ("session.json", "events.jsonl", "flow.json"):
        if not (correlated_dir / name).is_file():
            raise FileNotFoundError(f"{correlated_dir} no parece salida de `pepper correlate`: falta {name}")
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError(f"el directorio del paquete ya existe y no está vacío: {out_dir}")
    if legacy_dir is not None and not legacy_dir.is_dir():
        raise FileNotFoundError(f"directorio del legacy inexistente: {legacy_dir}")
    if previous is not None and not previous.is_file():
        raise FileNotFoundError(f"discovery anterior inexistente: {previous}")
    _assert_no_symlinks(correlated_dir, "correlated")
    if legacy_dir is not None:
        _assert_no_symlinks(legacy_dir, "legacy", _legacy_ignore(legacy_dir))
    if not DISCOVERY_SKILL.is_file():
        raise FileNotFoundError(f"falta la skill de discovery: {DISCOVERY_SKILL}")
    source_manifest_path = correlated_dir / evidence_manifest.MANIFEST_NAME
    if not source_manifest_path.is_file():
        raise FileNotFoundError(
            f"{correlated_dir} no tiene {evidence_manifest.MANIFEST_NAME}: sin manifest no hay integridad de evidencia — vuelve a correr `pepper correlate`")
    source_manifest = evidence_manifest.load(source_manifest_path)
    source_errors = evidence_manifest.verify(correlated_dir, source_manifest)
    if source_errors:
        raise ValueError("la salida de Correlate perdió integridad: " + "; ".join(source_errors[:5]))

    session = json.loads((correlated_dir / "session.json").read_text(encoding="utf-8"))
    flow = json.loads((correlated_dir / "flow.json").read_text(encoding="utf-8"))

    external_manifest = manifest_out or out_dir.with_name(f"{out_dir.name}.{evidence_manifest.MANIFEST_NAME}")
    if not _outside_package(external_manifest, out_dir):
        raise ValueError("--manifest-out debe estar FUERA del paquete, fuera del alcance normal del agente")
    if external_manifest.exists():
        raise FileExistsError(f"el manifest externo ya existe: {external_manifest}; no se sobrescribe")

    # El paquete se arma primero en un staging al lado del destino y se escanea AHÍ, archivo por
    # archivo, exactamente lo que va a viajar: la copia de la evidencia, el legacy ya redactado,
    # el mapa completo (system-map.json incluido) y el discovery anterior. Antes se escaneaban
    # las fuentes y una representación del mapa, y previous/funcional.json y system-map.json se
    # copiaban sin mirarse: un paquete remoto salía con sensitive_findings: 0 llevando una
    # credencial (revisión 2026-09-21). Si el gate cae, el staging se borra y no queda nada.
    staging = out_dir.parent / f".{out_dir.name}.staging-{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    try:
        redacted_notes, legacy_dirs, map_summary, previous_summary = _stage(
            staging, correlated_dir, legacy_dir, system_map, previous)
        # La evidencia copiada se amarra a Correlate ANTES de cualquier sustitución.
        _verify_evidence_copies(staging, source_manifest)
        # `synthetic` lo escribe quien produce session.json: informa, pero NO exime del gate.
        synthetic = bool(session.get("synthetic"))
        excluded: List[str] = _exclude_key_material(staging) if data_mode == "remote" else []
        data_report = _scan(staging)
        substitutions: Dict[str, Dict[str, Any]] = {}
        approved: Optional[Dict[str, Any]] = None
        if data_mode == "remote" and (data_report.sensitive or data_report.unscanned):
            needed = boundary.proposal(staging, (session.get("environment") or {}).get("profile_id"), data_report, excluded)
            if authorization is None or not authorization.is_file():
                problems = ["no hay autorización de datos para este sistema"]
            else:
                approved = boundary.load(authorization)
                problems = boundary.out_of_scope(approved, needed)
            if problems:
                proposal_path = boundary.write_proposal(out_dir, needed, problems)
                found = summarize_sensitive(data_report.sensitive + data_report.unscanned)
                raise boundary.BoundaryError(
                    "el paquete remoto trae datos fuera de lo autorizado; no se armó. "
                    + "; ".join(problems[:6]) + f". Ubicaciones: {found}. "
                    f"Propuesta de alcance para que una persona decida: {proposal_path} "
                    "(con su sí: `pepper authorize <propuesta> --by <nombre>` y repite con --authorization)",
                    proposal_path)
            if data_report.sensitive:
                substitutions = _substitute(staging, boundary.pseudonym_key(authorization))
                residual = _scan(staging).sensitive
                if residual:
                    raise ValueError("quedaron datos detectables tras la sustitución; no se armó el paquete. "
                                     f"Ubicaciones: {summarize_sensitive(residual)}")
        _finish(staging, correlated_dir, source_manifest, session, flow, legacy_dirs, data_mode,
                data_report, map_summary, previous_summary, synthetic, external_manifest,
                approved, authorization, substitutions, excluded)
        if out_dir.exists():
            out_dir.rmdir()  # existía vacío (se comprobó arriba); rename exige que no exista
        staging.rename(out_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    return {
        "session_id": session.get("session_id"),
        "redacted_notes": redacted_notes,
        "events": flow.get("stats", {}).get("events", 0),
        "traces": len(flow.get("traces", [])),
        "legacy": legacy_dirs,
        "map": map_summary,
        "previous": previous_summary,
        "data_mode": data_mode,
        "sensitive_findings": len(data_report.sensitive),
        "unscanned_files": len(data_report.unscanned),
        "substituted": sum(sum(s["counts"].values()) for s in substitutions.values()),
        "excluded": excluded,
        "external_manifest": str(external_manifest),
        "files": sum(1 for path in out_dir.rglob("*") if path.is_file()),
        "out_dir": str(out_dir),
    }


def _stage(out_dir: Path, correlated_dir: Path, legacy_dir: Optional[Path], system_map: Optional[Path],
           previous: Optional[Path]):
    """Copia al staging todo lo que viaja y redacta las notas (el escaneo va después, sobre la copia)."""

    evidence = out_dir / "evidence"
    evidence.mkdir(parents=True)
    (out_dir / "output").mkdir()
    (out_dir / "schemas").mkdir()
    shutil.copy2(correlated_dir / "session.json", out_dir / "session.json")
    for name in _EVIDENCE_FILES:
        if (correlated_dir / name).is_file():
            shutil.copy2(correlated_dir / name, evidence / name)
    if (correlated_dir / "raw").is_dir():
        shutil.copytree(correlated_dir / "raw", evidence / "raw")

    map_summary = _copy_map(system_map, out_dir) if system_map is not None else ""
    previous_summary = ""
    if previous is not None:
        (out_dir / "previous").mkdir()
        shutil.copy2(previous, out_dir / "previous" / OUTPUT_JSON)
        previous_summary = f"previous/{OUTPUT_JSON}"

    legacy_dirs: List[str] = []
    if legacy_dir is not None:
        ignore = _legacy_ignore(legacy_dir)
        tool = tool_paths(legacy_dir)
        for child in sorted(legacy_dir.iterdir()):
            if child.name.startswith(".") or is_tool_path(child, tool):
                continue
            if child.name in ("pepper-out", "evidence", "legacy"):
                continue
            if child.is_dir():
                shutil.copytree(child, out_dir / "legacy" / child.name, ignore=ignore)
                legacy_dirs.append(child.name + "/")
            elif child.is_file():
                (out_dir / "legacy").mkdir(exist_ok=True)
                shutil.copy2(child, out_dir / "legacy" / child.name)
                legacy_dirs.append(child.name)
    redacted_notes = _redact_notes(out_dir / "legacy")
    return redacted_notes, legacy_dirs, map_summary, previous_summary


_SCOPES = ("evidence", "legacy", "map", "previous")


def _scan(out_dir: Path):
    """Se escanea la COPIA, después de redactar: cada archivo que el manifest va a amarrar."""
    from pepper.sensitive import scan as scan_sensitive

    roots = [(scope, out_dir / scope, None) for scope in _SCOPES if (out_dir / scope).is_dir()]
    data_report = scan_sensitive(roots)
    session_report = scan_sensitive([("", out_dir, _only_session_json)])
    data_report.sensitive.extend(session_report.sensitive)
    data_report.unscanned.extend(session_report.unscanned)
    return data_report


def _verify_evidence_copies(out_dir: Path, source_manifest: Dict[str, Any]) -> None:
    for original_rel, digest in source_manifest.get("files", {}).items():
        package_rel = original_rel if original_rel == "session.json" else f"evidence/{original_rel}"
        copied = out_dir / package_rel
        if copied.is_file() and evidence_manifest.sha256_file(copied) != digest:
            raise ValueError(f"la copia de {original_rel} no coincide con el manifest de Correlate: {package_rel}")


def _exclude_key_material(out_dir: Path) -> List[str]:
    """Un keystore o una llave privada no viaja nunca, ni con autorización: se quita de la copia."""
    from pepper.sensitive import is_key_material

    excluded: List[str] = []
    for scope in _SCOPES:
        for path in sorted((out_dir / scope).rglob("*")) if (out_dir / scope).is_dir() else []:
            if path.is_file() and not path.is_symlink() and is_key_material(path):
                excluded.append(path.relative_to(out_dir).as_posix())
                path.unlink()
    return excluded


def _substitute(out_dir: Path, key: bytes) -> Dict[str, Dict[str, Any]]:
    """Sustituye en la copia lo que el escáner detecta en texto. Un JSON que dejara de ser JSON
    tras sustituir detiene el paquete: mejor no armarlo que entregar evidencia rota."""
    from pepper.sensitive import _looks_binary, _MAX_TEXT_BYTES, pseudonymize_text

    targets = [out_dir / "session.json"]
    for scope in _SCOPES:
        if (out_dir / scope).is_dir():
            targets.extend(sorted(p for p in (out_dir / scope).rglob("*") if p.is_file() and not p.is_symlink()))
    done: Dict[str, Dict[str, int]] = {}
    for path in targets:
        if not path.is_file() or path.stat().st_size > _MAX_TEXT_BYTES:
            continue
        data = path.read_bytes()
        if _looks_binary(data[:8192]):
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        new, counts = pseudonymize_text(text, key)
        if not counts:
            continue
        rel = path.relative_to(out_dir).as_posix()
        if path.suffix == ".json":
            _assert_json(new, rel, lambda t: [t])
        elif path.suffix == ".jsonl":
            _assert_json(new, rel, lambda t: [line for line in t.splitlines() if line.strip()])
        path.write_bytes(new.encode("utf-8"))
        done[rel] = {"counts": counts, "source_sha256": evidence_manifest.sha256_bytes(data)}
    return done


def _assert_json(text: str, rel: str, chunks) -> None:
    for chunk in chunks(text):
        try:
            json.loads(chunk)
        except ValueError as error:
            raise ValueError(f"sustituir datos dejó {rel} como JSON inválido ({error}); no se armó el paquete") from error


def _only_session_json(directory: str, names: List[str]) -> List[str]:
    return [name for name in names if name != "session.json"]


def _finish(out_dir: Path, correlated_dir: Path, source_manifest: Dict[str, Any], session: Dict[str, Any],
            flow: Dict[str, Any], legacy_dirs: List[str], data_mode: str, data_report,
            map_summary: str, previous_summary: str, synthetic: bool, external_manifest: Path,
            approved: Optional[Dict[str, Any]], authorization: Optional[Path],
            substitutions: Dict[str, Dict[str, Any]], excluded: List[str]) -> None:
    """Lo que PEPPER genera (puertas de entrada, prompt, schema, manifest) — nada del legacy."""
    shutil.copy2(SCHEMAS_DIR / SCHEMA_NAME, out_dir / "schemas" / SCHEMA_NAME)
    prompt = strip_frontmatter(DISCOVERY_SKILL.read_text(encoding="utf-8"))
    (out_dir / "prompt.md").write_text(prompt, encoding="utf-8")

    adapter = _adapter(bool(map_summary), bool(previous_summary), data_mode)
    (out_dir / "CLAUDE.md").write_text(adapter, encoding="utf-8")
    (out_dir / "AGENTS.md").write_text(adapter, encoding="utf-8")
    overrides = []
    if approved:
        overrides.append(f"{approved['decided_by']} ({approved['date']}): categorías "
                         f"{', '.join(approved['categories']) or 'ninguna'}; {len(approved['unscanned'])} archivo(s) no inspeccionado(s)")
    (out_dir / "README.md").write_text(
        _readme(session, flow, legacy_dirs, data_mode, len(data_report.sensitive),
                len(data_report.unscanned), overrides, bool(map_summary), bool(previous_summary)),
        encoding="utf-8",
    )

    # El manifest viaja con el paquete, re-mapeado a su layout. Cada copia se verificó contra
    # el hash de Correlate antes de sustituir; lo que se amarra es lo que viaja, y lo sustituido
    # queda declarado en data_policy con el hash original: la cadena se puede reconstruir en local.
    # El mapa, el discovery anterior y el legacy también se amarran: el agente solo escribe en output/.
    package_files: Dict[str, str] = {}
    for original_rel in source_manifest.get("files", {}):
        package_rel = original_rel if original_rel == "session.json" else f"evidence/{original_rel}"
        copied = out_dir / package_rel
        if copied.is_file():
            package_files[package_rel] = evidence_manifest.sha256_file(copied)
    for scope in ("legacy", "map", "previous"):
        base = out_dir / scope
        if base.is_dir():
            for path in sorted(base.rglob("*")):
                if path.is_file() and not path.is_symlink():
                    package_files[path.relative_to(out_dir).as_posix()] = evidence_manifest.sha256_file(path)
    manifest = dict(source_manifest)
    manifest["files"] = package_files
    manifest["data_policy"] = {
        "mode": data_mode,
        "synthetic": synthetic,
        "sensitive_findings": len(data_report.sensitive),
        "categories": sorted({f.kind for f in data_report.sensitive}),
        "unscanned_files": len(data_report.unscanned),
        "excluded": excluded,
        "substitutions": dict(sorted(substitutions.items())),
        "authorization": ({"sha256": evidence_manifest.sha256_file(authorization), "decided_by": approved["decided_by"],
                           "date": approved["date"], "system": approved["system"], "destination": approved["destination"]}
                          if approved and authorization else None),
    }
    internal_manifest = evidence_manifest.write(out_dir, manifest)
    external_manifest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(internal_manifest, external_manifest)
