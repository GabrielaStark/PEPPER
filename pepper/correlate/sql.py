"""Forma de una sentencia SQL: operación, tabla y secuencia. Genérico, no depende del motor."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

_OPERATION_BY_KEYWORD = {
    "SELECT": "SELECT",
    "WITH": "SELECT",
    "INSERT": "INSERT",
    "UPDATE": "UPDATE",
    "DELETE": "DELETE",
    "MERGE": "UPDATE",
    "CALL": "PROCEDURE",
    "EXEC": "PROCEDURE",
    "EXECUTE": "PROCEDURE",
    "CREATE": "DDL",
    "ALTER": "DDL",
    "DROP": "DDL",
    "TRUNCATE": "DDL",
    "BEGIN": "TRANSACTION",
    "START": "TRANSACTION",
    "COMMIT": "TRANSACTION",
    "ROLLBACK": "TRANSACTION",
}

_TABLE_PATTERNS = {
    "INSERT": re.compile(r"^INSERT\s+INTO\s+([\w.\"]+)", re.IGNORECASE),
    "UPDATE": re.compile(r"^(?:UPDATE|MERGE\s+INTO)\s+(?:ONLY\s+)?([\w.\"]+)", re.IGNORECASE),
    "DELETE": re.compile(r"^DELETE\s+FROM\s+(?:ONLY\s+)?([\w.\"]+)", re.IGNORECASE),
    "SELECT": re.compile(r"\bFROM\s+([\w.\"]+)", re.IGNORECASE),
}
_SEQUENCE_RE = re.compile(r"nextval\(\s*'([^']+)'", re.IGNORECASE)


def sql_shape(statement: str) -> Tuple[str, Optional[str], Dict[str, Any]]:
    """→ (operación, tabla, metadata extra). La operación cae en OTHER si no se reconoce."""
    stripped = statement.strip().rstrip(";").strip()
    first = stripped.split(None, 1)[0].upper() if stripped else ""
    operation = _OPERATION_BY_KEYWORD.get(first, "OTHER")

    table = None
    pattern = _TABLE_PATTERNS.get(operation)
    if pattern:
        match = pattern.search(stripped)
        if match:
            table = match.group(1).strip('"')

    extra: Dict[str, Any] = {}
    sequence = _SEQUENCE_RE.search(stripped)
    if sequence:
        extra["sequence"] = sequence.group(1)
    return operation, table, extra
