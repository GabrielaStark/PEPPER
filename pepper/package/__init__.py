"""Fase — Package: arma el paquete controlado, la carpeta autocontenida sobre la que trabaja el agente.

Contenido:
  README.md, CLAUDE.md, AGENTS.md   puertas de entrada (todas llevan a prompt.md)
  prompt.md                          la skill discovery-runtime, sin frontmatter
  session.json
  evidence/                          events.jsonl, flow.json, flow.md, reduction.md, raw/
  legacy/                            artefactos del legacy (source, configuration, docs, ...)
  schemas/runtime-discovery.schema.json
  output/                            aquí escribe el agente
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
from pepper.workspace import is_tool_path, tool_paths

_EVIDENCE_FILES = ("events.jsonl", "flow.json", "flow.md", "reduction.md")
_LEGACY_IGNORE = shutil.ignore_patterns("target", ".git", "node_modules", "__pycache__", "*.class", ".DS_Store")


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


DISCOVERY_SKILL = SKILLS_DIR / "discovery-runtime" / "SKILL.md"


def strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    return parts[2].lstrip("\n") if len(parts) == 3 else text


def _adapter(has_source: bool, data_mode: str) -> str:
    lines = [
        "# Instrucciones para el agente",
        "",
        "Lee `prompt.md` y síguelo al pie de la letra.",
        "",
    ]
    if has_source:
        lines += [
            "Hay código fuente en `legacy/source/`: aplica también la sección",
            "«Comparación runtime ↔ código» de `prompt.md`.",
            "",
        ]
    lines += [
        "Trabajas en modo **solo lectura** sobre este paquete. Tu único destino de escritura es `output/`:",
        "`output/runtime-discovery.json` (válido contra `schemas/runtime-discovery.schema.json`)",
        "y `output/runtime-discovery.md`.",
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
            overrides: List[str]) -> str:
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
        f"- Excepciones autorizadas explícitamente: **{', '.join(overrides) if overrides else 'ninguna'}**",
        "",
        "## Evidencia",
        "",
        "- `evidence/flow.md` — la secuencia correlacionada, legible; **empieza por aquí**",
        f"- `evidence/flow.json` — lo mismo, estructurado: {len(flow.get('traces', []))} peticiones, "
        f"{stats.get('assigned', 0)} eventos asignados, {stats.get('unassigned', 0)} sin asignar",
        f"- `evidence/events.jsonl` — los {stats.get('events', 0)} eventos normalizados de la ventana (uno por línea)",
        "- `evidence/reduction.md` — qué se descartó como ruido y por qué",
        "- `evidence/raw/` — la evidencia cruda; cada evento la referencia con `raw_ref` (archivo:línea)",
        "",
        "Los eventos traen `correlation_id` solo cuando la fuente lo emitió. Cuando PEPPER lo infirió,",
        "está en `metadata.inferred_correlation_id` junto con `metadata.correlation_basis`, que dice",
        "qué sustenta el enlace (correlation_id explícito > afinidad por thread/pid > ventana temporal).",
        "",
    ]
    if session.get("synthetic"):
        lines += [
            "> **Evidencia sintética.** " + session.get("synthetic_note", "Construida a mano para pruebas."),
            "",
        ]
    lines += ["## Legacy", ""]
    if legacy_dirs:
        lines += [f"- `legacy/{name}/`" for name in legacy_dirs]
    else:
        lines.append("Sin artefactos del legacy en este paquete: el análisis se limita a la evidencia de ejecución.")
    lines += [
        "",
        "## Salida",
        "",
        "Escribe en `output/`: `runtime-discovery.json` (contrato en `schemas/`) y `runtime-discovery.md`.",
        "",
    ]
    return "\n".join(lines)



_CREDENTIAL_LINE_RE = re.compile(r"(?im)^(\s*(?:pass\w*|contrase\w*|clave|secret\w*|token|pwd)\s*[:=]\s*)(\S.*)$")


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

    NOTAS.md dice \"sin credenciales aquí\", pero si el humano las puso, no pueden
    viajar a un agente externo en claro (auditoría C-03). Se redacta en la COPIA;
    el original en legacy/ no se toca. Devuelve los archivos donde redactó.
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


def assemble(correlated_dir: Path, out_dir: Path, legacy_dir: Optional[Path] = None,
             data_mode: str = "remote", allow_sensitive: bool = False,
             acknowledge_unscanned: bool = False,
             manifest_out: Optional[Path] = None) -> Dict[str, Any]:
    if data_mode not in ("local", "remote"):
        raise ValueError("data_mode debe ser 'local' o 'remote'")
    for name in ("session.json", "events.jsonl", "flow.json"):
        if not (correlated_dir / name).is_file():
            raise FileNotFoundError(f"{correlated_dir} no parece salida de `pepper correlate`: falta {name}")
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError(f"el directorio del paquete ya existe y no está vacío: {out_dir}")
    if legacy_dir is not None and not legacy_dir.is_dir():
        raise FileNotFoundError(f"directorio del legacy inexistente: {legacy_dir}")
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

    from pepper.sensitive import scan as scan_sensitive
    from pepper.sensitive import summarize as summarize_sensitive

    roots = [("evidence", correlated_dir, None)]
    if legacy_dir is not None:
        roots.append(("legacy", legacy_dir, _legacy_ignore(legacy_dir)))
    data_report = scan_sensitive(roots)
    # `synthetic` lo escribe quien produce session.json (el agente, en Observe): informa,
    # pero NO exime del gate — con esa bandera un WAR y un dump reales viajaban a un
    # agente remoto sin revisión (auditoría 2026-09-03). El demo pasa con flags explícitos.
    synthetic = bool(session.get("synthetic"))
    if data_mode == "remote":
        if data_report.sensitive and not allow_sensitive:
            raise ValueError(
                "datos sensibles detectados; no se creó el paquete remoto. "
                f"Ubicaciones: {summarize_sensitive(data_report.sensitive)}. "
                "Sanea la fuente o repite con --allow-sensitive únicamente tras autorización humana"
            )
        if data_report.unscanned and not acknowledge_unscanned:
            raise ValueError(
                "hay archivos que PEPPER no puede inspeccionar antes de enviarlos a un agente remoto. "
                f"Ubicaciones: {summarize_sensitive(data_report.unscanned)}. "
                "Revísalos o repite con --acknowledge-unscanned tras autorización humana"
            )

    external_manifest = manifest_out or out_dir.with_name(f"{out_dir.name}.{evidence_manifest.MANIFEST_NAME}")
    if not _outside_package(external_manifest, out_dir):
        raise ValueError("--manifest-out debe estar FUERA del paquete, fuera del alcance normal del agente")
    if external_manifest.exists():
        raise FileExistsError(f"el manifest externo ya existe: {external_manifest}; no se sobrescribe")

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
                legacy_dirs.append(child.name)
            elif child.is_file():
                # el caso de uso central es "me queda un WAR y un dump" sueltos (auditoría H-03)
                (out_dir / "legacy").mkdir(exist_ok=True)
                shutil.copy2(child, out_dir / "legacy" / child.name)
                legacy_dirs.append(child.name)
    has_source = "source" in legacy_dirs

    shutil.copy2(SCHEMAS_DIR / "runtime-discovery.schema.json", out_dir / "schemas" / "runtime-discovery.schema.json")
    prompt = strip_frontmatter(DISCOVERY_SKILL.read_text(encoding="utf-8"))
    (out_dir / "prompt.md").write_text(prompt, encoding="utf-8")

    adapter = _adapter(has_source, data_mode)
    (out_dir / "CLAUDE.md").write_text(adapter, encoding="utf-8")
    (out_dir / "AGENTS.md").write_text(adapter, encoding="utf-8")
    overrides = []
    if allow_sensitive:
        overrides.append("datos sensibles")
    if acknowledge_unscanned:
        overrides.append("archivos no inspeccionados")
    (out_dir / "README.md").write_text(
        _readme(session, flow, legacy_dirs, data_mode, len(data_report.sensitive),
                len(data_report.unscanned), overrides),
        encoding="utf-8",
    )

    # El manifest viaja con el paquete, re-mapeado a su layout, y cada copia se
    # verifica contra el hash original: la evidencia del paquete queda amarrada a
    # la salida de Correlate (auditoría C-02). El agente solo escribe en output/.
    package_files: Dict[str, str] = {}
    for original_rel, digest in source_manifest.get("files", {}).items():
        package_rel = original_rel if original_rel == "session.json" else f"evidence/{original_rel}"
        copied = out_dir / package_rel
        if not copied.is_file():
            continue  # flow.md/reduction.md pueden no existir; lo copiado es lo que se amarra
        actual = evidence_manifest.sha256_file(copied)
        if actual != digest:
            raise ValueError(f"la copia de {original_rel} no coincide con el manifest de Correlate: {package_rel}")
        package_files[package_rel] = digest
    redacted_notes = _redact_notes(out_dir / "legacy")
    for path in sorted((out_dir / "legacy").rglob("*")) if (out_dir / "legacy").is_dir() else []:
        if path.is_file() and not path.is_symlink():
            package_files[path.relative_to(out_dir).as_posix()] = evidence_manifest.sha256_file(path)
    manifest = dict(source_manifest)
    manifest["files"] = package_files
    manifest["data_policy"] = {
        "mode": data_mode,
        "synthetic": synthetic,
        "sensitive_findings": len(data_report.sensitive),
        "unscanned_files": len(data_report.unscanned),
        "allow_sensitive": bool(allow_sensitive),
        "acknowledge_unscanned": bool(acknowledge_unscanned),
    }
    internal_manifest = evidence_manifest.write(out_dir, manifest)
    external_manifest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(internal_manifest, external_manifest)

    return {
        "session_id": session.get("session_id"),
        "redacted_notes": redacted_notes,
        "events": flow.get("stats", {}).get("events", 0),
        "traces": len(flow.get("traces", [])),
        "legacy": legacy_dirs,
        "data_mode": data_mode,
        "sensitive_findings": len(data_report.sensitive),
        "unscanned_files": len(data_report.unscanned),
        "external_manifest": str(external_manifest),
        "files": sum(1 for path in out_dir.rglob("*") if path.is_file()),
        "out_dir": str(out_dir),
    }
