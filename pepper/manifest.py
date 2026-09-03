"""Manifest de evidencia: hashes que amarran la salida de Correlate a lo que Export publica.

`raw_ref` da trazabilidad interna ("esta conclusión apunta a esta línea"); el
manifest da integridad ("esta línea pertenecía a la captura original"). Sin él,
cualquiera —el agente incluido— puede fabricar un evento en el paquete y Export
no tiene contra qué compararlo (hallazgo C-02 de la auditoría 2026-09-02).

- Correlate lo escribe al terminar (`evidence-manifest.json`): SHA-256 de cada
  archivo de su salida, más versión de PEPPER. Sin timestamp: misma evidencia →
  mismos bytes (D8), y dos corridas se comparan con un diff.
- Package lo copia junto con la evidencia.
- Export lo exige y verifica: todo archivo listado existe con el hash exacto, y
  no hay archivos EXTRA en el ámbito de la evidencia. El agente solo escribe en
  `output/`.

Sin criptografía de más: hashes determinísticos bastan para el MVP. Lo que esto
NO da (y se declara): protección contra quien pueda editar el manifest mismo
fuera del paquete; para eso, consérvalo también fuera (Export acepta
`--manifest` externo).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict, List, Optional

MANIFEST_NAME = "evidence-manifest.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def build(root: Path, exclude: tuple = (MANIFEST_NAME,)) -> Dict:
    """Hashes de todos los archivos bajo `root` (rutas relativas POSIX, ordenadas)."""
    from pepper import __version__

    files: Dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix()
        if relative in exclude:
            continue
        files[relative] = sha256_file(path)
    return {
        "manifest_version": "0.1.0",
        "generated_by": f"pepper {__version__}",
        "files": files,
    }


def write(root: Path, manifest: Dict) -> Path:
    path = root / MANIFEST_NAME
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load(path: Path) -> Dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict):
        raise ValueError(f"{path}: no es un manifest de evidencia válido")
    return manifest


def verify(root: Path, manifest: Dict, scopes: Optional[List[str]] = None) -> List[str]:
    """Errores de integridad de `root` contra el manifest.

    - Todo archivo listado debe existir con el hash exacto (alterado/faltante = error).
    - Dentro de cada `scope` (p. ej. "evidence/") no puede haber archivos que el
      manifest no liste: un evento fabricado aparece como archivo extra o como hash
      alterado, nunca en silencio.
    """
    errors: List[str] = []
    listed: Dict[str, str] = manifest.get("files", {})
    for relative, expected in sorted(listed.items()):
        path = root / relative
        if path.is_symlink():
            errors.append(f"integridad · {relative} es un symlink; la evidencia debe permanecer dentro del paquete")
        elif not path.is_file():
            errors.append(f"integridad · falta {relative} (listado en el manifest)")
        elif sha256_file(path) != expected:
            errors.append(f"integridad · {relative} fue modificado después de Correlate (hash distinto al manifest)")
    for scope in (scopes or []):
        base = root / scope
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_symlink():
                relative = path.relative_to(root).as_posix()
                errors.append(f"integridad · {relative} es un symlink; no se siguen enlaces en evidencia ni legacy")
                continue
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if relative not in listed and relative != MANIFEST_NAME:
                errors.append(f"integridad · {relative} no existe en el manifest: archivo ajeno a la captura original")
    return errors
