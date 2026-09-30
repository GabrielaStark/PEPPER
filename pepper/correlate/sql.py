"""Forma de una sentencia SQL: operación, tabla y secuencia. Genérico, no depende del motor.

Los identificadores llegan como cada dialecto los escribe: `"app"."order"` (PostgreSQL),
`` `user` `` (MySQL), `[dbo].[Users]` (SQL Server), calificados o no por esquema, con
`ONLY` delante en PostgreSQL. Hasta la auditoría 2026-09-29 solo se reconocían letras,
puntos y comillas dobles: `INSERT INTO \\`user\\`` daba tabla None, `FROM ONLY citas` daba
tabla `ONLY`, y `EXEC dbo.sp_x` no decía qué procedimiento. El perfil MySQL validado
sobrevivía porque Hibernate no entrecomilla; una app que sí lo haga perdía la tabla de
cada escritura, y con ella la evidencia de qué guarda.
"""

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

# Un identificador, en cualquier dialecto: `nombre`, "nombre", `nombre` o [nombre], con cero o
# más calificadores de esquema separados por punto (`"app"."order"`, `[dbo].[Users]`, `public.cita`).
_PART = r"(?:\"[^\"]+\"|`[^`]+`|\[[^\]]+\]|[\w$]+)"
_IDENT = rf"({_PART}(?:\s*\.\s*{_PART})*)"

_TABLE_PATTERNS = {
    "INSERT": re.compile(rf"^INSERT\s+(?:IGNORE\s+)?INTO\s+{_IDENT}", re.IGNORECASE),
    "UPDATE": re.compile(rf"^(?:UPDATE|MERGE\s+INTO)\s+(?:ONLY\s+)?{_IDENT}", re.IGNORECASE),
    "DELETE": re.compile(rf"^DELETE\s+FROM\s+(?:ONLY\s+)?{_IDENT}", re.IGNORECASE),
    "SELECT": re.compile(rf"\bFROM\s+(?:ONLY\s+)?{_IDENT}", re.IGNORECASE),
    # el objeto de un `CALL`/`EXEC` es el procedimiento: se devuelve en la misma posición que una tabla
    "PROCEDURE": re.compile(rf"^(?:CALL|EXEC(?:UTE)?)\s+(?:PROCEDURE\s+)?{_IDENT}", re.IGNORECASE),
}
_SEQUENCE_RE = re.compile(r"nextval\(\s*'([^']+)'", re.IGNORECASE)
_QUOTES = "\"`[]"


def normalize_identifier(raw: str) -> str:
    """`"app" . "order"` / `[dbo].[Users]` / `` `user` `` → `app.order` / `dbo.Users` / `user`."""
    parts = re.findall(_PART, raw)
    return ".".join(part.strip(_QUOTES) for part in parts)


def sql_shape(statement: str) -> Tuple[str, Optional[str], Dict[str, Any]]:
    """→ (operación, tabla, metadata extra). La operación cae en OTHER si no se reconoce.

    La tabla se devuelve sin comillas y con su esquema si venía calificada (`esquema.tabla`);
    para PROCEDURE es el procedimiento invocado."""
    stripped = statement.strip().rstrip(";").strip()
    first = stripped.split(None, 1)[0].upper() if stripped else ""
    operation = _OPERATION_BY_KEYWORD.get(first, "OTHER")

    table = None
    pattern = _TABLE_PATTERNS.get(operation)
    if pattern:
        match = pattern.search(stripped)
        if match:
            table = normalize_identifier(match.group(1)) or None

    extra: Dict[str, Any] = {}
    sequence = _SEQUENCE_RE.search(stripped)
    if sequence:
        extra["sequence"] = sequence.group(1)
    return operation, table, extra
