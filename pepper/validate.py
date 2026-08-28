"""Validación de instancias contra los contratos de schemas/.

Lo usan los agentes para comprobar sus borradores (perfiles, parsers, session.json,
environment.json) antes de darlos por buenos.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from pepper import SCHEMAS_DIR

SCHEMA_NAMES = ("event", "environment", "flow", "parser", "profile", "runtime-discovery", "session")
_BY_FILENAME = {
    "profile.json": "profile",
    "session.json": "session",
    "environment.json": "environment",
    "flow.json": "flow",
    "runtime-discovery.json": "runtime-discovery",
    "events.jsonl": "event",
}
_MAX_ERRORS = 20


def guess_schema(path: Path) -> Optional[str]:
    if path.name in _BY_FILENAME:
        return _BY_FILENAME[path.name]
    if path.parent.name == "parsers" and path.suffix == ".json":
        return "parser"
    return None


def load_schema(name: str) -> dict:
    if name not in SCHEMA_NAMES:
        raise ValueError(f"schema desconocido: {name!r} (válidos: {', '.join(SCHEMA_NAMES)})")
    return json.loads((SCHEMAS_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))


def validate_instance(instance: object, schema_name: str) -> List[str]:
    """→ lista de errores (vacía si valida). Lanza ImportError si falta jsonschema."""
    import jsonschema  # noqa: WPS433 — dependencia opcional, se reporta al usuario si falta

    validator = jsonschema.Draft202012Validator(load_schema(schema_name))
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
    messages = []
    for error in errors[:_MAX_ERRORS]:
        where = "/".join(str(part) for part in error.absolute_path) or "(raíz)"
        messages.append(f"{where}: {error.message}")
    if len(errors) > _MAX_ERRORS:
        messages.append(f"… y {len(errors) - _MAX_ERRORS} errores más")
    return messages


def validate_file(path: Path, schema_name: Optional[str] = None) -> List[str]:
    schema_name = schema_name or guess_schema(path)
    if schema_name is None:
        raise ValueError(f"no sé qué schema aplica a {path.name}; indícalo con --schema")
    if not path.is_file():
        raise FileNotFoundError(f"no existe {path}")
    if schema_name == "event":
        errors: List[str] = []
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                errors.extend(f"línea {number} · {e}" for e in validate_instance(json.loads(line), "event"))
            except ValueError as error:
                errors.append(f"línea {number}: JSON inválido — {error}")
        return errors
    try:
        instance = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        return [f"JSON inválido — {error}"]
    return validate_instance(instance, schema_name)
