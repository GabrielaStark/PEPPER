"""Lector de respaldos SQL en TEXTO: mysqldump / mariadb-dump y pg_dump en formato plano.

Complementa a `pgdump` (formato custom de PostgreSQL). Un respaldo en texto es un
script: `CREATE TABLE`, `INSERT INTO … VALUES (…),(…)` (o `COPY … FROM stdin`),
vistas, rutinas y triggers. Este lector lo recorre una vez, línea por línea (un
respaldo real puede pesar gigabytes), y saca lo mismo que el lector custom: tablas
con columnas y conteo de filas, las filas completas de las tablas chicas (catálogos),
vistas, funciones/procedimientos y triggers con su cuerpo, y la cabecera (motor,
versión, base de origen, herramienta).

También contesta la pregunta que nadie hacía: **¿esto es la base de la aplicación?**
Un `mysqldump` del esquema `mysql` (cuentas, privilegios, zonas horarias) restaura sin
error y deja un sistema sin datos: `system_only` lo marca para que Map lo declare y
Rehydrate lo detenga en vez de restaurarlo en silencio (2026-09-21, tercer stack).

Sin datos personales aquí: este módulo entrega filas crudas al que lo llama, y es
`systemmap` quien redacta antes de escribir nada.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Tuple

# Tablas del esquema de sistema de MySQL/MariaDB 5.x–8.x. Un respaldo cuyo contenido es
# esto no es el respaldo de ningún sistema: es la administración del motor.
MYSQL_SYSTEM_TABLES = {
    "columns_priv", "component", "db", "default_roles", "engine_cost", "event", "func", "general_log",
    "global_grants", "gtid_executed", "help_category", "help_keyword", "help_relation", "help_topic",
    "innodb_index_stats", "innodb_table_stats", "ndb_binlog_index", "password_history", "plugin", "proc",
    "procs_priv", "proxies_priv", "replication_asynchronous_connection_failover",
    "replication_asynchronous_connection_failover_managed", "replication_group_configuration_version",
    "replication_group_member_actions", "role_edges", "server_cost", "servers", "slave_master_info",
    "slave_relay_log_info", "slave_worker_info", "slow_log", "tables_priv", "time_zone",
    "time_zone_leap_second", "time_zone_name", "time_zone_transition", "time_zone_transition_type", "user",
    "transaction_registry", "column_stats", "index_stats", "table_stats", "roles_mapping", "global_priv",
}
SYSTEM_DATABASES = {"mysql", "sys", "performance_schema", "information_schema", "pg_catalog"}

_MAX_KEEP_ROWS = 300
_CREATE_TABLE = re.compile(r"(?is)^CREATE\s+(?:TEMPORARY\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([`\"\w.]+)\s*\((.*)\)\s*([^()]*)$")
_INSERT = re.compile(r"(?is)^INSERT\s+(?:IGNORE\s+)?INTO\s+([`\"\w.]+)\s*(\([^)]*\))?\s*VALUES\s*(.*)$")
_COPY = re.compile(r"(?i)^COPY\s+([`\"\w.]+)\s*\(([^)]*)\)\s+FROM\s+stdin;?\s*$")
_CREATE_VIEW = re.compile(r"(?is)^CREATE\s+(?:OR\s+REPLACE\s+)?(?:ALGORITHM=\w+\s+)?(?:DEFINER=\S+\s+)?(?:SQL\s+SECURITY\s+\w+\s+)?VIEW\s+([`\"\w.]+)\s+(?:\([^)]*\)\s+)?AS\s+(.*)$")
_CREATE_ROUTINE = re.compile(r"(?is)^CREATE\s+(?:OR\s+REPLACE\s+)?(?:DEFINER=\S+\s+)?(FUNCTION|PROCEDURE)\s+([`\"\w.]+)\s*\((.*)$")
_CREATE_TRIGGER = re.compile(r"(?is)^CREATE\s+(?:OR\s+REPLACE\s+)?(?:CONSTRAINT\s+)?(?:DEFINER=\S+\s+)?TRIGGER\s+([`\"\w.]+)\s+(BEFORE|AFTER|INSTEAD\s+OF)\s+(\w+(?:\s+OR\s+\w+)*)\s+ON\s+([`\"\w.]+)")
_DEFINER = re.compile(r"DEFINER=`([^`]+)`@`[^`]*`")
_OWNER_TO = re.compile(r"(?i)^ALTER\s+\w+\s+\S+\s+OWNER\s+TO\s+([\w\"]+);?$")
_VERSIONED = re.compile(r"^/\*!\d{5}\s?(.*?)\s?\*/;?$", re.S)
_VALUE_TOKEN = re.compile(r"'(?:[^'\\]|\\.|'')*'|\"(?:[^\"\\]|\\.)*\"|NULL\b|[^,()'\"]+|[(),]", re.I)


@dataclass
class SqlTable:
    name: str
    schema: str = ""
    columns: List[str] = field(default_factory=list)
    count: int = 0
    rows: List[List[Optional[str]]] = field(default_factory=list)   # solo mientras quepan (catálogos)
    overflow: bool = False
    definition: str = ""

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.name}" if self.schema and self.schema not in ("", "public") else self.name


@dataclass
class SqlDumpInfo:
    dialect: str = "unknown"          # mysql · postgresql
    tool: str = ""                    # mysqldump · mariadb-dump · pg_dump
    tool_version: str = ""
    server_version: str = ""
    dbname: str = ""
    source_host: str = ""
    tables: Dict[str, SqlTable] = field(default_factory=dict)
    views: List[Tuple[str, str]] = field(default_factory=list)
    routines: List[Tuple[str, str, str]] = field(default_factory=list)     # (kind, name, definition)
    triggers: List[Tuple[str, str, str, str]] = field(default_factory=list)  # (name, event, table, definition)
    owners: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def system_only(self) -> bool:
        """El respaldo es el esquema de administración del motor, no una base de aplicación."""
        if self.dbname.lower() in SYSTEM_DATABASES and self.dbname:
            return True
        names = [t.name.lower() for t in self.tables.values()]
        if not names:
            return False
        system = sum(1 for n in names if n in MYSQL_SYSTEM_TABLES)
        return system >= max(3, int(len(names) * 0.8)) and "user" in names and "db" in names


def _unquote(name: str) -> Tuple[str, str]:
    """`\"public\".\"tabla\"` / `` `base`.`tabla` `` → (esquema, tabla)."""
    parts = [p.strip("`\" ") for p in re.split(r"\.(?=[`\"\w])", name.strip())]
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    return "", parts[-1]


def is_sql_dump(path: Path) -> bool:
    """Texto, y con la firma de mysqldump/mariadb-dump/pg_dump o un CREATE TABLE en los primeros KB."""
    try:
        with path.open("rb") as handle:
            head = handle.read(65536)
    except OSError:
        return False
    if b"\x00" in head or head[:5] == b"PGDMP":
        return False
    text = head.decode("utf-8", errors="replace")
    return bool(re.search(r"(?i)(MySQL dump|MariaDB dump|PostgreSQL database dump|^\s*CREATE TABLE)", text, re.M))


def _header(text: str, info: SqlDumpInfo) -> None:
    m = re.search(r"--\s+(MySQL|MariaDB) dump\s+([\d.]+)\s+Distrib\s+([\w.-]+)", text)
    if m:
        info.dialect = "mysql"
        info.tool = "mysqldump" if m.group(1) == "MySQL" else "mariadb-dump"
        # "Distrib 10.11.14-MariaDB": la versión es la parte numérica; el sabor ya está en `tool`.
        # Con el sufijo dentro, rehydrate fabricaba la imagen `mysql:10.11.14-MariaDB`, que no existe.
        numeric = re.match(r"[\d.]+", m.group(3))
        info.tool_version = numeric.group(0).rstrip(".") if numeric else m.group(3)
    if re.search(r"--\s+PostgreSQL database dump", text):
        info.dialect = "postgresql"
        info.tool = "pg_dump"
    m = re.search(r"--\s+Host:\s*(\S+)\s+Database:\s*(\S*)", text)
    if m:
        info.source_host, info.dbname = m.group(1), m.group(2)
    m = re.search(r"--\s+Server version\s+([\d][\w.-]*)", text)
    if m:
        info.server_version = m.group(1)
    m = re.search(r"--\s+Dumped from database version\s+([\d][\w.]*)", text)
    if m:
        info.server_version = m.group(1)
    m = re.search(r"--\s+Dumped by pg_dump version\s+([\d][\w.]*)", text)
    if m:
        info.tool_version = m.group(1)
    m = re.search(r"(?m)^\\connect\s+\"?([\w-]+)\"?", text) or re.search(r"--\s+Name:\s*([\w-]+);\s*Type:\s*DATABASE\b", text)
    if m and not info.dbname:
        info.dbname = m.group(1)
    if info.dialect == "unknown":
        if re.search(r"(?m)^CREATE TABLE `", text) or "ENGINE=" in text:
            info.dialect = "mysql"
        elif re.search(r"(?m)^(COPY |CREATE TABLE public\.|SET search_path)", text):
            info.dialect = "postgresql"


def _columns_from_create(body: str, dialect: str) -> List[str]:
    """Nombres de columna de un CREATE TABLE (no las constraints), respetando paréntesis anidados."""
    columns: List[str] = []
    depth = 0
    current: List[str] = []
    items: List[str] = []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            items.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if "".join(current).strip():
        items.append("".join(current).strip())
    for item in items:
        first = item.split(None, 1)[0] if item.split() else ""
        upper = first.upper()
        if upper in ("PRIMARY", "UNIQUE", "KEY", "INDEX", "CONSTRAINT", "FOREIGN", "CHECK", "FULLTEXT", "SPATIAL", "EXCLUDE", "LIKE"):
            continue
        columns.append(first.strip("`\""))
    return columns


def split_values(values: str) -> Iterator[List[Optional[str]]]:
    """`(1,'a',NULL),(2,'b\\'c','')` → filas con celdas ya sin comillas; NULL → None."""
    row: Optional[List[Optional[str]]] = None
    depth = 0
    for match in _VALUE_TOKEN.finditer(values):
        token = match.group(0)
        if token == "(":
            depth += 1
            if depth == 1:
                row = []
            continue
        if token == ")":
            depth -= 1
            if depth == 0 and row is not None:
                yield row
                row = None
            continue
        if token == "," or row is None:
            continue
        if depth > 1:
            continue   # expresiones anidadas: no son celdas
        if token.upper() == "NULL":
            row.append(None)
        elif token[0] == "'" and token[-1] == "'" and len(token) >= 2:
            row.append(token[1:-1].replace("''", "'").replace("\\'", "'").replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\"))
        elif token[0] == '"' and token[-1] == '"' and len(token) >= 2:
            row.append(token[1:-1].replace('\\"', '"'))
        else:
            row.append(token.strip())


def _copy_cells(line: str) -> List[Optional[str]]:
    cells: List[Optional[str]] = []
    for raw in line.split("\t"):
        if raw == "\\N":
            cells.append(None)
        else:
            cells.append(raw.replace("\\t", "\t").replace("\\n", "\n").replace("\\\\", "\\"))
    return cells


def _iter_statements(path: Path) -> Iterator[Tuple[str, Optional[Tuple[str, List[str], Iterator[str]]]]]:
    """Sentencias completas, con el delimitador de MySQL y las cadenas `$$` de PostgreSQL en cuenta.

    Un bloque `COPY … FROM stdin` se entrega como ("COPY", (tabla, columnas, iterador de líneas))
    y el iterador DEBE consumirse antes de pedir la siguiente sentencia."""
    delimiter = ";"
    buffer: List[str] = []
    in_dollar: Optional[str] = None
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        iterator = iter(handle)
        for raw in iterator:
            line = raw.rstrip("\r\n")
            stripped = line.strip()
            if not buffer:
                if not stripped or stripped.startswith("--") or stripped.startswith("#"):
                    continue
                if stripped.upper().startswith("DELIMITER "):
                    delimiter = stripped[10:].strip()
                    continue
                copy = _COPY.match(stripped)
                if copy:
                    schema, table = _unquote(copy.group(1))
                    columns = [c.strip().strip('"') for c in copy.group(2).split(",")]

                    def rows() -> Iterator[str]:
                        for data in iterator:
                            data = data.rstrip("\r\n")
                            if data == "\\.":
                                return
                            yield data
                    yield "COPY", (table if not schema or schema == "public" else f"{schema}.{table}", columns, rows())
                    continue
            buffer.append(line)
            for tag in re.findall(r"\$[A-Za-z_]*\$", line):
                if in_dollar is None:
                    in_dollar = tag
                elif tag == in_dollar:
                    in_dollar = None
            if in_dollar is None and stripped.endswith(delimiter):
                statement = "\n".join(buffer).strip()
                buffer = []
                statement = statement[: -len(delimiter)].rstrip() if statement.endswith(delimiter) else statement
                # /*!50003 CREATE TRIGGER … */ — el comentario versionado de mysqldump ES la sentencia
                statement = re.sub(r"/\*!\d{5}\s?", "", statement).replace("*/", "").strip() \
                    if statement.startswith("/*!") else statement
                if statement:
                    yield statement, None
    if buffer:
        yield "\n".join(buffer).strip(), None


RowSink = Callable[[str, List[str], List[Optional[str]]], None]


def read_sql_dump(path: Path, keep_rows: int = _MAX_KEEP_ROWS, on_row: Optional[RowSink] = None) -> SqlDumpInfo:
    """Una pasada: cabecera, tablas (columnas, conteo, filas de las chicas), vistas, rutinas, triggers.

    `on_row(tabla, columnas, celdas)` recibe TODAS las filas conforme se leen (para contar
    distribuciones sin guardarlas); `keep_rows` acota cuántas se conservan por tabla."""
    info = SqlDumpInfo()
    with path.open("rb") as handle:
        _header(handle.read(65536).decode("utf-8", errors="replace"), info)
    owners: List[str] = []

    def table_for(ref: str) -> SqlTable:
        schema, name = _unquote(ref)
        key = name if not schema or schema == "public" else f"{schema}.{name}"
        table = info.tables.get(key)
        if table is None:
            table = info.tables[key] = SqlTable(name=name, schema=schema)
        return table

    def feed(table: SqlTable, cells: List[Optional[str]]) -> None:
        table.count += 1
        if not table.overflow:
            if len(table.rows) < keep_rows:
                table.rows.append(cells)
            else:
                table.overflow = True
                table.rows = []
        if on_row is not None:
            on_row(table.qualified, table.columns, cells)

    for statement, copy in _iter_statements(path):
        if copy is not None:
            name, columns, rows = copy
            table = table_for(name)
            if not table.columns:
                table.columns = columns
            for line in rows:
                feed(table, _copy_cells(line))
            continue
        head = statement[:16].upper()
        if head.startswith("INSERT"):
            m = _INSERT.match(statement)
            if m:
                table = table_for(m.group(1))
                if m.group(2) and not table.columns:
                    table.columns = [c.strip().strip('`" ') for c in m.group(2).strip("()").split(",")]
                for cells in split_values(m.group(3)):
                    feed(table, cells)
            continue
        if head.startswith("CREATE"):
            m = _CREATE_TABLE.match(statement)
            if m and not re.match(r"(?i)^CREATE\s+(?:OR\s+REPLACE\s+)?(?:ALGORITHM|DEFINER|VIEW)", statement):
                table = table_for(m.group(1))
                table.columns = _columns_from_create(m.group(2), info.dialect)
                table.definition = statement[:2000]
                continue
            m = _CREATE_VIEW.match(statement)
            if m:
                info.views.append((_unquote(m.group(1))[1], m.group(2).strip()[:4000]))
                owners += _DEFINER.findall(statement)
                continue
            m = _CREATE_ROUTINE.match(statement)
            if m:
                info.routines.append((m.group(1).lower(), _unquote(m.group(2))[1], statement[:6000]))
                owners += _DEFINER.findall(statement)
                continue
            m = _CREATE_TRIGGER.match(statement)
            if m:
                info.triggers.append((_unquote(m.group(1))[1], f"{m.group(2)} {m.group(3)}".upper(),
                                      _unquote(m.group(4))[1], statement[:6000]))
                owners += _DEFINER.findall(statement)
                continue
            if re.match(r"(?i)^CREATE\s+DATABASE\s", statement) and not info.dbname:
                m2 = re.search(r"(?i)DATABASE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"]?([\w-]+)", statement)
                if m2:
                    info.dbname = m2.group(1)
            continue
        if head.startswith("USE ") and not info.dbname:
            info.dbname = statement[4:].strip().strip("`\"")
            continue
        m = _OWNER_TO.match(statement)
        if m:
            owners.append(m.group(1).strip('"'))
    info.owners = sorted(set(owners))
    return info
