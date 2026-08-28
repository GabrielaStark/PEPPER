"""Localización y carga de perfiles (profiles/<id>/profile.json)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from pepper import PROFILES_DIR


@dataclass
class Profile:
    id: str
    dir: Path
    data: dict

    @property
    def status(self) -> str:
        return self.data.get("status", "draft")

    def parser_spec_for(self, source: str) -> Optional[Path]:
        for collector in self.data.get("collectors", []):
            if collector.get("source") == source and collector.get("parser"):
                return self.dir / collector["parser"]
        return None


def load_profile(ref: str) -> Profile:
    """`ref` puede ser un id (profiles/<id>/), una carpeta de perfil o un profile.json."""
    candidate = Path(ref)
    if candidate.is_dir():
        path = candidate / "profile.json"
    elif candidate.is_file():
        path = candidate
    else:
        path = PROFILES_DIR / ref / "profile.json"
    if not path.is_file():
        raise FileNotFoundError(
            f"perfil no encontrado: {ref!r} (no es una ruta ni existe profiles/{ref}/profile.json)"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    return Profile(id=data.get("id", path.parent.name), dir=path.parent, data=data)


def iter_profiles(profiles_dir: Path = PROFILES_DIR) -> Iterator[Profile]:
    for path in sorted(profiles_dir.glob("*/profile.json")):
        yield load_profile(str(path))
