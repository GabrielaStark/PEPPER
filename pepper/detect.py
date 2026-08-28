"""Detección de perfil: evalúa las señales de `detection.signals` de cada perfil sobre un directorio de artefactos.

Es la parte determinística de Inspect. Determina si un legacy cae en el escalón 1
(hay perfil validado aplicable) o si el agente debe identificar el stack y redactar
un borrador de perfil.
"""

from __future__ import annotations

import re
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Dict, List

from pepper.profiles import Profile, iter_profiles

_SKIP_DIRS = {".git", "node_modules", "__pycache__", "target"}
_MAX_CONTENT_BYTES = 5 * 1024 * 1024


def _walk(root: Path) -> List[Path]:
    found: List[Path] = []
    for path in root.rglob("*"):
        if any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        found.append(path)
    return found


def _matches(path: Path, root: Path, pattern: str) -> bool:
    relative = path.relative_to(root).as_posix()
    if fnmatch(relative, pattern) or fnmatch(path.name, pattern):
        return True
    if pattern.startswith("**/"):
        return fnmatch(path.name, pattern[3:])
    return False


def evaluate(profile: Profile, root: Path, entries: List[Path]) -> Dict[str, Any]:
    detection = profile.data.get("detection", {})
    score = 0.0
    matches: List[Dict[str, Any]] = []
    for signal in detection.get("signals", []):
        kind, pattern, weight = signal["type"], signal["pattern"], signal.get("weight", 1)
        hit = None
        if kind in ("file_exists", "extension"):
            hit = next((p for p in entries if p.is_file() and _matches(p, root, pattern)), None)
        elif kind == "directory":
            hit = next((p for p in entries if p.is_dir() and _matches(p, root, pattern)), None)
        elif kind == "file_content":
            regex = re.compile(pattern)
            for candidate in entries:
                if not candidate.is_file() or not _matches(candidate, root, signal.get("file", "*")):
                    continue
                if candidate.stat().st_size > _MAX_CONTENT_BYTES:
                    continue
                if regex.search(candidate.read_text(encoding="utf-8", errors="replace")):
                    hit = candidate
                    break
        if hit is not None:
            score += weight
            matches.append({
                "type": kind,
                "pattern": pattern,
                "weight": weight,
                "hit": hit.relative_to(root).as_posix(),
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
    results.sort(key=lambda r: (-r["score"], r["profile_id"]))
    return results
