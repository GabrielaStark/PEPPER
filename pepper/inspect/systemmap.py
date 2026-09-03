"""`pepper map`: extracción EXHAUSTIVA de la superficie de un sistema, determinística.

El fallo que este módulo corrige: Inspect entregaba prosa, y Discover medía solo
los flujos que un humano ejercitaba a mano — así que "todos los flujos y todas las
dependencias" quedaba a medias sin que nadie lo notara. `pepper map` enumera el
UNIVERSO completo (cada entrada HTTP, cada job, cada dependencia, cada almacén de
datos, cada perfil) desde el artefacto, y `coverage` mide qué se ha observado
contra ese universo.

Agnóstico por construcción (Principio 3 + perfiles como datos): el núcleo entiende
un puñado de MECANISMOS de extracción; los patrones concretos (regex de URLs,
prefijos de paquete, claves de config, comando del volcado) los declara el perfil
en `extractors.json`. Mecanismos:

  archive_url_scan       URLs externas dentro del artefacto (cualquier zip/tar)
  config_hosts           hosts/urls declarados en archivos de configuración
  db_dump_toc            inventario del respaldo vía `pg_restore -l` (tablas,
                         funciones, triggers, vistas y SERVIDORES FORÁNEOS)
  jvm_route_annotations  rutas @*Mapping y jobs @Scheduled vía `javap`

Fail-honest (como isolate): si falta una herramienta (javap, pg_restore) o un
extractor no puede correr, el mapa se marca `complete=false` y lo dice en
`coverage_gaps`. Un mapa parcial se declara parcial.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

MAP_NAME = "system-map.json"
_MAX_MEMBER_BYTES = 4 * 1024 * 1024
_URL_RE = re.compile(r"https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+")


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
    key_re = re.compile(spec.get("host_key_pattern", r"(?i)(url|host|smtp|uri|endpoint|bus|sideco)"))
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


def _run_tool(binary: Optional[str], args: List[str]) -> Optional[str]:
    if not binary:
        return None
    try:
        out = subprocess.run([binary, *args], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def _extract_db_toc(spec: Dict[str, Any], report: "MapReport", dump: Optional[Path],
                    tools: Dict[str, str]) -> None:
    """Inventario del respaldo con `pg_restore -l` (no requiere servidor)."""
    if dump is None or not dump.is_file():
        report.gap("db_dump_toc: no se encontró el respaldo (--dump); sin él no hay inventario de datos")
        return
    listing = _run_tool(tools.get("pg_restore"), ["-l", str(dump)])
    if listing is None:
        # Sin pg_restore local, corre pg_restore -l dentro de un contenedor (el
        # respaldo es de PostgreSQL; el perfil declara la imagen). No expone datos:
        # -l solo lee la tabla de contenidos.
        image = spec.get("docker_image", "postgres:17")
        docker = tools.get("docker", shutil.which("docker"))
        if docker:
            listing = _run_tool(docker, ["run", "--rm", "-v", f"{dump.resolve()}:/d/dump:ro",
                                         image, "pg_restore", "-l", "/d/dump"])
    if listing is None:
        report.gap("db_dump_toc: falta `pg_restore` y Docker en PATH (o el volcado no es formato custom); inventario de datos incompleto")
        return
    counts: Dict[str, int] = {}
    ref = f"{dump.name} (pg_restore -l)"
    # La cabecera del TOC declara de dónde salió el respaldo: base de origen y
    # versión del servidor. Son los datos que delatan una discrepancia con lo que
    # el humano cree (NOTAS.md) y con la imagen que Rehydrate va a levantar.
    for key, label in (("dbname", "base de origen"),
                       ("Dumped from database version", "versión del servidor de origen"),
                       ("Dumped by pg_dump version", "generado por pg_dump")):
        for line in listing.splitlines():
            if line.startswith(";") and key in line:
                value = line.split(":", 1)[1].strip() if ":" in line else ""
                if value:
                    report.notes.append(f"respaldo {dump.name} · {label}: {value}")
                break
    for line in listing.splitlines():
        line = line.strip()
        if not line or line.startswith(";"):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        obj = parts[3].upper()
        counts[obj] = counts.get(obj, 0) + 1
        # Formato TOC: "<id>; <oid> <oid> <TIPO> <esquema> <nombre> <dueño>"
        name = parts[5] if len(parts) > 5 else (parts[4] if len(parts) > 4 else "?")
        if obj == "SERVER":
            report.data.append({"kind": "foreign_server", "name": name,
                                "detail": "servidor foráneo (dblink/postgres_fdw): interconexión directa a otra base",
                                "evidence": ref})
        elif obj == "TRIGGER":
            report.data.append({"kind": "trigger", "name": name,
                                "detail": "regla dura en la base", "evidence": ref})
    for obj, label in (("TABLE", "table"), ("VIEW", "view"), ("FUNCTION", "function"), ("SEQUENCE", "sequence")):
        if obj in counts:
            report.data.append({"kind": "summary", "name": label, "count": counts[obj],
                                "detail": f"{counts[obj]} {label}(s) en el respaldo", "evidence": ref})


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
    class_root = spec.get("class_root", "WEB-INF/classes")
    package_prefixes = spec.get("package_prefixes", [])
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        with zipfile.ZipFile(artifact) as archive:
            classes = [n for n in archive.namelist()
                       if n.startswith(class_root) and n.endswith(".class")]
            wanted = [n for n in classes
                      if not package_prefixes or _match_any(package_prefixes, n[len(class_root):].lstrip("/"))]
            for n in wanted:
                archive.extract(n, tmpdir)
        cp = tmpdir / class_root
        for n in wanted:
            fqn = n[len(class_root):].lstrip("/").removesuffix(".class").replace("/", ".")
            out = _run_tool(javap, ["-p", "-v", "-classpath", str(cp), fqn])
            if out:
                _parse_javap(out, fqn.split(".")[-1], report, spec.get("job_signatures") or {})


_MAP_ANN = re.compile(r"annotation\.(RequestMapping|GetMapping|PostMapping|PutMapping|DeleteMapping)\(")


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
            signature = (job_signatures or {}).get(class_name)
            if signature:
                # Con qué se reconoce este job en la evidencia: muchos no dejan log
                # propio y solo se delatan por las consultas que lanzan.
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


# ------------------------------------------------------------- ensamblado

class MapReport:
    def __init__(self) -> None:
        self.entrypoints: List[Dict[str, Any]] = []
        self.jobs: List[Dict[str, Any]] = []
        self.external: List[Dict[str, Any]] = []
        self.data: List[Dict[str, Any]] = []
        self.roles: List[Dict[str, Any]] = []
        self.notes: List[str] = []
        self.gaps: List[str] = []

    def gap(self, message: str) -> None:
        self.gaps.append(message)


# Qué superficie del mapa alimenta cada mecanismo. Lo que ningún mecanismo del
# perfil cubre no puede salir como "cero": sale como hueco declarado (D23:
# un mapa parcial se declara parcial, nunca se disfraza de completo).
_MECHANISM_SURFACES: Dict[str, tuple] = {
    "archive_url_scan": ("external_dependencies",),
    "config_hosts": ("external_dependencies",),
    "db_dump_toc": ("data_stores",),
    "jvm_route_annotations": ("entrypoints", "jobs"),
}
_SURFACES = ("entrypoints", "jobs", "external_dependencies", "data_stores", "roles")


_MECHANISMS: Dict[str, Callable] = {
    "archive_url_scan": lambda art, spec, rep, ctx: _extract_archive_urls(art, spec, rep),
    "config_hosts": lambda art, spec, rep, ctx: _extract_config_hosts(art, spec, rep),
    "db_dump_toc": lambda art, spec, rep, ctx: _extract_db_toc(spec, rep, ctx["dump"], ctx["tools"]),
    "jvm_route_annotations": lambda art, spec, rep, ctx: _extract_jvm_routes(art, spec, rep, ctx["tools"]),
}


def _default_tools() -> Dict[str, str]:
    tools = {}
    for name in ("javap", "pg_restore"):
        found = shutil.which(name)
        if found:
            tools[name] = found
    return tools


def build_map(artifact: Path, extractors: List[Dict[str, Any]], profile_id: Optional[str],
              dump: Optional[Path] = None, tools: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    from datetime import datetime

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

    return {
        "schema_version": "0.1.0",
        "profile_id": profile_id,
        "artifact": {"name": artifact.name},
        "generated_by": f"pepper {__version__}",
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "complete": not report.gaps,
        "coverage_gaps": report.gaps,
        "entrypoints": report.entrypoints,
        "jobs": report.jobs,
        "external_dependencies": report.external,
        "data_stores": report.data,
        "roles": report.roles,
        "notes": report.notes,
    }


def coverage(system_map: Dict[str, Any], observed_paths: List[str],
             evidence_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Qué del mapa se ha confirmado en la evidencia: rutas, dependencias y jobs.

    - Rutas: comparación directa contra los `path` del http.jsonl.
    - Dependencias: el stub registra CADA llamada externa interceptada; un host que
      aparece ahí está confirmado en ejecución.
    - Jobs: un job sin log propio no es detectable por nombre (le pasó a
      RevisionCitasSchedule, que solo deja sus consultas). Si el mapa no trae una
      firma para buscarlo, se declara **no medible automáticamente** — nunca "0
      observados", que sería mentir por omisión.
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
