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
        return sorted(ignored)

    return ignore


DISCOVERY_SKILL = SKILLS_DIR / "discovery-runtime" / "SKILL.md"


def strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    return parts[2].lstrip("\n") if len(parts) == 3 else text


def _adapter(has_source: bool) -> str:
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
    return "\n".join(lines)


def _readme(session: Dict[str, Any], flow: Dict[str, Any], legacy_dirs: List[str]) -> str:
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
        if not path.is_file() or path.suffix.lower() not in (".md", ".txt", ""):
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


def assemble(correlated_dir: Path, out_dir: Path, legacy_dir: Optional[Path] = None) -> Dict[str, Any]:
    for name in ("session.json", "events.jsonl", "flow.json"):
        if not (correlated_dir / name).is_file():
            raise FileNotFoundError(f"{correlated_dir} no parece salida de `pepper correlate`: falta {name}")
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError(f"el directorio del paquete ya existe y no está vacío: {out_dir}")
    if legacy_dir is not None and not legacy_dir.is_dir():
        raise FileNotFoundError(f"directorio del legacy inexistente: {legacy_dir}")
    if not DISCOVERY_SKILL.is_file():
        raise FileNotFoundError(f"falta la skill de discovery: {DISCOVERY_SKILL}")
    source_manifest_path = correlated_dir / evidence_manifest.MANIFEST_NAME
    if not source_manifest_path.is_file():
        raise FileNotFoundError(
            f"{correlated_dir} no tiene {evidence_manifest.MANIFEST_NAME}: sin manifest no hay integridad de evidencia — vuelve a correr `pepper correlate`")
    source_manifest = evidence_manifest.load(source_manifest_path)

    session = json.loads((correlated_dir / "session.json").read_text(encoding="utf-8"))
    flow = json.loads((correlated_dir / "flow.json").read_text(encoding="utf-8"))

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
            if child.is_symlink():
                continue  # un symlink puede apuntar fuera del legacy: no se sigue (auditoría H-02)
            if child.is_dir():
                shutil.copytree(child, out_dir / "legacy" / child.name, ignore=ignore, symlinks=True)
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

    adapter = _adapter(has_source)
    (out_dir / "CLAUDE.md").write_text(adapter, encoding="utf-8")
    (out_dir / "AGENTS.md").write_text(adapter, encoding="utf-8")
    (out_dir / "README.md").write_text(_readme(session, flow, legacy_dirs), encoding="utf-8")

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
    evidence_manifest.write(out_dir, manifest)

    return {
        "session_id": session.get("session_id"),
        "redacted_notes": redacted_notes,
        "events": flow.get("stats", {}).get("events", 0),
        "traces": len(flow.get("traces", [])),
        "legacy": legacy_dirs,
        "files": sum(1 for path in out_dir.rglob("*") if path.is_file()),
        "out_dir": str(out_dir),
    }
