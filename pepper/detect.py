"""Detección de perfil: evalúa las señales de `detection.signals` de cada perfil sobre un directorio de artefactos.

Es la parte determinística de Inspect. Determina si un legacy cae en el escalón 1
(hay perfil validado aplicable) o si el agente debe identificar el stack y redactar
un borrador de perfil.
"""

from __future__ import annotations

import re
import zipfile
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pepper.profiles import Profile, iter_profiles
from pepper.workspace import is_tool_path, tool_paths

_SKIP_DIRS = {".git", "node_modules", "__pycache__", "target"}
_MAX_CONTENT_BYTES = 5 * 1024 * 1024
# Los artefactos desplegables llevan dentro pom.xml, application*.yml, descriptores…
# Las señales también se buscan ahí (un nivel: no se abren jars dentro de wars).
_ARCHIVE_SUFFIXES = {".war", ".jar", ".ear", ".zip"}
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


def _archives(entries: List[Path], root: Path) -> List[Tuple[str, zipfile.ZipFile]]:
    opened = []
    for path in entries:
        if path.is_file() and path.suffix.lower() in _ARCHIVE_SUFFIXES:
            try:
                opened.append((path.relative_to(root).as_posix(), zipfile.ZipFile(path)))
            except (zipfile.BadZipFile, OSError):
                continue
    return opened


def _member_hit(archives: List[Tuple[str, zipfile.ZipFile]], pattern: str, regex: Optional["re.Pattern[str]"] = None,
                file_pattern: Optional[str] = None) -> Optional[str]:
    for archive_name, archive in archives:
        for info in archive.infolist():
            if info.is_dir():
                continue
            if regex is None:
                if _name_matches(info.filename, pattern):
                    return f"{archive_name}!{info.filename}"
                continue
            if not _name_matches(info.filename, file_pattern or "*") or info.file_size > _MAX_MEMBER_BYTES:
                continue
            text = archive.read(info.filename).decode("utf-8", errors="replace")
            if regex.search(text):
                return f"{archive_name}!{info.filename}"
    return None


def evaluate(profile: Profile, root: Path, entries: List[Path]) -> Dict[str, Any]:
    detection = profile.data.get("detection", {})
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
    results = [evaluate(profile, root, entries) for profile in iter_profiles()]
    for _, archive in _archives(entries, root):
        archive.close()
    results.sort(key=lambda r: (-r["score"], r["profile_id"]))
    return results
