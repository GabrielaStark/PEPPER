"""Dónde vive PEPPER y dónde vive el trabajo.

Tres formas de usarlo, y en las tres el marcador `.claude/commands/pepper.md` dice "aquí hay
herramienta de PEPPER":

- **workspace creado por `pepper init`**: la herramienta es un clon aparte (la *instalación*);
  el workspace lleva un enlace `pepper/` al paquete de la instalación, la parte que Claude Code
  carga de la raíz copiada (`.claude/`, `CLAUDE.md`, `AGENTS.md`, `templates/`, el guardia) y
  `.pepper-home` con la ruta de la instalación. Lo que es *instalación* (perfiles, schemas,
  skills, ejemplos) se resuelve desde `pepper.REPO_ROOT`; lo que es *trabajo* (`legacy/`,
  `docs/pepper/`, `pepper-out/`, `evidence/`) es relativo al workspace.
- **dentro del clon** (el flujo viejo): instalación y workspace son la misma carpeta.
- **encima del repo del legacy**: la herramienta copiada convive con el código del sistema.

`pepper detect` y `pepper package` excluyen la herramienta para no confundirla con artefactos
del sistema (el fixture de `examples/` trae un pom.xml de juguete; `templates/NOTAS-LEGACY.md`
nombra servidores y motores).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Dict, Optional, Set

from pepper import REPO_ROOT, __version__

MARKER = Path(".claude/commands/pepper.md")
HOME_FILE = ".pepper-home"
# Todo lo que es herramienta cuando PEPPER está instalado aquí: lo del clon completo y lo que
# `pepper init` copia o enlaza en un workspace (`pepper` es el enlace al paquete; `scripts/` lleva
# el guardia; `templates/`, `CLAUDE.md` y `AGENTS.md` son copias).
TOOL_DIRS = (
    ".claude", ".github", "pepper", "schemas", "profiles", "examples", "tests", "scripts", "templates",
    "docs/documentacion", "docs/pepper", "pepper-out", "evidence",
    "CLAUDE.md", "AGENTS.md", HOME_FILE,
)


def is_pepper_root(root: Path) -> bool:
    return (root / MARKER).is_file()


def tool_paths(root: Path) -> Set[Path]:
    """Rutas de la herramienta bajo `root` si PEPPER está instalado ahí; vacío si no."""
    if not is_pepper_root(root):
        return set()
    return {(root / rel).resolve() for rel in TOOL_DIRS}


def is_tool_path(path: Path, tool: Set[Path]) -> bool:
    if not tool:
        return False
    resolved = path.resolve()
    return any(resolved == candidate or candidate in resolved.parents for candidate in tool)


def find_root(start: Path) -> Optional[Path]:
    """El workspace al que pertenece `start`: el ancestro más cercano (o él mismo) con el marcador
    de PEPPER o con `.pepper-home`. None si no está dentro de ninguno."""
    current = start.resolve()
    for candidate in (current, *current.parents):
        if is_pepper_root(candidate) or (candidate / HOME_FILE).is_file():
            return candidate
    return None


def installation_commit(root: Path = REPO_ROOT) -> Optional[str]:
    """El commit de la instalación, si `git rev-parse HEAD` responde ahí; None si no es un clon."""
    try:
        result = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    commit = result.stdout.strip()
    return commit if result.returncode == 0 and commit else None


def read_home(root: Path) -> Dict[str, str]:
    """`.pepper-home` del workspace como diccionario (`home`, `version`, `commit`); vacío si no hay."""
    path = root / HOME_FILE
    if not path.is_file():
        return {}
    data: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        data[key.strip()] = value.strip()
    return data


def version_line(cwd: Optional[Path] = None) -> str:
    """Lo que imprime `pepper --version`: versión y commit de la instalación que responde, y en un
    workspace de `pepper init`, la instalación que declara `.pepper-home` (y un aviso si la copia de
    la herramienta se hizo con otra versión)."""
    commit = installation_commit()
    line = f"pepper {__version__}" + (f" (commit {commit[:12]})" if commit else "")
    start = cwd or Path.cwd()
    home = read_home(find_root(start) or start)
    if not home:
        return line
    line += f" · instalación: {home.get('home', '?')}"
    recorded = home.get("version", ""), home.get("commit", "")
    if recorded[0] != __version__ or (commit and recorded[1] and recorded[1] != commit):
        line += (f"\n  la herramienta copiada en este workspace es de pepper {recorded[0] or '?'}"
                 + (f" (commit {recorded[1][:12]})" if recorded[1] else "")
                 + ": corre `python3 -m pepper init . --force` para actualizarla")
    return line
