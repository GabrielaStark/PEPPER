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
  jvm_route_annotations  rutas @*Mapping y jobs @Scheduled vía `javap`
  jvm_class_inventory    por clase: métodos públicos, constantes y cadenas de
                         negocio (mensajes, estados) vía `javap -c -constants`
  view_templates         pantallas: título, encabezados, campos, botones y sus
                         acciones, mensajes de validación, condiciones por rol;
                         con el bundle de etiquetas resuelto

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
import re
import shutil
import subprocess
import tempfile
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

MAP_NAME = "system-map.json"
MAP_DIR = "map"
_MAX_MEMBER_BYTES = 4 * 1024 * 1024
_URL_RE = re.compile(r"https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+")
_REDACTED = "[REDACTADO]"

# Columnas cuyo VALOR no debe viajar: credenciales y datos de personas.
_SENSITIVE_COLUMN_RE = re.compile(
    r"(?i)pass|pwd|contrase|secret|token|credencial|correo|mail|curp|rfc|telefono|celular|nacimiento|cedula"
)
# Valores que son datos de una persona o una credencial, en cualquier columna o cadena.
_PII_VALUE_RE = re.compile(
    r"[\w.+-]+@[\w-]+\.[\w.-]+|\b[A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d\b|\b[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}\b|"
    r"\b\d{2,3}[ -]?\d{3}[ -]?\d{2}[ -]?\d{2}\b|\b\d{10}\b"
)
# Renglones de tablas parámetro/clave-valor donde la CLAVE delata un secreto.
_SECRET_KEY_RE = re.compile(r"(?i)pass|pwd|contrase|secret|token|credencial|smtp\.user|mail\.user|api.?key")
# Cadenas del bytecode que no aportan negocio o pueden ser secretos.
_NOISE_STRING_RE = re.compile(
    r"^(?:[A-Za-z]+:[/\\]|/|\\|<|\{|\[|%|\d+$|[a-z]{1,3}$|yyyy|dd[/-]|HH:|UTF|ISO|null$|"
    r"[A-Z][a-z]+(?:[A-Z][a-z]+)+$|java|org\.|mx\.|com\.|net\.|javax|select |SELECT |from |FROM |"
    r"insert |INSERT |update |UPDATE |delete |DELETE |where |WHERE )"
)
_SECRET_STRING_RE = re.compile(r"(?i)(password|contrase|secret|token|pwd)\s*[:=]")


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


def _match_any(patterns: List[str], value: str) -> bool:
    return any(re.search(p, value) for p in patterns)


def _run_tool(binary: Optional[str], args: List[str], timeout: int = 300) -> Optional[str]:
    if not binary:
        return None
    try:
        out = subprocess.run([binary, *args], capture_output=True, text=True, timeout=timeout,
                             errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


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
                               [r"(?i)usuario|user|persona|trabajador|empleado|cliente|testigo|beneficiario|patron|empresa"])
    state_re = re.compile(spec.get("state_column_pattern", r"(?i)estatus|status|estado$|^tipo|_tipo|tipo_|nivel|sector|rol$"))
    top_n = int(spec.get("distribution_top", 15))
    date_re = re.compile(spec.get("date_column_pattern", r"(?i)^fecha|^fc[a-z]|_fecha|fecha$"))

    tables = {e.tag: e for e in info.by_desc("TABLE")}
    readable = info.compression in ("none", "gzip")
    counts: Dict[str, int] = {}
    for entry in info.by_desc("TABLE DATA"):
        table = tables.get(entry.tag)
        columns = pgdump.table_columns(table.defn) if table else []
        rows_count = pgdump.count_rows(info, entry) if readable else -1
        counts[entry.tag] = rows_count
        item: Dict[str, Any] = {"kind": "table", "name": entry.tag, "columns": columns, "evidence": ref}
        if rows_count >= 0:
            item["count"] = rows_count
        report.data.append(item)
        if not readable:
            continue
        is_catalog = (rows_count <= catalog_max and not _match_any(catalog_exclude, entry.tag)
                      and (not catalog_include or _match_any(catalog_include, entry.tag)))
        if is_catalog:
            rows = [_redact_row(columns, row) for row in pgdump.iter_rows(info, entry, limit=catalog_max)]
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
                    if len(counters[i]) > 60:
                        continue  # no es un estado: demasiados valores distintos
                    top = counters[i].most_common(top_n)
                    if mode == "year":
                        top = sorted(top, key=lambda kv: kv[0])
                    report.distributions.append({
                        "table": entry.tag, "column": column + (" (año)" if mode == "year" else ""),
                        "total": rows_count, "distinct": len(counters[i]),
                        "values": [{"value": (v[:80] if isinstance(v, str) else v), "count": n} for v, n in top],
                        "evidence": ref,
                    })
    for name in sorted(t for t in tables if t not in counts):
        report.data.append({"kind": "table", "name": name, "columns": pgdump.table_columns(tables[name].defn),
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


# ---- el bytecode ---------------------------------------------------------

def _own_roots(names: List[str], class_root: str, depth: int = 3) -> List[str]:
    """Paquetes raíz del sistema (p. ej. `mx/com/edomex/`): los jars que los comparten son propios."""
    root = class_root.rstrip("/") + "/"
    roots = set()
    for n in names:
        if n.startswith(root) and n.endswith(".class"):
            parts = n[len(root):].split("/")
            if len(parts) > depth:
                roots.add("/".join(parts[:depth]) + "/")
    return sorted(roots)


def _class_names(artifact: Path, class_root: str, package_prefixes: List[str],
                 include_libs: bool, tmpdir: Path) -> List[Tuple[str, Path]]:
    """Extrae las clases del artefacto (y de sus jars propios) a `tmpdir`.

    Los prefijos de paquete se anclan a un segmento de ruta (`beans/` no casa con
    `xmlbeans/`). "Jar propio" = comparte paquete raíz con las clases del WAR,
    así las librerías de terceros quedan fuera sin listas negras.
    → [(nombre.calificado, classpath_root)] en orden determinístico."""
    anchored = [r"(?:^|/)" + p.lstrip("^/") for p in package_prefixes]
    wanted: List[Tuple[str, Path]] = []
    with zipfile.ZipFile(artifact) as archive:
        names = archive.namelist()
        root = class_root.rstrip("/") + "/"
        for n in sorted(names):
            if n.startswith(root) and n.endswith(".class"):
                relative = n[len(root):]
                if not anchored or _match_any(anchored, relative):
                    archive.extract(n, tmpdir)
                    wanted.append((relative.removesuffix(".class").replace("/", "."), tmpdir / class_root))
        if include_libs:
            own = _own_roots(names, class_root)
            for n in sorted(names):
                if not n.endswith(".jar"):
                    continue
                jar_dir = tmpdir / "lib" / Path(n).stem
                with zipfile.ZipFile(archive.open(n)) as jar:
                    members = [m for m in sorted(jar.namelist())
                               if m.endswith(".class") and any(m.startswith(o) for o in own)
                               and (not anchored or _match_any(anchored, m))]
                    if not members:
                        continue
                    jar_dir.mkdir(parents=True, exist_ok=True)
                    for m in members:
                        jar.extract(m, jar_dir)
                        wanted.append((m.removesuffix(".class").replace("/", "."), jar_dir))
    return wanted


def _javap_batches(javap: str, classes: List[Tuple[str, Path]], flags: List[str], batch: int = 40) -> Dict[str, str]:
    """Corre javap por lotes (una invocación por 40 clases) y separa la salida por clase."""
    outputs: Dict[str, str] = {}
    by_root: Dict[Path, List[str]] = {}
    for fqn, root in classes:
        by_root.setdefault(root, []).append(fqn)
    for root, fqns in by_root.items():
        for start in range(0, len(fqns), batch):
            chunk = fqns[start:start + batch]
            out = _run_tool(javap, [*flags, "-classpath", str(root), *chunk])
            if out is None:
                for fqn in chunk:  # un lote roto no oculta a los demás
                    single = _run_tool(javap, [*flags, "-classpath", str(root), fqn])
                    if single is not None:
                        outputs[fqn] = single
                continue
            current: Optional[str] = None
            buffer: List[str] = []
            for line in out.splitlines():
                m = re.match(r"^(?:public |final |abstract |private |protected )*(?:class|interface|enum) ([\w.$]+)", line)
                if m and not line.startswith(" "):
                    if current is not None:
                        outputs[current] = "\n".join(buffer)
                    current = m.group(1)
                    buffer = [line]
                else:
                    buffer.append(line)
            if current is not None:
                outputs[current] = "\n".join(buffer)
    return outputs


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
        classes = _class_names(artifact, spec.get("class_root", "WEB-INF/classes"),
                               spec.get("package_prefixes", []), False, Path(tmp))
        outputs = _javap_batches(javap, classes, ["-p", "-v"])
        for fqn, _ in classes:
            out = outputs.get(fqn)
            if out:
                _parse_javap(out, fqn.split(".")[-1], report, spec.get("job_signatures") or {})


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
        classes = _class_names(artifact, spec.get("class_root", "WEB-INF/classes"),
                               spec.get("package_prefixes", []), bool(spec.get("include_own_libs", True)), Path(tmp))
        outputs = _javap_batches(javap, classes, ["-p", "-c", "-constants"])
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
                    if not _SECRET_STRING_RE.search(f"{m.group(1)}={value}") and not _SECRET_KEY_RE.search(m.group(1)):
                        constants[m.group(1)] = value.strip('"')[:120]
                    continue
                m = re.search(r"\bldc2?_?w?\s+#\d+\s+// String (.*)$", s)
                if m:
                    text = m.group(1).strip()
                    if (len(text) >= 4 and re.search(r"[A-Za-zÁÉÍÓÚáéíóúñÑ]", text)
                            and not _NOISE_STRING_RE.search(text) and not _SECRET_STRING_RE.search(text)
                            and not _PII_VALUE_RE.search(text) and text not in strings):
                        strings.append(text[:200])
            if not methods and not constants and not strings:
                continue
            report.classes.append({
                "name": fqn, "kind": kind, "methods": methods, "constants": constants,
                "strings": strings[:max_strings], "evidence": f"{simple}.class (javap -c -constants)",
            })


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
        if not (title or headings or labels or buttons or messages):
            continue
        screen: Dict[str, Any] = {"path": name, "title": title, "headings": headings, "fields": labels,
                                  "buttons": buttons, "messages": messages, "conditions": conditions,
                                  "includes": includes, "evidence": f"{artifact.name}!{name}"}
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
    "jvm_route_annotations": ("entrypoints", "jobs"),
    "jvm_class_inventory": ("classes",),
    "view_templates": ("screens",),
}
_SURFACES = ("entrypoints", "jobs", "external_dependencies", "data_stores", "catalogs", "classes", "screens")


_MECHANISMS: Dict[str, Callable] = {
    "archive_url_scan": lambda art, spec, rep, ctx: _extract_archive_urls(art, spec, rep),
    "config_hosts": lambda art, spec, rep, ctx: _extract_config_hosts(art, spec, rep),
    "pg_dump_custom": lambda art, spec, rep, ctx: _extract_pg_dump(spec, rep, ctx["dump"]),
    "jvm_route_annotations": lambda art, spec, rep, ctx: _extract_jvm_routes(art, spec, rep, ctx["tools"]),
    "jvm_class_inventory": lambda art, spec, rep, ctx: _extract_jvm_classes(art, spec, rep, ctx["tools"]),
    "view_templates": lambda art, spec, rep, ctx: _extract_views(art, spec, rep),
}


def _default_tools() -> Dict[str, str]:
    tools = {}
    for name in ("javap",):
        found = shutil.which(name)
        if found:
            tools[name] = found
    return tools


def build_map(artifact: Path, extractors: List[Dict[str, Any]], profile_id: Optional[str],
              dump: Optional[Path] = None, tools: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    from pepper import __version__

    if not artifact.exists():
        raise FileNotFoundError(f"artefacto inexistente: {artifact}")
    ctx = {"dump": dump, "tools": tools if tools is not None else _default_tools()}
    report = MapReport()
    for extractor in extractors:
        kind = extractor.get("mechanism")
        handler = _MECHANISMS.get(kind)
        if handler is None:
            report.gap(f"mecanismo desconocido en el perfil: {kind!r}")
            continue
        handler(artifact, extractor, report, ctx)

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
    lines += [f"## Funciones ({len(functions)})", ""]
    for f in functions:
        lines += [f"### `{f['name']}`", "", "```sql", f.get("definition", ""), "```", ""]
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
            lines.append("")
    out["code.md"] = "\n".join(lines) + "\n"
    return out


def coverage(system_map: Dict[str, Any], observed_paths: List[str],
             evidence_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Qué del mapa se ha confirmado en la evidencia: rutas, dependencias y jobs.

    - Rutas: comparación directa contra los `path` del http.jsonl.
    - Dependencias: el stub registra CADA llamada externa interceptada; un host que
      aparece ahí está confirmado en ejecución.
    - Jobs: un job sin log propio no es detectable por nombre. Si el mapa no trae
      una firma para buscarlo, se declara **no medible automáticamente** — nunca
      "0 observados", que sería mentir por omisión.
    """
    observed = {p.split("?")[0].rstrip("/") or "/" for p in observed_paths}
    routes = [e for e in system_map.get("entrypoints", []) if e["kind"] in ("http_route", "rest_endpoint")]
    hit, miss = [], []
    for e in routes:
        path = e["path"].rstrip("/") or "/"
        (hit if path in observed else miss).append(f"{e.get('method','')} {e['path']}".strip())

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
        "routes_total": len(routes), "routes_observed": len(hit),
        "jobs_total": len(jobs),
        "jobs_measurable": jobs_measurable,
        "jobs_observed": len(jobs_hit) if jobs_measurable else None,
        "dependencies_total": len(deps), "dependencies_observed": len(deps_hit),
        "dependencies_confirmed": deps_hit,
        "observed": sorted(hit), "not_observed": sorted(miss),
    }
