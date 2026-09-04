"""`pepper rehydrate`: del artefacto y el respaldo a un entorno aislado corriendo, sin manos.

El artefacto dicta el ambiente (D17): dentro del desplegable viene la configuración
que dice a qué base se conecta (host, puerto, nombre, usuario, contraseña) y a qué
servicios externos habla. Aquí se fabrica exactamente esa red — la base vive en la
IP que el artefacto espera, con el nombre que espera; cada host externo se resuelve
por alias a un stub que responde error y registra; nada tiene salida (D19, D22).

Lo que hace, igual cada vez:

  1. Encuentra el desplegable y el respaldo en `legacy/`; lee NOTAS.md (versiones).
  2. Lee la configuración embebida y elige el perfil de configuración completo.
  3. Decide el servidor (descriptor del artefacto + NOTAS.md) y las versiones
     (la de la base la dicta el respaldo, no la nota: fidelidad).
  4. Rinde el compose y el script de restauración desde las plantillas del perfil,
     copia el proxy y el stub, escribe `.env` con la credencial que el artefacto trae.
  5. Verifica el aislamiento del compose (fail-closed) y, con `--up`: levanta la
     base y el stub, restaura el respaldo si la base está vacía, levanta app e
     ingress, espera a que arranque, verifica en vivo, valida y escribe
     `docs/pepper/environment.json` + `validation.md`.

BLOCKED es un entregable: si falta el desplegable, el respaldo o la configuración
no dice a qué conectarse, se escribe qué falta y se para.
"""

from __future__ import annotations

import ipaddress
import json
import re
import shutil
import subprocess
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pepper import REPO_ROOT
from pepper.profiles import Profile

DEFAULT_PORT = 18080
_ARTIFACT_SUFFIXES = (".war", ".ear", ".jar")
_DUMP_SUFFIXES = (".dump", ".backup", ".sql", ".bak")
_URL_RE = re.compile(r"https?://([A-Za-z0-9.-]+)(?::(\d+))?")
_JDBC_RE = re.compile(r"jdbc:(\w+)://([A-Za-z0-9.-]+)(?::(\d+))?/([A-Za-z0-9_]+)")
_SECRET_KEY_RE = re.compile(r"(?i)pass|pwd|secret|psw|token")


class Blocked(RuntimeError):
    """Falta algo sin lo cual no se puede reconstruir; el mensaje dice qué."""


# --------------------------------------------------------------- configuración

def parse_config(text: str) -> Dict[str, str]:
    """YAML simple de Spring (o .properties) → {clave.punteada: valor}. Sin pyyaml.

    Cubre lo que traen estos archivos: anidamiento por sangría, `clave: valor`,
    comentarios `#`, listas ignoradas. Las claves comentadas no cuentan."""
    flat: Dict[str, str] = {}
    if "=" in text and ":" not in text.split("\n", 1)[0]:  # .properties
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith(("#", "!")) or "=" not in line:
                continue
            key, _, value = line.partition("=")
            flat[key.strip()] = value.strip()
        return flat
    stack: List[Tuple[int, str]] = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        if line.startswith("- "):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.split(" #", 1)[0].strip().strip("'\"") if value.strip() else ""
        while stack and stack[-1][0] >= indent:
            stack.pop()
        path = ".".join([k for _, k in stack] + [key])
        if value:
            flat[path] = value
        stack.append((indent, key))
    return flat


def read_artifact_configs(artifact: Path, patterns: List[str]) -> Dict[str, Dict[str, str]]:
    """{nombre-de-perfil: config} de cada archivo de configuración dentro del artefacto."""
    configs: Dict[str, Dict[str, str]] = {}
    with zipfile.ZipFile(artifact) as archive:
        for name in sorted(archive.namelist()):
            if not any(re.search(p, name) for p in patterns):
                continue
            stem = Path(name).stem  # application-prod
            profile = stem.split("-", 1)[1] if "-" in stem else "default"
            configs[profile] = parse_config(archive.read(name).decode("utf-8", errors="replace"))
    return configs


def choose_spring_profile(configs: Dict[str, Dict[str, str]]) -> Tuple[str, Dict[str, str]]:
    """El perfil completo: trae url, usuario y contraseña del datasource. Prefiere prod."""
    candidates = []
    for name, cfg in configs.items():
        url = next((v for k, v in cfg.items() if k.endswith("datasource.url")), "")
        user = next((v for k, v in cfg.items() if k.endswith("datasource.username")), "")
        pwd = next((v for k, v in cfg.items() if k.endswith("datasource.password")), None)
        if url and user and pwd is not None:
            candidates.append((0 if name == "prod" else 1 if "prod" in name else 2, name, cfg))
    if not candidates:
        raise Blocked("ningún perfil de configuración dentro del artefacto trae url, usuario y contraseña del datasource: "
                      "no dice a qué conectarse. Consigue la configuración externa del ambiente.")
    candidates.sort(key=lambda c: c[0])
    _, name, cfg = candidates[0]
    merged = dict(configs.get("default", {}))
    merged.update(cfg)
    return name, merged


# --------------------------------------------------------------- el plan

@dataclass
class Plan:
    stack_name: str
    artifact: Path
    dump: Path
    spring_profile: str
    db_engine: str
    db_ip: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str
    subnet: str
    app_ip: str
    stub_ip: str
    dns_sink: str
    server: str
    server_image: str
    postgres_version: str
    pg_restore_version: str
    external_hosts: List[str]
    external_by_ip: List[str]
    stub_ports: str
    app_package_env: str
    files_root: str
    create_roles: List[str]
    host_port: int
    deviations: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def variables(self, out_dir: Path) -> Dict[str, str]:
        rel = lambda p: Path(*([".."] * len(out_dir.resolve().relative_to(REPO_ROOT).parts))) / p.resolve().relative_to(REPO_ROOT) \
            if _under(p, REPO_ROOT) and _under(out_dir, REPO_ROOT) else p.resolve()
        return {
            "stack_name": self.stack_name, "subnet": self.subnet, "postgres_version": self.postgres_version,
            "pg_restore_version": self.pg_restore_version, "dns_sink": f'"{self.dns_sink}"',
            "db_name": self.db_name, "db_user": self.db_user, "db_ip": self.db_ip,
            "dump_path": str(rel(self.dump)), "stub_ports": self.stub_ports, "stub_ip": self.stub_ip,
            "external_hosts": "[" + ", ".join(self.external_hosts) + "]",
            "server_image": self.server_image, "spring_profile": self.spring_profile,
            "app_package_env": self.app_package_env, "app_ip": self.app_ip,
            "war_path": str(rel(self.artifact)), "war_name": self.artifact.name,
            "files_root": self.files_root, "host_port": str(self.host_port),
            "create_roles": "\n".join(
                f'psql -d postgres -c "CREATE ROLE \\"{role}\\";" 2>&1 | grep -v "already exists" || true'
                for role in self.create_roles) or "true",
        }


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def find_inputs(legacy_dir: Path) -> Tuple[Path, Path]:
    artifacts = sorted((p for p in legacy_dir.iterdir() if p.suffix.lower() in _ARTIFACT_SUFFIXES),
                       key=lambda p: -p.stat().st_size)
    dumps = sorted((p for p in legacy_dir.iterdir() if p.suffix.lower() in _DUMP_SUFFIXES),
                   key=lambda p: -p.stat().st_size)
    if not artifacts:
        raise Blocked(f"no hay desplegable (.war/.ear/.jar) en {legacy_dir}")
    if not dumps:
        raise Blocked(f"no hay respaldo de la base (.dump/.backup/.sql) en {legacy_dir}")
    return artifacts[0], dumps[0]


def _notes_version(notes_text: str, engine: str) -> Optional[str]:
    m = re.search(rf"(?i){engine}\D{{0,20}}(\d+(?:\.\d+)*)", notes_text)
    return m.group(1) if m else None


def _choose_server(artifact: Path, profile: Profile, notes_text: str) -> Tuple[str, str, List[str]]:
    recipe = profile.data.get("rehydrate", {})
    deviations: List[str] = []
    descriptors: Dict[str, List[str]] = recipe.get("descriptors") or {}
    images: Dict[str, Dict[str, str]] = recipe.get("server_images") or {}
    with zipfile.ZipFile(artifact) as archive:
        members = set(archive.namelist())
    present = [server for server, files in descriptors.items() if any(f in members for f in files)]
    noted = next((s for s in images if re.search(rf"(?i)\b{s}\b", notes_text)), None)
    server = noted or (present[0] if present else "")
    if not server:
        raise Blocked("el artefacto no trae descriptor de servidor (jboss-web.xml, context.xml…) y NOTAS.md no dice en qué corre")
    if noted and present and noted not in present:
        deviations.append(f"NOTAS.md dice {noted} pero el artefacto trae descriptor de {present[0]}; manda la nota")
    version = _notes_version(notes_text, server) or ""
    major = version.split(".")[0] if version else ""
    table = images.get(server, {})
    image = table.get(major) or table.get("*", "").replace("{major}", major)
    if not image:
        if table:
            fallback_major = sorted(table, key=lambda k: -int(k) if k.isdigit() else 0)[0]
            image = table[fallback_major]
            deviations.append(f"{server} {version or '(versión no declarada)'}: no hay imagen para esa versión en el perfil; se usa {image}")
        else:
            raise Blocked(f"el perfil no declara imagen para el servidor {server}")
    return server, image, deviations


def make_plan(legacy_dir: Path, profile: Profile, host_port: int = DEFAULT_PORT,
              notes_path: Optional[Path] = None) -> Plan:
    from pepper.inspect import pgdump

    artifact, dump = find_inputs(legacy_dir)
    notes_text = notes_path.read_text(encoding="utf-8", errors="replace") if notes_path and notes_path.is_file() else ""
    recipe = profile.data.get("rehydrate", {})
    configs = read_artifact_configs(artifact, recipe.get("config_patterns") or [r"application.*\.(yml|yaml|properties)$"])
    if not configs:
        raise Blocked("el artefacto no trae configuración embebida (application*.yml) y no se dio configuración externa")
    spring_profile, cfg = choose_spring_profile(configs)
    url = next(v for k, v in cfg.items() if k.endswith("datasource.url"))
    m = _JDBC_RE.search(url)
    if not m:
        raise Blocked(f"no entiendo la URL del datasource: {url!r}")
    engine, host, port, db_name = m.group(1), m.group(2), int(m.group(3) or 5432), m.group(4)
    if engine != "postgresql":
        raise Blocked(f"motor {engine}: este perfil solo reconstruye PostgreSQL")
    db_user = next(v for k, v in cfg.items() if k.endswith("datasource.username"))
    db_password = next(v for k, v in cfg.items() if k.endswith("datasource.password"))
    deviations: List[str] = []
    notes: List[str] = []
    try:
        db_ip = str(ipaddress.IPv4Address(host))
        if ipaddress.IPv4Address(host).is_loopback:
            raise ValueError
    except ValueError:
        db_ip = "10.100.0.2"
        deviations.append(f"el datasource apunta a {host!r} (no es una IP enrutable): la base queda en {db_ip} con alias {host}")
    net = ipaddress.IPv4Network(f"{db_ip}/24", strict=False)
    hosts = list(net.hosts())
    taken = {db_ip}
    pick = lambda pref: next(str(h) for h in hosts if str(h) not in taken and (str(h).endswith(pref) or True))
    stub_ip = str(hosts[1]) if str(hosts[1]) != db_ip else str(hosts[2])
    taken.add(stub_ip)
    app_ip = next(str(h) for h in hosts[8:] if str(h) not in taken)
    taken.add(app_ip)
    dns_sink = str(hosts[-2])

    info = pgdump.read_toc(dump)
    postgres_version = info.server_version.split(".")[0]
    pg_restore_version = info.pg_dump_version.split(".")[0] or postgres_version
    noted_pg = _notes_version(notes_text, "postgres")
    if noted_pg and noted_pg.split(".")[0] != postgres_version:
        deviations.append(f"NOTAS.md dice PostgreSQL {noted_pg}; el respaldo declara origen {info.server_version}: por fidelidad se levanta {postgres_version}")
    if info.dbname != db_name:
        deviations.append(f"el respaldo viene de la base '{info.dbname}' y se restaura dentro de '{db_name}', que es la que el artefacto espera")

    server, server_image, server_deviations = _choose_server(artifact, profile, notes_text)
    deviations += server_deviations

    external: List[str] = []
    by_ip: List[str] = []
    ports = set((recipe.get("stub_ports") or "80,443").split(","))
    for key, value in cfg.items():
        if _SECRET_KEY_RE.search(key) or key.endswith("datasource.url"):
            continue
        for mh in _URL_RE.finditer(value):
            h, p = mh.group(1), mh.group(2)
            if p:
                ports.add(p)
            try:
                ipaddress.IPv4Address(h)
                if h not in by_ip and ipaddress.IPv4Address(h) not in net:
                    by_ip.append(h + (f":{p}" if p else ""))
                continue
            except ValueError:
                pass
            if h not in external and h != "localhost":
                external.append(h)
        if re.search(r"(?i)smtp|mail\.host|mail\.smtp", key) and re.fullmatch(r"[A-Za-z0-9.-]+\.[a-z]{2,}", value):
            if value not in external:
                external.append(value)
    for key, value in cfg.items():
        if re.search(r"(?i)mail.*port|smtp.*port", key) and value.isdigit():
            ports.add(value)
    stub_ports = ",".join(sorted(ports, key=int))
    if by_ip:
        notes.append("dependencias declaradas por IP directa (no se pueden aliasear al stub; fallan sin salir y sin registro): " + ", ".join(by_ip))

    start_class = ""
    with zipfile.ZipFile(artifact) as archive:
        if "META-INF/MANIFEST.MF" in archive.namelist():
            manifest = archive.read("META-INF/MANIFEST.MF").decode("utf-8", errors="replace")
            mm = re.search(r"Start-Class:\s*(\S+)", manifest)
            start_class = mm.group(1) if mm else ""
    package = ".".join(start_class.split(".")[:3]) if start_class else "app"
    app_package_env = package.upper().replace(".", "_").replace("-", "_")
    files_root = next((v for k, v in cfg.items() if re.search(r"(?i)ruta|path|dir|folder", k) and v.startswith("/")), "/data")

    stack_name = re.sub(r"[^a-z0-9]+", "-", artifact.stem.split("-")[0].lower()).strip("-") or "legacy"
    return Plan(stack_name=stack_name, artifact=artifact, dump=dump, spring_profile=spring_profile,
                db_engine=engine, db_ip=db_ip, db_port=port, db_name=db_name, db_user=db_user, db_password=db_password,
                subnet=str(net), app_ip=app_ip, stub_ip=stub_ip, dns_sink=dns_sink,
                server=server, server_image=server_image, postgres_version=postgres_version,
                pg_restore_version=pg_restore_version, external_hosts=external, external_by_ip=by_ip,
                stub_ports=stub_ports, app_package_env=app_package_env, files_root=files_root,
                create_roles=[o for o in info.owners() if o != db_user], host_port=host_port,
                deviations=deviations, notes=notes)


# --------------------------------------------------------------- render

def render(plan: Plan, profile: Profile, out_dir: Path) -> List[Path]:
    recipe = profile.data.get("rehydrate", {})
    out_dir.mkdir(parents=True, exist_ok=True)
    variables = plan.variables(out_dir)
    written: List[Path] = []
    for key, target in (("compose_template", "docker-compose.yml"), ("restore_template", "restore.sh")):
        template_name = recipe.get(key)
        if not template_name:
            raise Blocked(f"el perfil {profile.id} no declara {key}")
        text = (profile.dir / template_name).read_text(encoding="utf-8")
        missing = sorted(set(re.findall(r"\{\{(\w+)\}\}", text)) - set(variables))
        if missing:
            raise Blocked(f"la plantilla {template_name} pide variables que el plan no tiene: {', '.join(missing)}")
        for name, value in variables.items():
            text = text.replace("{{" + name + "}}", value)
        path = out_dir / target
        path.write_text(text, encoding="utf-8")
        written.append(path)
    (out_dir / "proxy").mkdir(exist_ok=True)
    (out_dir / "stub").mkdir(exist_ok=True)
    shutil.copy2(REPO_ROOT / "pepper" / "proxy.py", out_dir / "proxy" / "proxy.py")
    shutil.copy2(REPO_ROOT / "pepper" / "stub.py", out_dir / "stub" / "stub.py")
    env = out_dir / ".env"
    env.write_text(f"DB_PASSWORD={plan.db_password}\nWILDFLY_IMAGE={plan.server_image}\n", encoding="utf-8")
    env.chmod(0o600)
    written += [out_dir / "proxy" / "proxy.py", out_dir / "stub" / "stub.py", env]
    return written


# --------------------------------------------------------------- levantar

def _compose(out_dir: Path, *args: str, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", "-f", str(out_dir / "docker-compose.yml"), *args],
                          capture_output=True, text=True, timeout=timeout)


def _psql(out_dir: Path, plan: Plan, sql: str) -> str:
    result = _compose(out_dir, "exec", "-T", "db", "psql", "-U", plan.db_user, "-d", plan.db_name, "-Atc", sql)
    return result.stdout.strip() if result.returncode == 0 else ""


def _http(url: str, timeout: int = 5) -> Tuple[Optional[int], str]:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=timeout) as response:
            return response.status, response.read(4096).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        return error.code, ""
    except Exception:
        return None, ""


def bring_up(plan: Plan, profile: Profile, out_dir: Path, wait_s: int = 300,
             log=print) -> Tuple[str, List[Dict[str, str]], List[Dict[str, str]]]:
    """Levanta, restaura si hace falta, espera, verifica y valida. → (estado, validaciones, faltantes)."""
    from pepper.isolate import check_live, check_static, resolve_compose

    validations: List[Dict[str, str]] = []
    missing: List[Dict[str, str]] = []
    compose_path = out_dir / "docker-compose.yml"
    compose, resolved = resolve_compose(compose_path)
    report = check_static(compose, plan.external_hosts, "ingress", resolved=resolved, compose_dir=out_dir)
    if report.verdict != "VERIFIED":
        for finding in report.errors + report.unknowns:
            log(f"  ✗ {finding.check}")
        return "FAILED", [{"check": "aislamiento del compose", "result": "fail",
                           "detail": report.verdict}], missing
    validations.append({"check": "aislamiento del compose verificado antes de levantar", "result": "pass",
                        "detail": f"{len([f for f in report.findings if f.level == 'ok'])} comprobaciones"})

    log("  levantando base y stub…")
    up = _compose(out_dir, "up", "-d", "db", "stub")
    if up.returncode != 0:
        return "FAILED", validations + [{"check": "docker compose up db stub", "result": "fail", "detail": up.stderr[-400:]}], missing
    for _ in range(60):
        if _psql(out_dir, plan, "select 1") == "1":
            break
        time.sleep(2)
    tables = _psql(out_dir, plan, "select count(*) from pg_tables where schemaname not in ('pg_catalog','information_schema')")
    if tables in ("", "0"):
        log("  restaurando el respaldo (una vez; la base estaba vacía)…")
        restore = _compose(out_dir, "--profile", "restore", "run", "--rm", "restore", timeout=3600)
        tail = "\n".join(restore.stdout.strip().splitlines()[-6:])
        log("    " + tail.replace("\n", "\n    "))
        tables = _psql(out_dir, plan, "select count(*) from pg_tables where schemaname not in ('pg_catalog','information_schema')")
    validations.append({"check": "la base tiene los datos restaurados", "result": "pass" if tables not in ("", "0") else "fail",
                        "detail": f"{tables or 0} tablas en {plan.db_name}"})
    foreign = _psql(out_dir, plan, "select string_agg(srvname||'→'||coalesce((select option_value from pg_options_to_table(srvoptions) where option_name='host'),'?'),', ') from pg_foreign_server")
    if foreign:
        validations.append({"check": "servidores foráneos re-apuntados al stub", "result": "pass" if all(plan.stub_ip in f for f in foreign.split(", ")) else "fail", "detail": foreign})

    log("  levantando app e ingress…")
    up = _compose(out_dir, "up", "-d", "app", "ingress")
    if up.returncode != 0:
        return "FAILED", validations + [{"check": "docker compose up app ingress", "result": "fail", "detail": up.stderr[-400:]}], missing
    ready_re = re.compile(profile.data.get("rehydrate", {}).get("ready_log_pattern") or "Started|started")
    started = time.time()
    ready = False
    while time.time() - started < wait_s:
        logs = subprocess.run(["docker", "compose", "-f", str(compose_path), "logs", "--no-log-prefix", "app"],
                              capture_output=True, text=True).stdout
        if ready_re.search(logs):
            ready = True
            break
        time.sleep(5)
    validations.append({"check": "el servidor de aplicaciones arrancó", "result": "pass" if ready else "fail",
                        "detail": f"patrón {ready_re.pattern!r} en {int(time.time() - started)} s" if ready else f"sin señal de arranque en {wait_s} s"})
    if not ready:
        return "FAILED", validations, missing
    errors = len(re.findall(r"\bERROR\b", logs))
    validations.append({"check": "sin líneas ERROR en el arranque", "result": "pass" if errors == 0 else "fail", "detail": f"{errors} líneas ERROR"})

    time.sleep(3)
    status, body = _http(f"http://127.0.0.1:{plan.host_port}/")
    validations.append({"check": "la raíz responde por el ingress (solo loopback)", "result": "pass" if status and status < 500 else "fail",
                        "detail": f"HTTP {status}" if status else "sin respuesta"})
    live = check_live(compose_path, plan.external_hosts, "ingress")
    ok = live.verdict == "VERIFIED"
    validations.append({"check": "aislamiento verificado en vivo (contenedores según Docker)", "result": "pass" if ok else "fail",
                        "detail": f"{live.verdict}: {len([f for f in live.findings if f.level == 'ok'])} comprobaciones" + (
                            "" if ok else "; " + "; ".join(f.check for f in live.errors + live.unknowns)[:300])})
    stub_logs = subprocess.run(["docker", "compose", "-f", str(compose_path), "logs", "--no-log-prefix", "stub"],
                               capture_output=True, text=True).stdout
    stub_hits = stub_logs.count('"method"')
    validations.append({"check": "el stub no recibió peticiones al arrancar", "result": "pass" if stub_hits == 0 else "fail",
                        "detail": f"{stub_hits} peticiones"})
    if plan.external_hosts:
        missing.append({"missing": "servicios externos reales: " + ", ".join(plan.external_hosts),
                        "recommended_evidence": "inalcanzables por diseño; stubeados (responden error y registran)"})
    for dep in plan.external_by_ip:
        missing.append({"missing": f"dependencia por IP directa {dep}: falla sin salir y sin registro",
                        "recommended_evidence": "una segunda subred interna con el stub en esa IP"})
    if not ok:
        return "FAILED", validations, missing
    failed = [v for v in validations if v["result"] == "fail"]
    return ("FAILED" if failed else "PARTIAL" if missing else "READY"), validations, missing


def write_environment(plan: Plan, profile: Profile, status: str, validations: List[Dict[str, str]],
                      missing: List[Dict[str, str]], docs_dir: Path, out_dir: Path) -> Tuple[Path, Path]:
    docs_dir.mkdir(parents=True, exist_ok=True)
    env = {
        "schema_version": "0.1.0",
        "status": status,
        "profile_id": profile.id,
        "support_tier": 1,
        "components": [
            {"name": "db", "role": "database", "engine": "postgresql", "version": plan.postgres_version,
             "container_image": f"postgres:{plan.postgres_version}", "endpoint": f"{plan.db_ip}:{plan.db_port}/{plan.db_name}",
             "status": "running" if status in ("READY", "PARTIAL") else "failed", "data_restored": status in ("READY", "PARTIAL")},
            {"name": "app", "role": "backend", "engine": plan.server, "artifact": plan.artifact.name,
             "container_image": plan.server_image, "endpoint": f"{plan.app_ip}:8080",
             "status": "running" if status in ("READY", "PARTIAL") else "failed"},
            {"name": "ingress", "role": "proxy", "engine": "pepper-proxy", "container_image": "python:3-alpine",
             "endpoint": f"http://127.0.0.1:{plan.host_port}", "status": "running" if status in ("READY", "PARTIAL") else "failed"},
            {"name": "stub", "role": "external", "engine": "pepper-stub", "container_image": "python:3-alpine",
             "endpoint": plan.stub_ip, "status": "running" if status in ("READY", "PARTIAL") else "failed"},
        ],
        "validations": validations,
        "missing_evidence": missing,
        "notes": plan.deviations + plan.notes + [f"compose: {out_dir / 'docker-compose.yml'} · apagar: docker compose -f {out_dir / 'docker-compose.yml'} down -v"],
    }
    env_path = docs_dir / "environment.json"
    env_path.write_text(json.dumps(env, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [f"# Rehidratación — {plan.artifact.name}", "",
             f"> Estado: **{status}** · perfil `{profile.id}` · {datetime.now().astimezone().isoformat(timespec='minutes')}",
             "> Entorno desechable y aislado; la base vive en la IP y con el nombre que el artefacto espera; todo lo externo va al stub.", "",
             "## Componentes", "", "| Servicio | Motor / imagen | Endpoint |", "|---|---|---|"]
    for c in env["components"]:
        lines.append(f"| `{c['name']}` | {c.get('engine', '')} (`{c.get('container_image', '')}`) | `{c.get('endpoint', '')}` |")
    lines += ["", "## Validaciones", "", "| | Comprobación | Detalle |", "|---|---|---|"]
    for v in validations:
        mark = {"pass": "✅", "fail": "❌", "skipped": "⏭"}[v["result"]]
        lines.append(f"| {mark} | {v['check']} | {v.get('detail', '')} |")
    if missing:
        lines += ["", "## Qué no se podrá observar", ""] + [f"- **{m['missing']}** — {m.get('recommended_evidence', '')}" for m in missing]
    if plan.deviations or plan.notes:
        lines += ["", "## Desviaciones y notas", ""] + [f"- {d}" for d in plan.deviations + plan.notes]
    lines += ["", "## Apagado", "", "```bash", f"docker compose -f {out_dir / 'docker-compose.yml'} down -v   # -v borra el volumen con datos reales", "```", ""]
    val_path = docs_dir / "validation.md"
    val_path.write_text("\n".join(lines), encoding="utf-8")
    return env_path, val_path
