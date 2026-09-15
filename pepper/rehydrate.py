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
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pepper import REPO_ROOT
from pepper.profiles import Profile

DEFAULT_PORT = 18080
# Por defecto, los desplegables de la JVM. Un stack cuyo front viaja en .zip o .tgz lo
# declara en `rehydrate.artifact_suffixes`: qué cuenta como desplegable es del perfil,
# no del núcleo — si no, el front de un sistema es invisible (2026-09-15).
_ARTIFACT_SUFFIXES = (".war", ".ear", ".jar")
_DUMP_SUFFIXES = (".dump", ".backup")   # formato custom de pg_dump: lo único que pg_restore restaura y el lector lee
_FILES_KEY_RE = re.compile(r"(?i)(ruta|path|dir|folder)")
_FILES_KEY_EXCLUDE_RE = re.compile(r"(?i)redirect|direccion|context[-.]?path|servlet[-.]?path|classpath|url")
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


def split_documents(text: str) -> List[str]:
    """Un application.yml de Spring Boot 1.x suele traer varios documentos separados por `---`,
    cada uno con `spring.profiles: <nombre>`. Aplanarlos juntos mezclaba datasources y llamaba
    `default` al perfil elegido (auditoría 2026-09-11)."""
    docs, current = [], []
    for line in text.splitlines():
        if re.fullmatch(r"---\s*", line):
            docs.append("\n".join(current)); current = []
        else:
            current.append(line)
    docs.append("\n".join(current))
    return [d for d in docs if d.strip()]


def read_artifact_configs(artifact: Path, patterns: List[str]) -> Dict[str, Dict[str, str]]:
    """{nombre-de-perfil: config} de cada archivo (y documento) de configuración dentro del artefacto."""
    configs: Dict[str, Dict[str, str]] = {}
    with zipfile.ZipFile(artifact) as archive:
        for name in sorted(archive.namelist()):
            if not any(re.search(p, name) for p in patterns):
                continue
            stem = Path(name).stem  # application-prod
            file_profile = stem.split("-", 1)[1] if "-" in stem else "default"
            for document in split_documents(archive.read(name).decode("utf-8", errors="replace")):
                cfg = parse_config(document)
                declared = cfg.get("spring.profiles") or cfg.get("spring.config.activate.on-profile")
                profile = declared or file_profile
                configs.setdefault(profile, {}).update(cfg)
    return configs


def choose_spring_profile(configs: Dict[str, Dict[str, str]]) -> Tuple[str, Dict[str, str], List[str]]:
    """El perfil completo (url, usuario y contraseña del datasource). Manda `spring.profiles.active`
    del documento base si nombra uno completo; si no, `prod`; si hay varios, se declara cuáles había.
    → (nombre, config fusionada con la base, desviaciones)."""
    base = configs.get("default", {})
    active = [a.strip() for a in (base.get("spring.profiles.active") or "").split(",") if a.strip()]
    complete: Dict[str, Dict[str, str]] = {}
    for name, cfg in configs.items():
        if name == "default":
            continue
        merged = dict(base); merged.update(cfg)
        url = next((v for k, v in merged.items() if k.endswith("datasource.url")), "")
        user = next((v for k, v in merged.items() if k.endswith("datasource.username")), "")
        pwd = next((v for k, v in merged.items() if k.endswith("datasource.password")), None)
        if url and user and pwd is not None:
            complete[name] = merged
    deviations: List[str] = []
    if not complete:
        # solo el documento base: vale si él mismo está completo
        url = next((v for k, v in base.items() if k.endswith("datasource.url")), "")
        user = next((v for k, v in base.items() if k.endswith("datasource.username")), "")
        pwd = next((v for k, v in base.items() if k.endswith("datasource.password")), None)
        if url and user and pwd is not None:
            return "default", dict(base), deviations
        raise Blocked("ningún perfil de configuración dentro del artefacto trae url, usuario y contraseña del datasource: "
                      "no dice a qué conectarse. Consigue la configuración externa del ambiente.")
    chosen = next((a for a in active if a in complete), None)
    if chosen is None:
        chosen = next((n for n in complete if n == "prod"), None) or next((n for n in complete if "prod" in n), None) \
            or sorted(complete)[0]
        if len(complete) > 1:
            deviations.append(f"varios perfiles de configuración completos ({', '.join(sorted(complete))}); "
                              f"spring.profiles.active no señala ninguno de ellos: se usa '{chosen}'")
    elif active and chosen != active[0]:
        deviations.append(f"spring.profiles.active = {','.join(active)}; el primero completo es '{chosen}'")
    return chosen, complete[chosen], deviations


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
    db_alias: str = ""
    gateway_ip: str = ""
    dump_sha: str = ""
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
            "db_alias": "[" + (self.db_alias or "") + "]", "gateway_ip": self.gateway_ip, "dump_sha": self.dump_sha,
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


@dataclass
class Component:
    """Una pieza desplegable del legacy: un servicio, un front, lo que sea.

    Un legacy no siempre es un desplegable. Puede ser tres servicios y un front, o un
    backend y un panel aparte. Cada pieza tiene su artefacto, su configuración embebida,
    su puerto y su IP; el perfil dice cómo reconocerla y con qué imagen corre.
    """
    name: str
    artifact: Path
    role: str          # backend · frontend · gateway · discovery · worker
    engine: str        # lo que el perfil llame: java, static, node…
    image: str
    ip: str
    port: int
    config_profile: str = ""
    notes: List[str] = field(default_factory=list)


def _member_names(artifact: Path) -> List[str]:
    if not (artifact.is_file() and zipfile.is_zipfile(artifact)):
        return []
    with zipfile.ZipFile(artifact) as archive:
        return archive.namelist()


def _rule_matches(rule: Dict[str, Any], artifact: Path, members: List[str], configs: Dict[str, Dict[str, str]]) -> bool:
    """Una regla de clasificación del perfil contra UN artefacto. Todo lo que declare debe cumplirse."""
    when = rule.get("when") or {}
    glob = when.get("member_glob")
    if glob and not any(fnmatch(name, glob) for name in members):
        return False
    needle = when.get("config_contains")
    if needle:
        blob = "\n".join(f"{k}={v}" for cfg in configs.values() for k, v in cfg.items())
        if needle not in blob:
            return False
    name_re = when.get("name_matches")
    if name_re and not re.search(name_re, artifact.name):
        return False
    return bool(when)


def classify_components(artifacts: List[Path], profile: Profile, subnet_base: str,
                        first_ip: int = 10) -> Tuple[List[Component], List[str]]:
    """Reparte los artefactos en componentes según las reglas del perfil.

    El núcleo no sabe qué es un gateway ni un descubrimiento de servicios: aplica las
    reglas que el perfil declara y, lo que ninguna regla reconozca, lo dice en vez de
    inventarle un papel.
    """
    recipe = profile.data.get("rehydrate", {})
    spec = recipe.get("components") or {}
    rules: List[Dict[str, Any]] = spec.get("classify") or []
    images: Dict[str, Dict[str, str]] = recipe.get("server_images") or {}
    patterns = recipe.get("config_patterns") or [r"application.*\.(yml|yaml|properties)$"]
    components: List[Component] = []
    sin_clasificar: List[str] = []
    for index, artifact in enumerate(artifacts):
        members = _member_names(artifact)
        try:
            configs = read_artifact_configs(artifact, patterns)
        except (zipfile.BadZipFile, OSError):
            configs = {}
        rule = next((r for r in rules if _rule_matches(r, artifact, members, configs)), None)
        if rule is None:
            sin_clasificar.append(artifact.name)
            continue
        engine = rule.get("engine", "app")
        image = rule.get("image") or next(iter((images.get(engine) or {}).values()), "")
        # Solo la pieza que habla con la base tiene datasource; una puerta de enlace o un
        # descubrimiento de servicios no, y eso es normal — aquí solo se busca su puerto.
        try:
            profile_name, cfg, _ = choose_spring_profile(configs) if configs else ("", {}, [])
        except Blocked:
            profile_name, cfg = "", next((c for c in configs.values() if c.get("server.port")), {})
        port_raw = rule.get("port") or cfg.get("server.port") or next(
            (c["server.port"] for c in configs.values() if c.get("server.port")), None)
        try:
            port = int(str(port_raw).strip())
        except (TypeError, ValueError):
            port = 8080
        components.append(Component(
            name=re.sub(r"[^a-z0-9]+", "-", artifact.stem.split("-")[0].lower()).strip("-") or f"pieza{index}",
            artifact=artifact, role=rule.get("role", "backend"), engine=engine, image=image,
            ip=f"{subnet_base}.{first_ip + index}", port=port, config_profile=profile_name))
    return components, sin_clasificar


def find_all_inputs(legacy_dir: Path, artifact_suffixes: Optional[Tuple[str, ...]] = None) -> Tuple[List[Path], List[Path]]:
    """TODOS los desplegables y TODOS los respaldos, de mayor a menor.

    Un legacy no siempre es un desplegable: puede ser tres servicios y un front (un
    sistema de microservicios), o un backend y un panel aparte. Quedarse con el más
    grande y callar los demás es exactamente la clase de silencio que esta herramienta
    no se permite: quien llama decide qué usa, pero tiene que SABER qué había.
    """
    suffixes = tuple(s.lower() for s in (artifact_suffixes or _ARTIFACT_SUFFIXES))
    artifacts = sorted((p for p in legacy_dir.iterdir() if p.suffix.lower() in suffixes),
                       key=lambda p: -p.stat().st_size)
    dumps = sorted((p for p in legacy_dir.iterdir() if p.suffix.lower() in _DUMP_SUFFIXES),
                   key=lambda p: -p.stat().st_size)
    if not artifacts:
        raise Blocked(f"no hay desplegable ({'/'.join(suffixes)}) en {legacy_dir}")
    if not dumps:
        raise Blocked(f"no hay respaldo de la base en {legacy_dir}: se necesita el formato custom de pg_dump (`pg_dump -Fc`, .dump/.backup)")
    return artifacts, dumps


def find_inputs(legacy_dir: Path) -> Tuple[Path, Path]:
    artifacts, dumps = find_all_inputs(legacy_dir)
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
    # Solo cuentan los servidores de APLICACIÓN (los que el perfil sabe reconocer por descriptor):
    # una nota que diga "la base es postgres 12" no puede convertir al app en un contenedor de
    # PostgreSQL (auditoría 2026-09-11).
    app_servers = [s for s in images if s in descriptors]
    noted = next((s for s in app_servers if re.search(rf"(?i)\b{s}\b", notes_text)), None)
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

    recipe_early = profile.data.get("rehydrate", {})
    artifacts, dumps = find_all_inputs(legacy_dir, recipe_early.get("artifact_suffixes"))
    artifact, dump = artifacts[0], dumps[0]
    notes_text = notes_path.read_text(encoding="utf-8", errors="replace") if notes_path and notes_path.is_file() else ""
    recipe = profile.data.get("rehydrate", {})
    # Un sistema de varios desplegables (microservicios, o un backend y un panel aparte) no se
    # levanta escogiendo el archivo más grande: sin sus compañeros el ambiente arranca a medias
    # y todo lo que se observe encima es basura. Mientras el perfil no sepa repartirlos en
    # servicios, esto se detiene diciendo qué hay — no se finge un ambiente (2026-09-15).
    if len(artifacts) > 1 and not recipe.get("components"):
        lista = "\n".join(f"      · {a.name}  ({a.stat().st_size // (1024 * 1024)} MB)" for a in artifacts)
        raise Blocked(
            f"el legacy trae {len(artifacts)} desplegables y el perfil {profile.id} levanta uno solo:\n{lista}\n"
            "    Para seguir, una de dos: deja en legacy/ únicamente el desplegable a levantar "
            "(los demás no se mapean ni se levantan), o usa un perfil que declare `rehydrate.components` "
            "y sepa repartirlos en servicios.")
    configs = read_artifact_configs(artifact, recipe.get("config_patterns") or [r"application.*\.(yml|yaml|properties)$"])
    if not configs:
        raise Blocked("el artefacto no trae configuración embebida (application*.yml) y no se dio configuración externa")
    spring_profile, cfg, profile_deviations = choose_spring_profile(configs)
    url = next(v for k, v in cfg.items() if k.endswith("datasource.url"))
    m = _JDBC_RE.search(url)
    if not m:
        raise Blocked(f"no entiendo la URL del datasource: {url!r}")
    engine, host, port, db_name = m.group(1), m.group(2), int(m.group(3) or 5432), m.group(4)
    if engine != "postgresql":
        raise Blocked(f"motor {engine}: este perfil solo reconstruye PostgreSQL")
    db_user = next(v for k, v in cfg.items() if k.endswith("datasource.username"))
    db_password = next(v for k, v in cfg.items() if k.endswith("datasource.password"))
    deviations: List[str] = list(profile_deviations)
    notes: List[str] = []
    if "${" in db_password:
        raise Blocked(f"la contraseña del datasource es una referencia sin resolver ({db_password!r}): "
                      "el artefacto espera una variable de entorno que no trae; consíguela")
    if host in ("localhost", "127.0.0.1", "::1"):
        raise Blocked(f"el datasource del perfil '{spring_profile}' apunta a {host}: dentro del contenedor del app eso es el app mismo, "
                      "no una base. Ese perfil no describe un ambiente reconstruible; elige otro o consigue la configuración externa")
    db_alias = ""
    try:
        db_ip = str(ipaddress.IPv4Address(host))
    except ValueError:
        db_ip, db_alias = "10.100.0.2", host
        deviations.append(f"el datasource apunta al nombre {host!r}: la base queda en {db_ip} y ese nombre es su alias en la red")
    net = ipaddress.IPv4Network(f"{db_ip}/24", strict=False)
    hosts = [str(h) for h in net.hosts()]
    taken = {db_ip}
    stub_ip = next(h for h in hosts[1:] if h not in taken); taken.add(stub_ip)
    app_ip = next(h for h in hosts[8:] if h not in taken); taken.add(app_ip)
    dns_sink = next(h for h in reversed(hosts) if h not in taken); taken.add(dns_sink)
    # la puerta de enlace la fija PEPPER: si el datasource cae en la .1, Docker chocaría con ella
    gateway_ip = next(h for h in hosts if h not in taken); taken.add(gateway_ip)

    try:
        info = pgdump.read_toc(dump)
    except ValueError as error:
        raise Blocked(f"el respaldo {dump.name} no se puede leer como formato custom de pg_dump ({error}); "
                      "pg_restore tampoco lo restauraría. Consigue un respaldo hecho con `pg_dump -Fc`")
    dump_sha = _sha256(dump)[:16]
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
                if h not in by_ip:
                    # dentro o fuera de la subred de la base da igual: nadie responde ahí y no se puede aliasear
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
    files_root = next((v for k, v in cfg.items()
                       if _FILES_KEY_RE.search(k) and not _FILES_KEY_EXCLUDE_RE.search(k)
                       and v.startswith("/") and v.rstrip("/") not in ("", "/")), "/data")

    # El nombre del proyecto (y de sus volúmenes) lleva un hash del respaldo: dos legacies con el
    # mismo prefijo, o dos respaldos del mismo sistema, no comparten base (auditoría 2026-09-11).
    prefix = re.sub(r"[^a-z0-9]+", "-", artifact.stem.split("-")[0].lower()).strip("-") or "legacy"
    stack_name = f"{prefix}-{dump_sha[:8]}"
    return Plan(stack_name=stack_name, artifact=artifact, dump=dump, spring_profile=spring_profile,
                db_engine=engine, db_ip=db_ip, db_port=port, db_name=db_name, db_user=db_user, db_password=db_password,
                subnet=str(net), app_ip=app_ip, stub_ip=stub_ip, dns_sink=dns_sink,
                server=server, server_image=server_image, postgres_version=postgres_version,
                pg_restore_version=pg_restore_version, external_hosts=external, external_by_ip=by_ip,
                stub_ports=stub_ports, app_package_env=app_package_env, files_root=files_root,
                create_roles=[o for o in info.owners() if o != db_user], host_port=host_port,
                db_alias=db_alias, gateway_ip=gateway_ip, dump_sha=dump_sha,
                deviations=deviations, notes=notes)


def _sha256(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def env_line(name: str, value: str) -> str:
    """Una línea de `.env` que `docker compose` lea tal cual: `$` se escapa como `$$` (si no,
    `ab$cd` se interpola a `ab`), y las comillas dobles protegen espacios y `#`."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "$$")
    return f'{name}="{escaped}"'


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
    env.write_text(env_line("DB_PASSWORD", plan.db_password) + "\n", encoding="utf-8")
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
    alive = False
    for _ in range(60):
        if _psql(out_dir, plan, "select 1") == "1":
            alive = True
            break
        time.sleep(2)
    if not alive:
        db_logs = subprocess.run(["docker", "compose", "-f", str(compose_path), "logs", "--no-log-prefix", "--tail", "15", "db"],
                                 capture_output=True, text=True).stdout
        return "FAILED", validations + [{"check": "la base responde", "result": "fail",
                                         "detail": "sin respuesta en 120 s · " + db_logs.strip()[-300:]}], missing
    # Los comentarios de una BASE viven en pg_shdescription: obj_description() no los ve (devolvía
    # vacío y un restore correcto salía como "marca ausente").
    marker_sql = "select shobj_description((select oid from pg_database where datname = current_database()), 'pg_database')"
    tables = _psql(out_dir, plan, "select count(*) from pg_tables where schemaname not in ('pg_catalog','information_schema')")
    marker = _psql(out_dir, plan, marker_sql)
    expected_marker = f"pepper:restored:{plan.dump_sha}"
    if tables not in ("", "0") and marker != expected_marker:
        return "FAILED", validations + [{"check": "la base del volumen corresponde a este respaldo", "result": "fail",
                                         "detail": f"el volumen ya trae {tables} tablas de otra restauración (marca {marker or 'ausente'}); "
                                                   f"bájalo con `docker compose -f {compose_path} down -v` y repite"}], missing
    if tables in ("", "0"):
        log("  restaurando el respaldo (una vez; la base estaba vacía)…")
        restore = _compose(out_dir, "--profile", "restore", "run", "--rm", "restore", timeout=3600)
        out = restore.stdout
        log("    " + "\n    ".join(out.strip().splitlines()[-8:]))
        status_line = re.search(r"PEPPER_RESTORE status=(\d+) errors=(\d+)", out)
        status = int(status_line.group(1)) if status_line else -1
        ignored = int(status_line.group(2)) if status_line else 0
        tables = _psql(out_dir, plan, "select count(*) from pg_tables where schemaname not in ('pg_catalog','information_schema')")
        marker = _psql(out_dir, plan, marker_sql)
        if status != 0 and status != 1 or marker != expected_marker or tables in ("", "0"):
            return "FAILED", validations + [{"check": "la base tiene los datos restaurados", "result": "fail",
                                             "detail": f"pg_restore terminó con código {status} (restore.sh {restore.returncode}); "
                                                       f"{tables or 0} tablas; marca {marker or 'ausente'} · " + out.strip()[-400:]}], missing
        validations.append({"check": "la base tiene los datos restaurados", "result": "pass",
                            "detail": f"{tables} tablas en {plan.db_name}" + (f"; pg_restore ignoró {ignored} error(es), listados en el log de la restauración" if ignored else "")})
    else:
        validations.append({"check": "la base tiene los datos restaurados", "result": "pass",
                            "detail": f"{tables} tablas en {plan.db_name} (ya restaurada de este mismo respaldo)"})
    foreign = _psql(out_dir, plan, "select string_agg(srvname||'→'||coalesce((select option_value from pg_options_to_table(srvoptions) where option_name='host'),'?'),', ') from pg_foreign_server")
    if foreign:
        validations.append({"check": "servidores foráneos re-apuntados al stub", "result": "pass" if all(plan.stub_ip in f for f in foreign.split(", ")) else "fail", "detail": foreign})

    log("  levantando app e ingress…")
    since = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    up = _compose(out_dir, "up", "-d", "--force-recreate", "app", "ingress")
    if up.returncode != 0:
        return "FAILED", validations + [{"check": "docker compose up app ingress", "result": "fail", "detail": up.stderr[-400:]}], missing
    recipe = profile.data.get("rehydrate", {})
    ready_re = re.compile(recipe.get("ready_log_pattern") or "Started|started")
    failed_re = re.compile(recipe["failed_log_pattern"]) if recipe.get("failed_log_pattern") else None
    started = time.time()
    ready, failed_hit = False, None
    while time.time() - started < wait_s:
        logs = subprocess.run(["docker", "compose", "-f", str(compose_path), "logs", "--no-log-prefix", "--since", since, "app"],
                              capture_output=True, text=True).stdout
        if failed_re and failed_re.search(logs):
            failed_hit = failed_re.search(logs).group(0)
            break
        if ready_re.search(logs):
            ready = True
            break
        time.sleep(5)
    validations.append({"check": "el servidor de aplicaciones arrancó", "result": "pass" if ready else "fail",
                        "detail": f"patrón {ready_re.pattern!r} en {int(time.time() - started)} s" if ready
                        else f"el despliegue falló ({failed_hit}) en {int(time.time() - started)} s" if failed_hit
                        else f"sin señal de arranque en {wait_s} s"})
    if not ready:
        return "FAILED", validations, missing
    errors = len(re.findall(r"\bERROR\b", logs))
    validations.append({"check": "sin líneas ERROR en el arranque", "result": "pass" if errors == 0 else "fail", "detail": f"{errors} líneas ERROR"})

    time.sleep(3)
    status, body = _http(f"http://127.0.0.1:{plan.host_port}/")
    validations.append({"check": "la raíz responde por el ingress (solo loopback)", "result": "pass" if status and status < 400 else "fail",
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
             "status": "running" if status in ("READY", "PARTIAL") else "unknown", "data_restored": status in ("READY", "PARTIAL")},
            {"name": "app", "role": "backend", "engine": plan.server, "artifact": plan.artifact.name,
             "container_image": plan.server_image, "endpoint": f"{plan.app_ip}:8080",
             "status": "running" if status in ("READY", "PARTIAL") else "unknown"},
            {"name": "ingress", "role": "proxy", "engine": "pepper-proxy", "container_image": "python:3-alpine",
             "endpoint": f"http://127.0.0.1:{plan.host_port}", "status": "running" if status in ("READY", "PARTIAL") else "unknown"},
            {"name": "stub", "role": "external", "engine": "pepper-stub", "container_image": "python:3-alpine",
             "endpoint": plan.stub_ip, "status": "running" if status in ("READY", "PARTIAL") else "unknown"},
        ],
        "validations": validations,
        "missing_evidence": missing,
        # el contrato pide un string: una lista aquí hacía que environment.json no validara contra su propio schema
        "notes": " · ".join(plan.deviations + plan.notes + [f"compose: {out_dir / 'docker-compose.yml'} · apagar: docker compose -f {out_dir / 'docker-compose.yml'} down -v"]),
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
