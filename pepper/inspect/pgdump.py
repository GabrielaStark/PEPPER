"""Lector del formato *custom* de `pg_dump` (`-Fc`), sin PostgreSQL instalado.

Por qué existe: la mitad del negocio de un legacy vive en su base — roles,
menús por rol, catálogos de estados y de resultados, triggers y funciones — y
un respaldo en formato custom es un archivo binario que nadie abre sin
`pg_restore`. Este módulo lo abre en Python puro: lista la tabla de contenidos
(tablas, vistas, funciones con su cuerpo, triggers), cuenta filas y devuelve
las filas de las tablas chicas. Solo lee; nunca escribe ni restaura.

Formato (pg_backup_archiver.c): cabecera `PGDMP`, versión, tamaño de enteros
y offsets, compresión, fecha, base y versiones, y después N entradas de TOC.
Cada entrada trae, entre otros, `desc` (TABLE, TABLE DATA, FUNCTION, …), `tag`
(nombre), `defn` (DDL completo) y —en formato custom— la posición del bloque de
datos. Los datos de una tabla son un flujo zlib troceado en bloques
`(longitud, bytes)`, en formato COPY (tab-separado, `\\N` = NULL).

Lo que NO cubre: dumps comprimidos con lz4/zstd (PostgreSQL 16+ con
`--compress=lz4|zstd`), que se declaran como no legibles; y datos de BLOBs.
"""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

_MAGIC = b"PGDMP"
_COMPRESSION = {0: "none", 1: "gzip", 2: "lz4", 3: "zstd"}


@dataclass
class TocEntry:
    dump_id: int
    desc: str
    tag: str
    namespace: str
    defn: str
    has_data: bool
    data_pos: int


@dataclass
class DumpInfo:
    path: Path
    format_version: Tuple[int, int, int]
    compression: str
    dbname: str
    server_version: str
    pg_dump_version: str
    entries: List[TocEntry] = field(default_factory=list)
    _bytes: Optional[bytes] = field(default=None, repr=False, compare=False)

    @property
    def bytes(self) -> bytes:
        """El archivo completo, leído una sola vez (un respaldo de 70 MB se recorre tabla por tabla)."""
        if self._bytes is None:
            self._bytes = self.path.read_bytes()
        return self._bytes

    def by_desc(self, desc: str) -> List[TocEntry]:
        return [e for e in self.entries if e.desc == desc]

    def table_data(self, table: str) -> Optional[TocEntry]:
        for entry in self.entries:
            if entry.desc == "TABLE DATA" and entry.tag == table:
                return entry
        return None


class _Reader:
    def __init__(self, data: bytes):
        self.d = data
        self.p = 0
        self.intsize = 4
        self.offsize = 8

    def byte(self) -> int:
        value = self.d[self.p]
        self.p += 1
        return value

    def int(self) -> int:
        sign = self.byte()
        value = 0
        for i in range(self.intsize):
            value |= self.d[self.p + i] << (8 * i)
        self.p += self.intsize
        return -value if sign else value

    def str(self) -> Optional[str]:
        n = self.int()
        if n < 0:
            return None
        raw = self.d[self.p:self.p + n]
        self.p += n
        return raw.decode("utf-8", errors="replace")

    def offset(self) -> Tuple[int, int]:
        state = self.byte()
        value = 0
        for i in range(self.offsize):
            value |= self.d[self.p + i] << (8 * i)
        self.p += self.offsize
        return state, value


def is_custom_dump(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(5) == _MAGIC
    except OSError:
        return False


def read_toc(path: Path) -> DumpInfo:
    """Cabecera y tabla de contenidos completa (sin tocar los datos)."""
    data = path.read_bytes()
    if data[:5] != _MAGIC:
        raise ValueError(f"{path.name}: no es un respaldo en formato custom de pg_dump (falta la firma PGDMP)")
    r = _Reader(data)
    r.p = 5
    vmaj, vmin, vrev = r.byte(), r.byte(), r.byte()
    version = (vmaj, vmin, vrev)
    r.intsize = r.byte()
    r.offsize = r.byte()
    r.byte()  # formato (1 = custom)
    if version >= (1, 15, 0):
        compression = _COMPRESSION.get(r.byte(), "desconocida")
    else:
        level = r.int()
        compression = "none" if level == 0 else "gzip"
    for _ in range(7):  # sec, min, hour, mday, mon, year, isdst
        r.int()
    dbname = r.str() or ""
    server_version = r.str() or ""
    pg_dump_version = r.str() or ""
    info = DumpInfo(path=path, format_version=version, compression=compression, dbname=dbname,
                    server_version=server_version, pg_dump_version=pg_dump_version, _bytes=data)
    count = r.int()
    for _ in range(count):
        dump_id = r.int()
        has_dumper = r.int()
        r.str()  # tableoid
        r.str()  # oid
        tag = r.str() or ""
        desc = r.str() or ""
        r.int()  # section
        defn = r.str() or ""
        r.str()  # dropStmt
        r.str()  # copyStmt
        namespace = r.str() or ""
        r.str()  # tablespace
        if version >= (1, 14, 0):
            r.str()  # tableam
        if version >= (1, 16, 0):
            r.int()  # relkind
        r.str()  # owner
        r.str()  # withOids (siempre "false")
        while r.str() is not None:  # dependencias, terminadas por NULL
            pass
        _, pos = r.offset()  # extra del formato custom: posición del bloque de datos
        info.entries.append(TocEntry(dump_id=dump_id, desc=desc, tag=tag, namespace=namespace,
                                     defn=defn, has_data=bool(has_dumper), data_pos=pos))
    return info


def iter_rows(info: DumpInfo, entry: TocEntry, limit: Optional[int] = None) -> Iterator[List[Optional[str]]]:
    """Filas de una tabla en formato COPY: lista de columnas, `None` donde había `\\N`."""
    if info.compression not in ("none", "gzip"):
        raise ValueError(f"{info.path.name}: compresión {info.compression} no soportada por el lector")
    data = info.bytes
    r = _Reader(data)
    r.intsize, r.offsize = _sizes(data)
    r.p = entry.data_pos
    block_type = r.byte()
    if block_type != 1:  # BLK_DATA
        raise ValueError(f"{info.path.name}: el bloque de {entry.tag} no es de datos")
    dump_id = r.int()
    if dump_id != entry.dump_id:
        raise ValueError(f"{info.path.name}: bloque de datos desalineado para {entry.tag}")
    decompressor = zlib.decompressobj() if info.compression == "gzip" else None
    pending = b""
    emitted = 0
    while True:
        n = r.int()
        if n <= 0:
            break
        chunk = data[r.p:r.p + n]
        r.p += n
        pending += decompressor.decompress(chunk) if decompressor else chunk
        while b"\n" in pending:
            line, pending = pending.split(b"\n", 1)
            if line == b"\\." or not line:
                continue
            yield _split_copy(line)
            emitted += 1
            if limit is not None and emitted >= limit:
                return
    if decompressor:
        pending += decompressor.flush()
    for line in pending.split(b"\n"):
        if line and line != b"\\.":
            yield _split_copy(line)
            emitted += 1
            if limit is not None and emitted >= limit:
                return


def count_rows(info: DumpInfo, entry: TocEntry) -> int:
    return sum(1 for _ in iter_rows(info, entry))


_COPY_ESCAPES = {b"\\N": None}


def _split_copy(line: bytes) -> List[Optional[str]]:
    cells: List[Optional[str]] = []
    for cell in line.split(b"\t"):
        if cell == b"\\N":
            cells.append(None)
        else:
            text = cell.decode("utf-8", errors="replace")
            cells.append(text.replace("\\t", "\t").replace("\\n", "\n").replace("\\\\", "\\"))
    return cells


def _sizes(data: bytes) -> Tuple[int, int]:
    return data[8], data[9]


_COLUMN_RE = re.compile(r"^\s*(?:\"([^\"]+)\"|([A-Za-z_][A-Za-z0-9_]*))\s+", re.M)


def table_columns(defn: str) -> List[str]:
    """Nombres de columna del `CREATE TABLE` en el orden del COPY."""
    body = defn.split("(", 1)[1] if "(" in defn else ""
    columns: List[str] = []
    depth = 0
    current: List[str] = []
    for char in body:
        if char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                break
            depth -= 1
        if char == "," and depth == 0:
            columns.append("".join(current))
            current = []
        else:
            current.append(char)
    if current:
        columns.append("".join(current))
    names: List[str] = []
    for column in columns:
        stripped = column.strip()
        if not stripped or stripped.upper().startswith(("CONSTRAINT", "PRIMARY KEY", "UNIQUE", "FOREIGN KEY", "CHECK")):
            continue
        match = _COLUMN_RE.match(stripped + " ")
        if match:
            names.append(match.group(1) or match.group(2))
    return names


def trigger_targets(defn: str) -> Dict[str, str]:
    """`CREATE TRIGGER x BEFORE INSERT ON public.t … EXECUTE PROCEDURE public.f()` → tabla, evento, función."""
    match = re.search(r"CREATE TRIGGER\s+\S+\s+(\w+(?:\s+OR\s+\w+)*(?:\s+\w+)*?)\s+ON\s+(?:\w+\.)?(\w+).*?(?:EXECUTE (?:PROCEDURE|FUNCTION))\s+(?:\w+\.)?(\w+)",
                      defn, re.S | re.I)
    if not match:
        return {}
    return {"event": " ".join(match.group(1).split()), "table": match.group(2), "function": match.group(3)}
