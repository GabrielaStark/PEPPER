"""Dónde vive PEPPER: en un workspace propio (clonado) o instalado encima del repo de un proyecto.

En el segundo caso, los directorios de la herramienta conviven con el código del legacy.
`pepper detect` y `pepper package` los excluyen para no confundirlos con artefactos del
sistema (el fixture de `examples/` trae un pom.xml de juguete, por ejemplo).
"""

from __future__ import annotations

from pathlib import Path
from typing import Set

MARKER = Path(".claude/commands/pepper-init.md")
TOOL_DIRS = (
    ".claude", ".github", "pepper", "schemas", "profiles", "examples", "tests", "scripts",
    "docs/documentacion", "docs/pepper", "pepper-out", "evidence",
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
