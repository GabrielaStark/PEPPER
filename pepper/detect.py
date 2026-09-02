"""Detección de perfil: evalúa las señales de `detection.signals` de cada perfil sobre un directorio de artefactos.

Es la parte determinística de Inspect. Determina si un legacy cae en el escalón 1
(hay perfil validado aplicable) o si el agente debe identificar el stack y redactar
un borrador de perfil.
"""

from __future__ import annotations

import re
import tarfile
import zipfile
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pepper.profiles import Profile, iter_profiles
from pepper.workspace import is_tool_path, tool_paths

_SKIP_DIRS = {".git", "node_modules", "__pycache__", "target"}
_MAX_CONTENT_BYTES = 5 * 1024 * 1024
# Los artefactos desplegables llevan dentro pom.xml, application*.yml, descriptores,
# .csproj embebidos, package.json… Las señales también se buscan ahí (un nivel: no se
# abren archivos dentro de archivos). Sin sesgo de stack: cualquier zip o tar entra;
# un dist en forma de carpeta ni siquiera necesita esto — se camina como archivos.
_ZIP_SUFFIXES = {".war", ".jar", ".ear", ".zip", ".nupkg", ".whl", ".egg", ".apk", ".aar"}
_TAR_SUFFIXES = {".tar", ".tgz", ".tbz2", ".txz"}
_TAR_DOUBLE_SUFFIXES = {".tar.gz", ".tar.bz2", ".tar.xz"}
_MAX_MEMBER_BYTES = 2 * 1024 * 1024


def _walk(root: Path) -> List[Path]:
    tool = tool_paths(root)  # PEPPER instalado encima del repo: su herramienta no es el legacy
    found: List[Path] = []
    for path in root.rglob("*"):
        if any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if is_tool_path(path, tool):
            continue
        found.append(path)
    return found


def _name_matches(relative: str, pattern: str) -> bool:
    name = relative.rsplit("/", 1)[-1]
    if fnmatch(relative, pattern) or fnmatch(name, pattern):
        return True
    if pattern.startswith("**/"):
        return fnmatch(name, pattern[3:])
    return False


def _matches(path: Path, root: Path, pattern: str) -> bool:
    return _name_matches(path.relative_to(root).as_posix(), pattern)


class _Archive:
    """Vista uniforme sobre un artefacto empacado (zip o tar): miembros y lectura."""

    def __init__(self, name: str, members: List[Tuple[str, int]], reader, closer):
        self.name = name
        self.members = members          # (ruta interna, tamaño)
        self.read = reader              # ruta interna -> bytes
        self.close = closer


def _is_tar(path: Path) -> bool:
    lowered = path.name.lower()
    return (path.suffix.lower() in _TAR_SUFFIXES
            or any(lowered.endswith(suffix) for suffix in _TAR_DOUBLE_SUFFIXES))


def _open_archive(path: Path, relative: str) -> Optional[_Archive]:
    if path.suffix.lower() in _ZIP_SUFFIXES:
        try:
            archive = zipfile.ZipFile(path)
        except (zipfile.BadZipFile, OSError):
            return None
        members = [(i.filename, i.file_size) for i in archive.infolist() if not i.is_dir()]
        return _Archive(relative, members, archive.read, archive.close)
    if _is_tar(path):
        try:
            archive = tarfile.open(path)
        except (tarfile.TarError, OSError):
            return None
        members = [(m.name, m.size) for m in archive.getmembers() if m.isfile()]

        def read(name: str) -> bytes:
            extracted = archive.extractfile(name)
            return extracted.read() if extracted else b""

        return _Archive(relative, members, read, archive.close)
    return None


def _archives(entries: List[Path], root: Path) -> List[_Archive]:
    opened = []
    for path in entries:
        if not path.is_file():
            continue
        archive = _open_archive(path, path.relative_to(root).as_posix())
        if archive is not None:
            opened.append(archive)
    return opened


def _member_hit(archives: List[_Archive], pattern: str, regex: Optional["re.Pattern[str]"] = None,
                file_pattern: Optional[str] = None) -> Optional[str]:
    for archive in archives:
        for member, size in archive.members:
            if regex is None:
                if _name_matches(member, pattern):
                    return f"{archive.name}!{member}"
                continue
            if not _name_matches(member, file_pattern or "*") or size > _MAX_MEMBER_BYTES:
                continue
            text = archive.read(member).decode("utf-8", errors="replace")
            if regex.search(text):
                return f"{archive.name}!{member}"
    return None


def evaluate(profile: Profile, root: Path, entries: List[Path],
             archives: Optional[List[_Archive]] = None) -> Dict[str, Any]:
    detection = profile.data.get("detection", {})
    own_archives = archives is None
    if archives is None:
        archives = _archives(entries, root)
    score = 0.0
    matches: List[Dict[str, Any]] = []
    for signal in detection.get("signals", []):
        kind, pattern, weight = signal["type"], signal["pattern"], signal.get("weight", 1)
        hit: Optional[str] = None
        if kind in ("file_exists", "extension"):
            found = next((p for p in entries if p.is_file() and _matches(p, root, pattern)), None)
            hit = found.relative_to(root).as_posix() if found else _member_hit(archives, pattern)
        elif kind == "directory":
            found = next((p for p in entries if p.is_dir() and _matches(p, root, pattern)), None)
            hit = found.relative_to(root).as_posix() if found else None
        elif kind == "file_content":
            regex = re.compile(pattern)
            for candidate in entries:
                if not candidate.is_file() or not _matches(candidate, root, signal.get("file", "*")):
                    continue
                if candidate.stat().st_size > _MAX_CONTENT_BYTES:
                    continue
                if regex.search(candidate.read_text(encoding="utf-8", errors="replace")):
                    hit = candidate.relative_to(root).as_posix()
                    break
            if hit is None:
                hit = _member_hit(archives, pattern, regex, signal.get("file", "*"))
        if hit is not None:
            score += weight
            matches.append({
                "type": kind,
                "pattern": pattern,
                "weight": weight,
                "hit": hit,
            })
    min_score = detection.get("min_score", 1)
    if own_archives:
        for archive in archives:
            archive.close()
    return {
        "profile_id": profile.id,
        "status": profile.status,
        "score": score,
        "min_score": min_score,
        "applicable": score >= min_score,
        "matches": matches,
    }


def detect(root: Path) -> List[Dict[str, Any]]:
    if not root.is_dir():
        raise FileNotFoundError(f"directorio de artefactos inexistente: {root}")
    entries = _walk(root)
    archives = _archives(entries, root)
    try:
        results = [evaluate(profile, root, entries, archives) for profile in iter_profiles()]
    finally:
        for archive in archives:
            archive.close()
    results.sort(key=lambda r: (-r["score"], r["profile_id"]))
    return results
