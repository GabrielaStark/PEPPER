"""`pepper map`: extracción EXHAUSTIVA y determinística de lo que un sistema ES.

El fallo que este módulo corrige: el discovery solo miraba logs de ejecución y el
artefacto y el respaldo viajaban como "contexto" sin abrirse. La mitad del
negocio de un legacy vive ahí — roles y menús por rol en catálogos de la base,
estados y resultados en constantes del código, reglas en triggers y funciones,
pantallas con sus botones y mensajes de validación. `pepper map` lo saca todo,
igual cada vez, y lo deja en `system-map.json` más una carpeta `map/` legible
que viaja dentro del paquete del discovery.

Agnóstico por construcción (Principio 4 + perfiles como datos): el núcleo
entiende un puñado de MECANISMOS de extracción; los patrones concretos (regex de
tags, prefijos de paquete, claves de config, nombres de columnas de estado) los
declara el perfil en `extractors.json`. Mecanismos:

  archive_url_scan       URLs externas dentro del artefacto (cualquier zip/tar)
  config_hosts           hosts/urls declarados en archivos de configuración
  pg_dump_custom         el respaldo de PostgreSQL leído en Python puro: tablas
                         con conteo y columnas, funciones y triggers con su
                         cuerpo, vistas, catálogos (tablas chicas completas) y
                         distribuciones de columnas de estado
  sql_dump               lo mismo para un respaldo SQL en texto (mysqldump,
                         mariadb-dump, pg_dump plano); detecta el respaldo que
                         es el esquema de sistema del motor y no la aplicación
  jvm_route_annotations  rutas @*Mapping y jobs @Scheduled vía `javap`
  jvm_class_inventory    por clase: métodos públicos, constantes y cadenas de
                         negocio (mensajes, estados) vía `javap -c -constants`
  view_templates         pantallas: título, encabezados, campos, botones y sus
                         acciones, mensajes de validación, condiciones por rol;
                         con el bundle de etiquetas resuelto
  groovy_config_values   Config/DataSource de Grails reconstruidos del bytecode:
                         jobs con su cron y notas de configuración
  groovy_controller_actions  acciones de controladores Grails (closures y métodos)
                         → rutas por convención, con allowedMethods
  groovy_url_mappings    UrlMappings de Grails → rutas declaradas

Fail-honest (como isolate): si falta una herramienta (javap) o un extractor no
puede correr, el mapa se marca `complete=false` y lo dice en `coverage_gaps`.
Un mapa parcial se declara parcial.

Sin datos personales ni secretos: las filas de catálogo redactan columnas y
renglones que parezcan credenciales o datos de personas; las tablas de usuarios
o personas no se vuelcan (solo se cuentan); las cadenas de código que parezcan
credenciales se omiten.
"""

from __future__ import annotations

import html
import json
import math
import re
import tempfile
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from pepper.inspect import jvm

MAP_NAME = "system-map.json"
MAP_DIR = "map"
_MAX_MEMBER_BYTES = 4 * 1024 * 1024
_URL_RE = re.compile(r"https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+")
_REDACTED = "[REDACTADO]"

# Columnas cuyo VALOR no debe viajar: credenciales y datos de personas.
_SENSITIVE_COLUMN_RE = re.compile(
    r"(?i)pass|pwd|contrase|secret|token|credencial|correo|mail|curp|rfc|telefono|celular|nacimiento|cedula|"
    r"nombre|apellido|domicilio|direccion|calle|colonia|nss|imss|clabe|cuenta|tarjeta|fecha_?nac|fnac|sexo|salario|sueldo"
)
# Valores que son datos de una persona o una credencial, en cualquier columna o cadena.
_PII_VALUE_RE = re.compile(
    r"[\w.+-]+@[\w-]+\.[\w.-]+|\b[A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d\b|\b[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}\b|"
    r"\b\d{2,3}[ -]?\d{3}[ -]?\d{2}[ -]?\d{2}\b|\b\d{10}\b|\b\d{11}\b|\b\d{18}\b"
)
# Renglones de tablas parámetro/clave-valor donde la CLAVE delata un secreto.
_SECRET_KEY_RE = re.compile(
    r"(?i)pass(?:word|phrase)?|pwd|psw|contrase|clave|llave|secret|token|credencial|smtp\.user|mail\.user|"
    r"api.?key|\bkey\b|_key|key_|cipher|crypt|aes|des\b|rsa|hmac|salt|seed|\biv\b|firma|sign(?:ature)?|"
    r"keystore|truststore|jks|p12|pfx|privat|auth")
# Cadenas del bytecode que no aportan negocio o pueden ser secretos.
_NOISE_STRING_RE = re.compile(
    r"^(?:[A-Za-z]+:[/\\]|/|\\|<|\{|\[|%|\d+$|[a-z]{1,3}$|yyyy|dd[/-]|HH:|UTF|ISO|null$|"
    r"[A-Z][a-z]+(?:[A-Z][a-z]+)+$|java|org\.|mx\.|com\.|net\.|javax|select |SELECT |from |FROM |"
    r"insert |INSERT |update |UPDATE |delete |DELETE |where |WHERE )"
)
_SECRET_STRING_RE = re.compile(r"(?i)(password|contrase|secret|token|pwd)\s*[:=]")
_PEM_RE = re.compile(r"(?i)-----BEGIN [A-Z ]*PRIVATE KEY|-----BEGIN CERTIFICATE|\bssh-rsa\b")
_JWT_RE = re.compile(r"^eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.")
_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
_B64_RE = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")
# Formas que parecen material criptográfico pero son inofensivas y sí dicen algo del negocio.
_NOT_A_KEY_RE = re.compile(r"://|^[\w.-]+\.[a-z]{2,}$|^/|\\|\s|^\d+$|^[A-Za-z]+$")


def _entropy(value: str) -> float:
    """Bits por carácter. Una llave se ve como ruido; una palabra, no."""
    if not value:
        return 0.0
    total = len(value)
    return -sum((n / total) * math.log2(n / total) for n in Counter(value).values())


def looks_like_secret_value(value: str) -> bool:
    """¿El VALOR es material criptográfico, se llame como se llame?

    El redactor juzgaba el NOMBRE y una forma `password=`. Una constante del bytecode
    con una llave AES literal pasó entera al mapa y al documento del sistema
    (auditoría 2026-09-15). Una llave no tiene espacios, es larga y se ve como ruido;
    eso se puede medir sin saber cómo se llama.
    """
    text = value.strip().strip('"').strip()
    if not text:
        return False
    if _PEM_RE.search(text) or _JWT_RE.match(text):
        return True
    if _NOT_A_KEY_RE.search(text):
        return False
    if len(text) >= 16 and _HEX_RE.match(text) and len(text) % 8 == 0:
        return True
    # Una llave mezcla clases de caracteres. Un identificador largo no: `lldocumentoexpediente=`
    # es un campo en un toString(), y redactarlo perdería el negocio que el mapa existe para contar.
    clases = sum((any(c.islower() for c in text), any(c.isupper() for c in text), any(c.isdigit() for c in text)))
    if clases < 2:
        return False
    if len(text) >= 16 and _B64_RE.match(text) and _entropy(text) >= 3.3:
        return True
    return len(text) >= 12 and _entropy(text) >= 4.0


# ---------------------------------------------------------------- utilidades

def _members(artifact: Path):
    """(nombre, bytes) de cada miembro de texto del artefacto (zip) o directorio."""
    if artifact.is_dir():
        for path in sorted(artifact.rglob("*")):
            if path.is_file() and not path.is_symlink() and path.stat().st_size <= _MAX_MEMBER_BYTES:
                yield path.relative_to(artifact).as_posix(), path.read_bytes()
        return
    if artifact.is_file() and zipfile.is_zipfile(artifact):
        with zipfile.ZipFile(artifact) as archive:
            for info in archive.infolist():
                if not info.is_dir() and info.file_size <= _MAX_MEMBER_BYTES:
                    yield info.filename, archive.read(info.filename)


_match_any = jvm.match_any


# ------------------------------------------------------------- mecanismos

def _extract_archive_urls(artifact: Path, spec: Dict[str, Any], report: "MapReport") -> None:
    """URLs externas incrustadas en el artefacto, agrupadas por host."""
    include = spec.get("member_patterns", [r"\.(yml|yaml|properties|xml|xhtml|js|class|jrxml)$"])
    exclude_hosts = spec.get("exclude_host_patterns", [])
    seen: Dict[str, str] = {}
    for name, data in _members(artifact):
        if not _match_any(include, name):
            continue
        text = data.decode("latin-1", errors="replace")
        for match in _URL_RE.findall(text):
            host = re.sub(r"^https?://", "", match).split("/")[0]
            if not host or _match_any(exclude_hosts, host):
                continue
            key = host.lower()
            if key not in seen:
                seen[key] = f"{artifact.name}!{name}"
    for host, evidence in sorted(seen.items()):
        report.external.append({
            "name": host, "kind": _guess_kind(host, spec), "target": host, "evidence": evidence,
        })


def _guess_kind(host: str, spec: Dict[str, Any]) -> str:
    for kind, patterns in (spec.get("kind_hints") or {}).items():
        if _match_any(patterns, host):
            return kind
    return "web"


def _extract_config_hosts(artifact: Path, spec: Dict[str, Any], report: "MapReport") -> None:
    """Hosts/urls en archivos de configuración declarados (line: key: value)."""
    config_globs = spec.get("config_patterns", [r"application.*\.(yml|yaml|properties)$"])
    key_re = re.compile(spec.get("host_key_pattern", r"(?i)(url|host|smtp|uri|endpoint)"))
    secret_re = re.compile(r"(?i)pass|pwd|contrase|secret|token")
    for name, data in _members(artifact):
        if not _match_any(config_globs, name):
            continue
        for lineno, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or secret_re.search(stripped):
                continue
            if ":" not in stripped:
                continue
            key, _, value = stripped.partition(":")
            value = value.strip()
            # usuario:clave@host dentro de una URL (jdbc, postgres://): la clave no viaja al mapa
            value = re.sub(r"(?i)(://[^\s/:@]+:)[^\s@]+@", r"\1[REDACTADO]@", value)
            if key_re.search(key) and value and ("//" in value or "." in value):
                report.notes.append(f"config {name}:{lineno} · {key.strip()}: {value[:80]}")


# ---- el respaldo ---------------------------------------------------------

def _redact_row(columns: List[str], row: List[Optional[str]]) -> List[Optional[str]]:
    """Redacta columnas sensibles por nombre y renglones clave-valor cuya clave delate un secreto."""
    secret_row = any(cell and _SECRET_KEY_RE.search(cell) and len(cell) < 64 for cell in row)
    out: List[Optional[str]] = []
    for index, cell in enumerate(row):
        name = columns[index] if index < len(columns) else ""
        if cell is None:
            out.append(None)
        elif _SENSITIVE_COLUMN_RE.search(name):
            out.append(_REDACTED)
        elif secret_row and not (_SECRET_KEY_RE.search(cell) and len(cell) < 64) and not cell.isdigit():
            out.append(_REDACTED)
        elif _PII_VALUE_RE.search(cell):
            out.append(_PII_VALUE_RE.sub(_REDACTED, cell))
        else:
            out.append(cell if len(cell) <= 400 else cell[:400] + "…")
    return out


def _extract_pg_dump(spec: Dict[str, Any], report: "MapReport", dump: Optional[Path]) -> None:
    from pepper.inspect import pgdump

    if dump is None or not dump.is_file():
        report.gap("pg_dump_custom: no se encontró el respaldo (--dump); sin él no hay inventario de datos")
        return
    if not pgdump.is_custom_dump(dump):
        report.gap(f"pg_dump_custom: {dump.name} no es un respaldo en formato custom de pg_dump (-Fc); "
                   "conviértelo o declara otro mecanismo")
        return
    try:
        info = pgdump.read_toc(dump)
    except ValueError as error:
        report.gap(f"pg_dump_custom: {error}")
        return
    ref = f"{dump.name} (lector pg_dump)"
    report.notes.append(f"respaldo {dump.name} · base de origen: {info.dbname}")
    report.notes.append(f"respaldo {dump.name} · versión del servidor de origen: {info.server_version}")
    report.notes.append(f"respaldo {dump.name} · generado por pg_dump: {info.pg_dump_version}")
    if info.compression not in ("none", "gzip"):
        report.gap(f"pg_dump_custom: compresión {info.compression} no soportada; solo se leyó la estructura, no los datos")

    catalog_max = int(spec.get("catalog_max_rows", 300))
    catalog_include = spec.get("catalog_include_patterns", [])  # vacío = toda tabla chica
    catalog_exclude = spec.get("catalog_exclude_patterns",
                               [r"(?i)usuario|user|persona|trabajador|empleado|cliente|testigo|beneficiario|patron|empresa|"
                                r"ciudadano|contacto|proveedor|medico|paciente|solicitante|asegurado|derechohabiente|domicilio|direccion|telefono"])
    state_re = re.compile(spec.get("state_column_pattern", r"(?i)estatus|status|estado$|^tipo|_tipo|tipo_|nivel|sector|rol$"))
    top_n = int(spec.get("distribution_top", 15))
    date_re = re.compile(spec.get("date_column_pattern", r"(?i)^fecha|^fc[a-z]|_fecha|fecha$"))

    tables = {(e.namespace, e.tag): e for e in info.by_desc("TABLE")}   # public.cliente ≠ archivo.cliente
    readable = info.compression in ("none", "gzip")
    counts: Dict[Tuple[str, str], int] = {}

    def table_name(namespace: str, tag: str) -> str:
        return tag if namespace in ("", "public") else f"{namespace}.{tag}"

    for entry in info.by_desc("TABLE DATA"):
        table = tables.get((entry.namespace, entry.tag))
        columns = pgdump.table_columns(table.defn) if table else []
        try:
            rows_count = pgdump.count_rows(info, entry) if readable else -1
        except ValueError as error:
            # fail-honest: una tabla ilegible es un hueco declarado, no un mapa sin ella ni un traceback
            report.gap(f"pg_dump_custom: {entry.tag}: {error}")
            rows_count = -1
        counts[(entry.namespace, entry.tag)] = rows_count
        item: Dict[str, Any] = {"kind": "table", "name": table_name(entry.namespace, entry.tag), "columns": columns, "evidence": ref}
        if rows_count >= 0:
            item["count"] = rows_count
        report.data.append(item)
        if not readable:
            continue
        is_catalog = (rows_count <= catalog_max and not _match_any(catalog_exclude, entry.tag)
                      and (not catalog_include or _match_any(catalog_include, entry.tag)))
        if is_catalog:
            try:
                rows = [_redact_row(columns, row) for row in pgdump.iter_rows(info, entry, limit=catalog_max)]
            except ValueError as error:
                report.gap(f"pg_dump_custom: {entry.tag}: {error}")
                continue
            report.catalogs.append({"table": entry.tag, "columns": columns, "count": rows_count,
                                    "rows": rows, "evidence": ref})
        elif rows_count > 0:
            # Tablas grandes, y también las chicas que no se vuelcan (personas): sus
            # columnas de estado/tipo y sus fechas (por año) se cuentan sin exponer filas.
            state_columns = [(i, c, "state") for i, c in enumerate(columns)
                             if state_re.search(c) and not _SENSITIVE_COLUMN_RE.search(c)]
            state_columns += [(i, c, "year") for i, c in enumerate(columns)
                              if date_re.search(c) and not _SENSITIVE_COLUMN_RE.search(c)]
            if state_columns:
                counters = {i: Counter() for i, _, _ in state_columns}
                for row in pgdump.iter_rows(info, entry):
                    for i, _, mode in state_columns:
                        if i < len(row):
                            value = row[i]
                            if mode == "year" and value:
                                value = value[:4] if value[:4].isdigit() else "∅"
                            counters[i][value if value is not None else "∅"] += 1
                for i, column, mode in state_columns:
                    _emit_distribution(report, entry.tag, column, mode, rows_count, counters[i], top_n, ref)
    for key in sorted(t for t in tables if t not in counts):
        report.data.append({"kind": "table", "name": table_name(*key), "columns": pgdump.table_columns(tables[key].defn),
                            "detail": "sin datos en el respaldo", "evidence": ref})
    for entry in info.by_desc("VIEW"):
        report.data.append({"kind": "view", "name": entry.tag, "definition": entry.defn.strip(), "evidence": ref})
    for entry in info.by_desc("FUNCTION"):
        report.data.append({"kind": "function", "name": entry.tag, "definition": entry.defn.strip(), "evidence": ref})
    for entry in info.by_desc("TRIGGER"):
        target = pgdump.trigger_targets(entry.defn)
        detail = (f"{target.get('event', '?')} en {target.get('table', '?')} → {target.get('function', '?')}()"
                  if target else "regla dura en la base")
        report.data.append({"kind": "trigger", "name": entry.tag, "detail": detail,
                            "definition": entry.defn.strip(), "evidence": ref})
    for entry in info.by_desc("SERVER"):
        report.data.append({"kind": "foreign_server", "name": entry.tag,
                            "detail": "servidor foráneo (dblink/postgres_fdw): interconexión directa a otra base",
                            "evidence": ref})
    for desc, label in (("SEQUENCE", "sequence"), ("EXTENSION", "extension")):
        n = len(info.by_desc(desc))
        if n:
            report.data.append({"kind": "summary", "name": label, "count": n,
                                "detail": f"{n} {label}(s) en el respaldo", "evidence": ref})



def _emit_distribution(report: "MapReport", table: str, column: str, mode: str, total: int,
                       counter: Counter, top_n: int, ref: str) -> None:
    """Una columna de estado/tipo (o el año de una fecha) como distribución, si de verdad es un estado."""
    if len(counter) > 60:
        return  # no es un estado: demasiados valores distintos
    top = counter.most_common(top_n)
    if mode == "year":
        top = sorted(top, key=lambda kv: str(kv[0]))
    report.distributions.append({
        "table": table, "column": column + (" (año)" if mode == "year" else ""),
        "total": total, "distinct": len(counter),
        "values": [{"value": (v[:80] if isinstance(v, str) else v), "count": n} for v, n in top],
        "evidence": ref,
    })


def _dump_patterns(spec: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "catalog_max": int(spec.get("catalog_max_rows", 300)),
        "catalog_include": spec.get("catalog_include_patterns", []),
        "catalog_exclude": spec.get("catalog_exclude_patterns",
                                    [r"(?i)usuario|user|persona|trabajador|empleado|cliente|testigo|beneficiario|patron|empresa|"
                                     r"ciudadano|contacto|proveedor|medico|paciente|solicitante|asegurado|derechohabiente|domicilio|direccion|telefono"]),
        "state_re": re.compile(spec.get("state_column_pattern", r"(?i)estatus|status|estado$|^tipo|_tipo|tipo_|nivel|sector|rol$")),
        "date_re": re.compile(spec.get("date_column_pattern", r"(?i)^fecha|^fc[a-z]|_fecha|fecha$|_date$|^date_|date$")),
        "top_n": int(spec.get("distribution_top", 15)),
    }


def _extract_sql_dump(spec: Dict[str, Any], report: "MapReport", dump: Optional[Path]) -> None:
    """Respaldo SQL en texto (mysqldump / mariadb-dump / pg_dump plano): una pasada."""
    from pepper.inspect import sqldump

    if dump is None or not dump.is_file():
        report.gap("sql_dump: no se encontró el respaldo (--dump); sin él no hay inventario de datos")
        return
    if not sqldump.is_sql_dump(dump):
        report.gap(f"sql_dump: {dump.name} no es un respaldo SQL en texto (mysqldump/mariadb-dump/pg_dump plano)")
        return
    pat = _dump_patterns(spec)
    ref = f"{dump.name} (lector sql_dump)"
    # Distribuciones sobre la marcha: el conteo por columna de estado se acumula fila a fila,
    # sin guardar las filas (un respaldo de aplicación puede traer millones).
    counters: Dict[str, Dict[int, Tuple[str, str, Counter]]] = {}
    saturated: Dict[str, set] = {}

    def on_row(table: str, columns: List[str], cells: List[Optional[str]]) -> None:
        if table not in counters:
            cols: Dict[int, Tuple[str, str, Counter]] = {}
            for i, c in enumerate(columns):
                if _SENSITIVE_COLUMN_RE.search(c):
                    continue
                if pat["state_re"].search(c):
                    cols[i] = (c, "state", Counter())
                elif pat["date_re"].search(c):
                    cols[i] = (c, "year", Counter())
            counters[table] = cols
            saturated[table] = set()
        for i, (_, mode, counter) in counters[table].items():
            if i in saturated[table] or i >= len(cells):
                continue
            value = cells[i]
            if mode == "year" and value:
                value = value[:4] if value[:4].isdigit() else "∅"
            counter[value if value is not None else "∅"] += 1
            if len(counter) > 60:
                saturated[table].add(i)

    try:
        info = sqldump.read_sql_dump(dump, keep_rows=pat["catalog_max"], on_row=on_row)
    except (OSError, ValueError) as error:
        report.gap(f"sql_dump: {dump.name}: {error}")
        return
    report.notes.append(f"respaldo {dump.name} · motor: {info.dialect} {info.server_version or '(versión no declarada)'}")
    report.notes.append(f"respaldo {dump.name} · base de origen: {info.dbname or '(no declarada)'}"
                        + (f" · host de origen: {info.source_host}" if info.source_host else ""))
    if info.tool:
        report.notes.append(f"respaldo {dump.name} · generado por {info.tool} {info.tool_version}".rstrip())
    if info.system_only:
        report.gap(f"sql_dump: {dump.name} es el esquema de SISTEMA del motor (base '{info.dbname or '?'}': "
                   f"{len(info.tables)} tablas como user, db, tables_priv…), NO la base de la aplicación. "
                   "Con él no hay tablas de negocio, catálogos ni volúmenes: consigue el respaldo de la base que el artefacto usa")
        return
    for key, table in info.tables.items():
        item: Dict[str, Any] = {"kind": "table", "name": table.qualified, "columns": table.columns,
                                "count": table.count, "evidence": ref}
        if table.count == 0 and not table.rows:
            item["detail"] = "sin datos en el respaldo"
        report.data.append(item)
        is_catalog = (table.count <= pat["catalog_max"] and not table.overflow
                      and not _match_any(pat["catalog_exclude"], table.name)
                      and (not pat["catalog_include"] or _match_any(pat["catalog_include"], table.name)))
        if is_catalog and table.rows:
            rows = [_redact_row(table.columns, row) for row in table.rows]
            report.catalogs.append({"table": table.qualified, "columns": table.columns, "count": table.count,
                                    "rows": rows, "evidence": ref})
        elif table.count > 0:
            for i, (column, mode, counter) in (counters.get(table.qualified) or {}).items():
                if i in saturated.get(table.qualified, set()):
                    continue
                _emit_distribution(report, table.qualified, column, mode, table.count, counter, pat["top_n"], ref)
    for name, definition in info.views:
        report.data.append({"kind": "view", "name": name, "definition": definition, "evidence": ref})
    for kind, name, definition in info.routines:
        report.data.append({"kind": "function", "name": name, "detail": kind, "definition": definition, "evidence": ref})
    for name, event, table, definition in info.triggers:
        report.data.append({"kind": "trigger", "name": name, "detail": f"{event} en {table}",
                            "definition": definition, "evidence": ref})
    if info.owners:
        report.notes.append(f"respaldo {dump.name} · definers/dueños referidos: {', '.join(info.owners[:10])}")


# ---- el bytecode ---------------------------------------------------------

_MAP_ANN = re.compile(r"annotation\.(RequestMapping|GetMapping|PostMapping|PutMapping|DeleteMapping)\(")


def _extract_jvm_routes(artifact: Path, spec: Dict[str, Any], report: "MapReport",
                        tools: Dict[str, str]) -> None:
    """Rutas @*Mapping y jobs @Scheduled vía javap sobre las clases del artefacto."""
    javap = tools.get("javap")
    if not javap:
        report.gap("jvm_route_annotations: falta `javap` (JDK) en PATH; no se enumeraron rutas ni jobs del bytecode")
        return
    if not (artifact.is_file() and zipfile.is_zipfile(artifact)):
        report.gap("jvm_route_annotations: el artefacto no es un archivo zip/WAR")
        return
    with tempfile.TemporaryDirectory() as tmp:
        ilegibles: List[str] = []
        classes = jvm.collect_classes(artifact, spec.get("class_root", "WEB-INF/classes"),
                                      spec.get("package_prefixes", []), False, Path(tmp), ilegibles)
        for nombre in ilegibles:
            report.gap(f"jvm_route_annotations: no se pudo abrir `{nombre}` dentro del artefacto; sus clases no se leyeron")
        if _report_no_classes("jvm_route_annotations", classes, spec.get("class_root", "WEB-INF/classes"), artifact, report):
            return
        outputs, tool_notes = jvm.javap_outputs(tools, classes, ["-p", "-v"])
        report.notes.extend(f"jvm_route_annotations: {n}" for n in tool_notes)
        _report_unreadable("jvm_route_annotations", classes, outputs, report)
        for fqn, _ in classes:
            out = outputs.get(fqn)
            if out:
                _parse_javap(out, fqn.split(".")[-1], report, spec.get("job_signatures") or {})


def _report_no_classes(mechanism: str, classes, class_root: str, artifact: Path, report: "MapReport") -> bool:
    """Cero clases bajo `class_root` no es "este sistema no tiene clases": es que el perfil
    busca donde no están. Un WAR las guarda en `WEB-INF/classes`; un fat JAR de Spring Boot,
    en `BOOT-INF/classes`. Con el perfil equivocado el mapa salía con 0 clases, 0 pantallas y
    0 rutas — y se declaraba COMPLETO (auditoría 2026-09-14, segundo stack). Es un hueco.
    """
    if classes:
        return False
    report.gap(f"{mechanism}: ninguna clase bajo '{class_root}' en {artifact.name}. El perfil busca ahí y el "
               f"artefacto no las tiene: un WAR usa 'WEB-INF/classes' y un fat JAR de Spring Boot "
               f"'BOOT-INF/classes'. Corrige `extractors.class_root` del perfil o usa el perfil del stack correcto")
    return True


def _report_unreadable(mechanism: str, classes, outputs: Dict[str, str], report: "MapReport") -> None:
    """Una clase que javap no pudo leer (artefacto compilado con un JDK más nuevo, .class
    corrupto) desaparecía en silencio y el mapa salía COMPLETO. Fail-honest: es un hueco
    con nombre y apellido."""
    missing = [fqn for fqn, _ in classes if fqn not in outputs]
    if missing:
        shown = ", ".join(missing[:5]) + (f" … y {len(missing) - 5} más" if len(missing) > 5 else "")
        report.gap(f"{mechanism}: {len(missing)} de {len(classes)} clases no se pudieron leer con javap "
                   f"(¿JDK más viejo que el del artefacto? ¿.class corrupto?): {shown}")


def _parse_javap(out: str, class_name: str, report: "MapReport",
                 job_signatures: Optional[Dict[str, str]] = None) -> None:
    """Extrae verbo+path+handler de las anotaciones de mapeo, y cron de @Scheduled.

    Distingue por indentación la anotación de CLASE (la ruta base, p. ej.
    `@RequestMapping("/api/rest")`) de las de MÉTODO: la base se antepone a las
    rutas de esa clase y no se emite como entrada propia — si no, aparece un
    endpoint fantasma `/api/rest` y los demás salen sin su prefijo.
    """
    method = ""
    verb = ""
    pend = False
    pend_class_level = False
    sched = False
    base_path = ""
    routes: List[Dict[str, Any]] = []
    for line in out.splitlines():
        s = line.strip()
        indent = len(line) - len(line.lstrip())
        m = re.match(r"^(public|protected).*\b(\w+)\(", s)
        if m:
            method = m.group(2)
        if "annotation.Scheduled" in s:
            sched = True
        if sched and ("cron=" in s or "value=[" in s or "fixedRate=" in s or "fixedDelay=" in s):
            val = s.split("=", 1)[1].strip().strip("[]").strip('"')
            job = {"name": class_name, "schedule": val, "evidence": f"{class_name}.class (javap @Scheduled)"}
            if method:
                job["detail"] = f"método {method}()"
            signature = (job_signatures or {}).get(class_name)
            if signature:
                job["signature"] = signature
            if not any(j["name"] == class_name and j["schedule"] == val for j in report.jobs):
                report.jobs.append(job)  # el cron aparece dos veces en el bytecode (anotación + constante)
            sched = False
        am = _MAP_ANN.search(s)
        if am:
            verb = am.group(1)
            pend = True
            pend_class_level = indent <= 4  # las de método van más indentadas
        if pend and "value=[" in s:
            path = s.split("value=[", 1)[1].split("]")[0].strip().strip('"')
            if pend_class_level:
                base_path = path.rstrip("/")
            else:
                http = {"RequestMapping": "", "GetMapping": "GET", "PostMapping": "POST",
                        "PutMapping": "PUT", "DeleteMapping": "DELETE"}.get(verb, "")
                routes.append({"kind": "rest_endpoint" if "Rest" in class_name else "http_route",
                               "method": http, "path": path, "handler": f"{class_name}.{method}",
                               "evidence": f"{class_name}.class (javap {verb})"})
            pend = False
    for route in routes:
        if base_path and not route["path"].startswith(base_path):
            route["path"] = base_path + route["path"]
        report.entrypoints.append(route)


_ACCESSOR_RE = re.compile(r"^(get|set|is)[A-Z]|^(equals|hashCode|toString|canEqual|builder|lambda\$)")
_CRYPTO_RE = re.compile(r"javax/crypto/|java/security/(?:Key|SecretKey|spec/|KeyStore|Signature|MessageDigest)|javax\$crypto\$|java\$security\$")


def _extract_jvm_classes(artifact: Path, spec: Dict[str, Any], report: "MapReport",
                         tools: Dict[str, str]) -> None:
    """Por clase: métodos públicos, constantes `static final` y cadenas de negocio del bytecode."""
    javap = tools.get("javap")
    if not javap:
        report.gap("jvm_class_inventory: falta `javap` (JDK) en PATH; no se inventariaron las clases")
        return
    if not (artifact.is_file() and zipfile.is_zipfile(artifact)):
        report.gap("jvm_class_inventory: el artefacto no es un archivo zip/WAR")
        return
    kinds: Dict[str, str] = spec.get("class_kinds") or {}
    max_strings = int(spec.get("max_strings_per_class", 80))
    with tempfile.TemporaryDirectory() as tmp:
        ilegibles: List[str] = []
        classes = jvm.collect_classes(artifact, spec.get("class_root", "WEB-INF/classes"),
                                      spec.get("package_prefixes", []), bool(spec.get("include_own_libs", True)), Path(tmp), ilegibles)
        for nombre in ilegibles:
            report.gap(f"jvm_class_inventory: no se pudo abrir `{nombre}` dentro del artefacto; sus clases no se leyeron")
        if _report_no_classes("jvm_class_inventory", classes, spec.get("class_root", "WEB-INF/classes"), artifact, report):
            return
        outputs, tool_notes = jvm.javap_outputs(tools, classes, ["-p", "-c", "-constants"])
        report.notes.extend(f"jvm_class_inventory: {n}" for n in tool_notes)
        _report_unreadable("jvm_class_inventory", classes, outputs, report)
        for fqn, _ in classes:
            out = outputs.get(fqn)
            if not out:
                continue
            simple = fqn.rsplit(".", 1)[-1]
            if "$" in simple:
                continue  # clases internas y lambdas: ruido
            kind = "other"
            for label, pattern in kinds.items():
                if re.search(pattern, fqn):
                    kind = label
                    break
            # Una clase que cifra o firma (javax.crypto, java.security) tiene sus llaves como
            # cadenas literales: `passphrase`, la semilla de un DES, la sal de un hash. El
            # redactor por valor no las reconoce cuando son una palabra (2026-09-21, un
            # BlowfishCodec con su frase escrita). Toda cadena de una clase así se omite: el
            # mapa dice que la clase existe y qué hace, no con qué llave.
            cryptographic = bool(_CRYPTO_RE.search(out))
            methods: List[str] = []
            constants: Dict[str, str] = {}
            strings: List[str] = []
            for line in out.splitlines():
                s = line.strip()
                m = re.match(r"^public (?:static |final |abstract |synchronized )*[\w.<>\[\], $?]+ (\w+)\((.*)\)(?: throws .*)?;$", s)
                if m and not _ACCESSOR_RE.match(m.group(1)) and m.group(1) != simple:
                    args = re.sub(r"\b(?:[a-z_]\w*\.)+(?=[A-Z])", "", m.group(2))
                    methods.append(f"{m.group(1)}({args})" if args else f"{m.group(1)}()")
                    continue
                m = re.match(r"^(?:public |private |protected )?static final [\w.<>\[\]]+ (\w+) = (.+);$", s)
                if m:
                    value = m.group(2).strip()
                    if cryptographic or _SECRET_STRING_RE.search(f"{m.group(1)}={value}") or _SECRET_KEY_RE.search(m.group(1)) \
                            or looks_like_secret_value(value):
                        # por ubicación, nunca el valor: que se sepa que ahí hay una llave
                        constants[m.group(1)] = _REDACTED
                    else:
                        constants[m.group(1)] = value.strip('"')[:120]
                    continue
                m = re.search(r"\bldc2?_?w?\s+#\d+\s+// String (.*)$", s)
                if m and not cryptographic:
                    text = m.group(1).strip()
                    if (len(text) >= 4 and re.search(r"[A-Za-zÁÉÍÓÚáéíóúñÑ]", text)
                            and not _NOISE_STRING_RE.search(text) and not _SECRET_STRING_RE.search(text)
                            and not looks_like_secret_value(text)
                            and not _PII_VALUE_RE.search(text) and text not in strings):
                        strings.append(text[:200])
            if not methods and not constants and not strings and not cryptographic:
                continue
            item: Dict[str, Any] = {
                "name": fqn, "kind": kind, "methods": methods, "constants": constants,
                "strings": strings[:max_strings], "evidence": f"{simple}.class (javap -c -constants)",
            }
            if cryptographic:
                item["strings_omitted"] = "clase criptográfica (javax.crypto / java.security): sus cadenas pueden ser llaves y no viajan"
            report.classes.append(item)


# ---- las pantallas -------------------------------------------------------

def _load_bundle(artifact: Path, patterns: List[str]) -> Dict[str, str]:
    bundle: Dict[str, str] = {}
    for name, data in _members(artifact):
        if not _match_any(patterns, name):
            continue
        text = data.decode("latin-1", errors="replace")
        for line in text.splitlines():
            if "=" not in line or line.lstrip().startswith(("#", "!")):
                continue
            key, _, value = line.partition("=")
            try:
                value = value.strip().encode("latin-1").decode("unicode_escape")
            except (UnicodeError, ValueError):
                value = value.strip()
            bundle[key.strip()] = html.unescape(re.sub(r"<[^>]+>", "", value)).strip()
    return bundle


def _extract_views(artifact: Path, spec: Dict[str, Any], report: "MapReport") -> None:
    """Pantallas: título, encabezados, campos, botones→acción, mensajes, condiciones, inclusiones."""
    member_patterns = spec.get("member_patterns", [r"\.xhtml$"])
    exclude = spec.get("exclude_patterns", [r"template|plantilla|layout"])
    bundle = _load_bundle(artifact, spec.get("bundle_patterns", [])) if spec.get("bundle_patterns") else {}
    ref_re = re.compile(spec.get("bundle_ref_pattern", r"#\{(?:lbl|msg|bundle|i18n)\[?['\"]?(?P<key>[\w.]+)['\"]?\]?\}"))
    title_re = re.compile(spec.get("title_pattern", r"<title>(.*?)</title>|name=\"title\">(.*?)<"), re.S)
    heading_re = re.compile(spec.get("heading_pattern", r"<h[1-4][^>]*>(.*?)</h[1-4]>"), re.S)
    label_re = re.compile(spec.get("label_pattern", r"<(?:p|h):outputLabel[^>]*\bvalue=\"([^\"]*)\""), re.S)
    button_re = re.compile(spec.get("button_pattern", r"<(?:p|h):(?:commandButton|commandLink|menuitem|button)\b(.*?)>"), re.S)
    message_re = re.compile(spec.get("message_pattern", r"(?:requiredMessage|validatorMessage|converterMessage)=\"([^\"]+)\""))
    condition_re = re.compile(spec.get("condition_pattern", r"rendered=\"#\{([^}]*(?:rol|Rol|perfil|permiso|esUsuario|admin|ADMIN)[^}]*)\}\""))
    include_re = re.compile(spec.get("include_pattern", r"<ui:include[^>]*\bsrc=\"([^\"]+)\""))
    input_re = re.compile(spec.get("input_pattern", r"<(?:p|h):(inputText|inputTextarea|password|selectOneMenu|selectOneRadio|selectBooleanCheckbox|calendar|datePicker|inputMask|autoComplete|inputNumber|fileUpload)\b[^>]*\bid=\"([^\"]+)\""))
    form_re = re.compile(spec.get("form_pattern", r"<h:form\b([^>]*)>"))
    action_re = re.compile(r"\b(?:actionListener|action)=\"#\{(?:\w+\.)*(\w+)\s*(?:\(|\})")
    value_re = re.compile(r"\b(?:value|title)=\"([^\"]*)\"")

    def resolve(text: str) -> str:
        text = ref_re.sub(lambda m: bundle.get(m.group("key"), m.group("key")), text)
        text = html.unescape(re.sub(r"<[^>]+>", "", text))
        return " ".join(text.split())

    for name, data in _members(artifact):
        if not _match_any(member_patterns, name) or _match_any(exclude, name):
            continue
        text = data.decode("utf-8", errors="replace")
        title = ""
        for m in title_re.finditer(text):
            title = resolve(next(g for g in m.groups() if g) if any(m.groups()) else "")
            if title:
                break
        headings = list(dict.fromkeys(h for h in (resolve(x) for x in heading_re.findall(text)) if h and len(h) < 120))
        labels = list(dict.fromkeys(l for l in (resolve(x) for x in label_re.findall(text)) if l and len(l) < 80 and not l.startswith("#{")))
        buttons: List[Dict[str, str]] = []
        for attrs in button_re.findall(text):
            # Un patrón del perfil con varias alternativas devuelve tuplas; se toma la que casó.
            if isinstance(attrs, tuple):
                attrs = next((g for g in attrs if g), "")
            v = value_re.search(attrs)
            a = action_re.search(attrs)
            label = resolve(v.group(1)) if v else ""
            if not label and not a:
                continue
            item = {"label": label}
            if a:
                item["action"] = a.group(1)
            if item not in buttons:
                buttons.append(item)
        messages = list(dict.fromkeys(resolve(x) for x in message_re.findall(text)))
        conditions = list(dict.fromkeys(" ".join(x.split()) for x in condition_re.findall(text)))
        includes = list(dict.fromkeys(include_re.findall(text)))
        forms: List[str] = []
        for attrs in form_re.findall(text):
            fid = re.search(r"\bid=\"([^\"]+)\"", attrs)
            prepend = "prependId=\"false\"" not in attrs
            forms.append((fid.group(1) if fid else "(sin id)") + ("" if prepend else " (prependId=false)"))
        inputs = [f"{kind}#{iid}" for kind, iid in input_re.findall(text)]
        if not (title or headings or labels or buttons or messages):
            continue
        screen: Dict[str, Any] = {"path": name, "title": title, "headings": headings, "fields": labels,
                                  "inputs": inputs[:80], "forms": forms, "buttons": buttons, "messages": messages,
                                  "conditions": conditions, "includes": includes, "evidence": f"{artifact.name}!{name}"}
        report.screens.append(screen)
    report.labels = len(bundle)


# ------------------------------------------------------------- ensamblado

class MapReport:
    def __init__(self) -> None:
        self.entrypoints: List[Dict[str, Any]] = []
        self.jobs: List[Dict[str, Any]] = []
        self.external: List[Dict[str, Any]] = []
        self.data: List[Dict[str, Any]] = []
        self.roles: List[Dict[str, Any]] = []
        self.catalogs: List[Dict[str, Any]] = []
        self.distributions: List[Dict[str, Any]] = []
        self.classes: List[Dict[str, Any]] = []
        self.screens: List[Dict[str, Any]] = []
        self.labels = 0
        self.notes: List[str] = []
        self.gaps: List[str] = []

    def gap(self, message: str) -> None:
        self.gaps.append(message)


# Qué superficie del mapa alimenta cada mecanismo. Lo que ningún mecanismo del
# perfil cubre no puede salir como "cero": sale como hueco declarado (D23).
_MECHANISM_SURFACES: Dict[str, tuple] = {
    "archive_url_scan": ("external_dependencies",),
    "config_hosts": ("external_dependencies",),
    "pg_dump_custom": ("data_stores", "catalogs"),
    "sql_dump": ("data_stores", "catalogs"),
    "jvm_route_annotations": ("entrypoints", "jobs"),
    "jvm_class_inventory": ("classes",),
    "view_templates": ("screens",),
    "groovy_config_values": ("jobs",),
    "groovy_controller_actions": ("entrypoints",),
    "groovy_url_mappings": ("entrypoints",),
}
_SURFACES = ("entrypoints", "jobs", "external_dependencies", "data_stores", "catalogs", "classes", "screens")


_MECHANISMS: Dict[str, Callable] = {
    "archive_url_scan": lambda art, spec, rep, ctx: _extract_archive_urls(art, spec, rep),
    "config_hosts": lambda art, spec, rep, ctx: _extract_config_hosts(art, spec, rep),
    "pg_dump_custom": lambda art, spec, rep, ctx: _extract_pg_dump(spec, rep, ctx["dump"]),
    "jvm_route_annotations": lambda art, spec, rep, ctx: _extract_jvm_routes(art, spec, rep, ctx["tools"]),
    "jvm_class_inventory": lambda art, spec, rep, ctx: _extract_jvm_classes(art, spec, rep, ctx["tools"]),
    "view_templates": lambda art, spec, rep, ctx: _extract_views(art, spec, rep),
    "sql_dump": lambda art, spec, rep, ctx: _extract_sql_dump(spec, rep, ctx["dump"]),
    "groovy_config_values": lambda art, spec, rep, ctx: _groovy().extract_config_values(art, spec, rep, ctx["tools"]),
    "groovy_controller_actions": lambda art, spec, rep, ctx: _groovy().extract_controller_actions(art, spec, rep, ctx["tools"]),
    "groovy_url_mappings": lambda art, spec, rep, ctx: _groovy().extract_url_mappings(art, spec, rep, ctx["tools"]),
}


def _groovy():
    from pepper.inspect import groovy
    return groovy


def build_map(artifact: Path, extractors: List[Dict[str, Any]], profile_id: Optional[str],
              dump: Optional[Path] = None, tools: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from pepper import __version__

    if not artifact.exists():
        raise FileNotFoundError(f"artefacto inexistente: {artifact}")
    ctx = {"dump": dump, "tools": tools if tools is not None else jvm.default_tools()}
    report = MapReport()
    for extractor in extractors:
        kind = extractor.get("mechanism")
        handler = _MECHANISMS.get(kind)
        if handler is None:
            report.gap(f"mecanismo desconocido en el perfil: {kind!r}")
            continue
        try:
            handler(artifact, extractor, report, ctx)
        except Exception as error:  # noqa: BLE001 — un perfil es DATO: un patrón mal escrito no tira el mapa
            # Los perfiles los redacta un agente y los revisa una persona; una regex con el número de
            # grupos equivocado reventaba el mapa entero con un traceback. Se declara como hueco y se
            # siguen corriendo los demás extractores: un mapa parcial se declara parcial.
            report.gap(f"{kind}: el extractor del perfil falló ({type(error).__name__}: {str(error)[:160]}); "
                       "revisa sus patrones — lo que ese mecanismo enumera NO está en el mapa")

    covered = set()
    for extractor in extractors:
        covered.update(_MECHANISM_SURFACES.get(extractor.get("mechanism"), ()))
    for surface in _SURFACES:
        if surface not in covered:
            report.gap(f"{surface}: ningún extractor del perfil sabe enumerarlos; "
                       f"la lista vacía NO significa que el sistema no tenga")

    # Sin timestamp: mismo artefacto → mismos bytes (D8), así el mapa se diffea.
    return {
        "schema_version": "0.2.0",
        "profile_id": profile_id,
        "artifact": {"name": artifact.name},
        "generated_by": f"pepper {__version__}",
        "complete": not report.gaps,
        "coverage_gaps": report.gaps,
        "entrypoints": report.entrypoints,
        "jobs": report.jobs,
        "external_dependencies": report.external,
        "data_stores": report.data,
        "catalogs": report.catalogs,
        "distributions": report.distributions,
        "classes": report.classes,
        "screens": report.screens,
        "labels": report.labels,
        "notes": report.notes,
    }


_MERGED_LISTS = ("entrypoints", "jobs", "external_dependencies", "data_stores",
                 "catalogs", "distributions", "classes", "screens")
# Mecanismos que leen el respaldo, no el artefacto: en un sistema de varias piezas corren UNA vez,
# con la pieza que habla con la base. En las demás no son un hueco: no les toca.
DUMP_MECHANISMS = ("pg_dump_custom", "sql_dump")


def extractors_without_dump(extractors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Los extractores que no dependen del respaldo (para las piezas que no hablan con la base)."""
    return [e for e in extractors if e.get("mechanism") not in DUMP_MECHANISMS]


def merge_maps(parts: List[Tuple[str, Dict[str, Any]]]) -> Dict[str, Any]:
    """Une los mapas de varias piezas en UN mapa del sistema.

    Un sistema de varios desplegables tiene sus pantallas repartidas entre las piezas: leer solo
    la que habla con la base dejaba fuera el front y la puerta de enlace, y el documento describía
    un sistema más chico que el real (D33). Cada hallazgo conserva de qué pieza salió, anteponiéndolo
    a su evidencia (`front › dist/index.html`), porque el contrato del mapa no admite campos nuevos
    por elemento. Lo que dos piezas declaren idéntico —misma evidencia incluida— se dice una vez.

    `parts` son (nombre de la pieza, su mapa), en el orden en que se quieren leer.
    """
    if not parts:
        raise ValueError("no hay mapas que unir")
    if len(parts) == 1:
        return parts[0][1]
    merged: Dict[str, Any] = dict(parts[0][1])
    merged["artifact"] = {
        "name": ", ".join(m.get("artifact", {}).get("name", name) for name, m in parts),
        "parts": [{"component": name, "name": m.get("artifact", {}).get("name", name)} for name, m in parts],
    }
    for key in _MERGED_LISTS:
        vistos: Dict[str, Dict[str, Any]] = {}
        for name, mapa in parts:
            for item in mapa.get(key) or []:
                item = dict(item)
                if item.get("evidence"):
                    item["evidence"] = f"{name} › {item['evidence']}"
                vistos.setdefault(json.dumps(item, sort_keys=True, ensure_ascii=False), item)
        merged[key] = list(vistos.values())
    merged["labels"] = sum(int(m.get("labels") or 0) for _, m in parts)
    merged["complete"] = all(m.get("complete") for _, m in parts)
    merged["coverage_gaps"] = [f"{name} › {gap}" for name, mapa in parts for gap in (mapa.get("coverage_gaps") or [])]
    merged["notes"] = [f"{name} › {note}" for name, mapa in parts for note in (mapa.get("notes") or [])]
    merged["notes"].insert(0, "sistema de {} piezas: {}".format(
        len(parts), ", ".join(f"{name} ({mapa.get('artifact', {}).get('name', '?')})" for name, mapa in parts)))
    return merged


# ------------------------------------------------------------- render legible

def _cell(value: Any) -> str:
    if value is None:
        return "∅"
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_map(system_map: Dict[str, Any]) -> Dict[str, str]:
    """→ {archivo.md: contenido}: el mapa en prosa mínima, para que el agente lo lea sin parsear JSON."""
    out: Dict[str, str] = {}
    name = system_map.get("artifact", {}).get("name", "?")

    lines = [f"# Superficie — {name}", ""]
    if system_map.get("coverage_gaps"):
        lines += ["> Mapa INCOMPLETO:"] + [f"> - {g}" for g in system_map["coverage_gaps"]] + [""]
    routes = system_map.get("entrypoints", [])
    lines += [f"## Entradas HTTP ({len(routes)})", "", "| método | ruta | atiende |", "|---|---|---|"]
    lines += [f"| {r.get('method','')} | `{r['path']}` | {r.get('handler','')} |" for r in routes]
    lines += ["", f"## Procesos automáticos ({len(system_map.get('jobs', []))})", "", "| job | cuándo (cron) | detalle |", "|---|---|---|"]
    lines += [f"| {j['name']} | `{j.get('schedule','')}` | {j.get('detail','')} |" for j in system_map.get("jobs", [])]
    deps = system_map.get("external_dependencies", [])
    lines += ["", f"## Hosts externos referidos por el artefacto ({len(deps)})", "", "| host | tipo | dónde |", "|---|---|---|"]
    lines += [f"| {d['name']} | {d['kind']} | {d.get('evidence','')} |" for d in deps]
    if system_map.get("notes"):
        lines += ["", "## Notas de configuración y respaldo", ""] + [f"- {n}" for n in system_map["notes"]]
    out["surface.md"] = "\n".join(lines) + "\n"

    data = system_map.get("data_stores", [])
    tables = [d for d in data if d["kind"] == "table"]
    lines = [f"# Base de datos — {name}", "", f"## Tablas ({len(tables)}), de mayor a menor", "",
             "| tabla | filas | columnas |", "|---|---:|---|"]
    for t in sorted(tables, key=lambda t: -(t.get("count") or 0)):
        lines.append(f"| `{t['name']}` | {t.get('count', '?')} | {', '.join(t.get('columns', []))[:300]} |")
    triggers = [d for d in data if d["kind"] == "trigger"]
    lines += ["", f"## Triggers ({len(triggers)}) — reglas que viven en la base", ""]
    for t in triggers:
        lines += [f"### `{t['name']}` — {t.get('detail','')}", "", "```sql", t.get("definition", ""), "```", ""]
    functions = [d for d in data if d["kind"] == "function"]
    lines += [f"## Funciones y procedimientos ({len(functions)})", ""]
    for f in functions:
        lines += [f"### `{f['name']}`" + (f" ({f['detail']})" if f.get("detail") else ""), "", "```sql", f.get("definition", ""), "```", ""]
    views = [d for d in data if d["kind"] == "view"]
    lines += [f"## Vistas ({len(views)})", ""]
    for v in views:
        lines += [f"### `{v['name']}`", "", "```sql", v.get("definition", ""), "```", ""]
    others = [d for d in data if d["kind"] in ("foreign_server", "summary")]
    if others:
        lines += ["## Otros objetos", ""] + [f"- {d['kind']} `{d.get('name','')}`: {d.get('detail','')}" for d in others]
    out["db.md"] = "\n".join(lines) + "\n"

    lines = [f"# Catálogos — {name}", "",
             "Tablas chicas completas (valores redactados donde parecen datos de personas o credenciales).", ""]
    for c in system_map.get("catalogs", []):
        lines += [f"## `{c['table']}` ({c['count']} filas)", "", "| " + " | ".join(c["columns"]) + " |",
                  "|" + "---|" * len(c["columns"])]
        lines += ["| " + " | ".join(_cell(v) for v in row) + " |" for row in c["rows"]]
        lines.append("")
    dists = system_map.get("distributions", [])
    if dists:
        lines += ["# Distribuciones — columnas de estado en tablas grandes", ""]
        for d in dists:
            lines += [f"## `{d['table']}.{d['column']}` ({d['total']} filas, {d['distinct']} valores distintos)", "",
                      "| valor | filas |", "|---|---:|"]
            lines += [f"| {_cell(v['value'])} | {v['count']} |" for v in d["values"]]
            lines.append("")
    out["catalogs.md"] = "\n".join(lines) + "\n"

    screens = system_map.get("screens", [])
    lines = [f"# Pantallas — {name} ({len(screens)})", ""]
    for s in screens:
        lines += [f"## `{s['path']}`" + (f" — {s['title']}" if s.get("title") else ""), ""]
        if s.get("headings"):
            lines.append(f"- **Encabezados:** {' · '.join(s['headings'])}")
        if s.get("fields"):
            lines.append(f"- **Campos:** {', '.join(s['fields'])}")
        if s.get("inputs"):
            lines.append("- **Controles (tipo#id):** " + ", ".join(s["inputs"]) +
                         (f" · formularios: {', '.join(s['forms'])}" if s.get("forms") else ""))
        if s.get("buttons"):
            lines.append("- **Botones:** " + " · ".join(
                f"{b.get('label') or '(sin texto)'}" + (f" → `{b['action']}()`" if b.get("action") else "") for b in s["buttons"]))
        if s.get("messages"):
            lines.append(f"- **Mensajes de validación:** {' · '.join(s['messages'])}")
        if s.get("conditions"):
            lines.append("- **Condiciones por rol/perfil:** " + " · ".join(f"`{c}`" for c in s["conditions"]))
        if s.get("includes"):
            lines.append(f"- **Incluye:** {', '.join(s['includes'])}")
        lines.append("")
    out["screens.md"] = "\n".join(lines) + "\n"

    classes = system_map.get("classes", [])
    lines = [f"# Código — {name} ({len(classes)} clases)", ""]
    by_kind: Dict[str, List[Dict[str, Any]]] = {}
    for c in classes:
        by_kind.setdefault(c["kind"], []).append(c)
    for kind, items in sorted(by_kind.items()):
        lines += [f"## {kind} ({len(items)})", ""]
        for c in items:
            lines += [f"### `{c['name']}`", ""]
            if c.get("methods"):
                lines.append(f"- **Métodos:** {', '.join(c['methods'])}")
            if c.get("constants"):
                lines.append("- **Constantes:** " + ", ".join(f"{k}={v}" for k, v in c["constants"].items()))
            if c.get("strings"):
                lines.append("- **Cadenas:** " + " · ".join(f"“{s}”" for s in c["strings"]))
            if c.get("strings_omitted"):
                lines.append(f"- **Cadenas omitidas:** {c['strings_omitted']}")
            lines.append("")
    out["code.md"] = "\n".join(lines) + "\n"
    return out


def route_pattern(path: str) -> "re.Pattern[str]":
    """`/{controller}/{action}?/{id}?` → regex: un segmento por variable, `?` lo hace opcional, `**` lo que sea."""
    out = "^"
    for segment in path.split("/"):
        if not segment:
            continue
        optional = segment.endswith("?")
        core = segment[:-1] if optional else segment
        if core == "**":
            part = "/.*"
        else:
            part = re.sub(r"\\\{\w+\\\}", "[^/]+", re.escape(core))
            part = "/" + part.replace("\\*\\*", ".*").replace("\\*", "[^/]*")
        out += f"(?:{part})?" if optional else part
    return re.compile(out + "/?$")


def coverage(system_map: Dict[str, Any], observed_paths: List[Any],
             evidence_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Qué del mapa se ha confirmado en la evidencia: rutas, dependencias y jobs.

    - Rutas: por (método, ruta). Una visita GET no confirma un DELETE: comparar solo la
      ruta inflaba la cobertura y daba por observada una acción destructiva que nunca
      corrió (auditoría 2026-09-21, P1-03). `observed_paths` trae `{"method", "path"}`
      (o `"GET /x"`); una observación sin método, o una ruta del mapa sin método, solo
      puede dar **parcial**, nunca observada.
    - Dependencias: el stub registra CADA llamada externa interceptada; un host que
      aparece ahí está confirmado en ejecución.
    - Jobs: un job sin log propio no es detectable por nombre. Si el mapa no trae
      una firma para buscarlo, se declara **no medible automáticamente** — nunca
      "0 observados", que sería mentir por omisión.
    """
    observed: List[Tuple[str, str]] = []
    for item in observed_paths:
        if isinstance(item, dict):
            method, path = str(item.get("method") or "").upper(), str(item.get("path") or "")
        elif isinstance(item, (tuple, list)) and len(item) == 2:
            method, path = str(item[0] or "").upper(), str(item[1] or "")
        else:
            text = str(item)
            method, _, path = text.partition(" ") if re.match(r"^[A-Z]+ /", text) else ("", "", text)
        observed.append((method, path.split("?")[0].rstrip("/") or "/"))
    routes = [e for e in system_map.get("entrypoints", []) if e["kind"] in ("http_route", "rest_endpoint")]
    hit, partial, miss = [], [], []
    for e in routes:
        path = e["path"].rstrip("/") or "/"
        method = str(e.get("method") or "").upper()
        templated = "{" in path or "*" in path
        pattern = route_pattern(path) if templated else None

        def same_path(seen: str) -> bool:
            return bool(pattern.match(seen)) if pattern else seen == path

        # una ruta del mapa sin método (Grails: cualquier verbo) se confirma con cualquier método conocido
        exact = any(m and (m == method or not method) and same_path(sp) for m, sp in observed)
        by_path = any(same_path(sp) for m, sp in observed)
        label = f"{method} {e['path']}".strip()
        if exact:
            hit.append(label)
        elif by_path:
            partial.append(label)   # la ruta se vio, pero no con ese método (o sin método conocido)
        else:
            miss.append(label)

    evidence_text = ""
    stub_text = ""
    if evidence_dir is not None and evidence_dir.is_dir():
        for name in ("containers/app.log", "containers/db.err.log"):
            path = evidence_dir / name
            if path.is_file():
                evidence_text += path.read_text(encoding="utf-8", errors="replace")
        stub = evidence_dir / "containers" / "stub.log"
        if stub.is_file():
            stub_text = stub.read_text(encoding="utf-8", errors="replace")

    deps = system_map.get("external_dependencies", [])
    deps_hit = sorted({d["target"] for d in deps if d.get("target") and d["target"].split(":")[0] in stub_text})

    jobs = system_map.get("jobs", [])
    jobs_with_signature = [j for j in jobs if j.get("signature")]
    jobs_hit = [j["name"] for j in jobs_with_signature
                if re.search(j["signature"], evidence_text)] if evidence_text else []
    jobs_measurable = bool(jobs_with_signature)

    return {
        "routes_total": len(routes), "routes_observed": len(hit), "routes_partial": len(partial),
        "jobs_total": len(jobs),
        "jobs_measurable": jobs_measurable,
        "jobs_observed": len(jobs_hit) if jobs_measurable else None,
        "dependencies_total": len(deps), "dependencies_observed": len(deps_hit),
        "dependencies_confirmed": deps_hit,
        "observed": sorted(hit), "partially_observed": sorted(partial), "not_observed": sorted(miss),
    }
