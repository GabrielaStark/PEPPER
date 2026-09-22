"""Verificación de aislamiento de un entorno rehidratado — fail-closed.

Un entorno rehidratado corre con la configuración del legacy: sus IPs, sus hosts,
sus credenciales de producción. Si tiene salida de red — y la máquina del ingeniero
suele tener VPN a la red institucional — el legacy **alcanza producción**: una vista
con `dblink`, un cliente de un bus, un job al arrancar. Basta una lectura.

Por eso el aislamiento no puede depender de que el agente se acuerde de escribirlo
en el compose (Principio 3), y por eso el veredicto es **fail-closed** (auditoría
C-01): lo que no se pudo comprobar no cuenta como verde.

    VERIFICADO   todo comprobado, ninguna fuga
    NO AISLADO   al menos una fuga demostrada
    NO VERIFICADO  algo no se pudo comprobar (compose sin resolver, sin
                   contenedores, red ilegible…): bloquea igual que una fuga

Invariante: **ningún contenedor del legacy puede alcanzar nada fuera de su red
interna.** La única excepción es la red de publicación del ingress —Docker no
publica puertos de un contenedor que solo está en redes `internal`; comprobado:
el puerto no responde desde el host— y esa red la usa solo él, de modo que el
único código con salida es el proxy verificado. No basta con llamarse `ingress`: debe ser el proxy de
PEPPER, sin entrypoint, con argv/upstream verificables y un único montaje `:ro`
cuyo hash coincide con `pepper/proxy.py`.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

DEFAULT_INGRESS = "ingress"

_FORBIDDEN_NAMESPACES = ("pid", "ipc", "uts")
_DOCKER_SOCKET = "docker.sock"
_PYTHON_IMAGE = re.compile(r"^python:\d+(?:\.\d+)?-alpine(?:@sha256:[0-9a-f]{64})?$")
_PROXY_PREFIX = ["python3", "-u", "/pepper-proxy.py"]
_PROXY_OPTIONS = {"--listen", "--upstream", "--timeout"}


@dataclass
class Finding:
    level: str  # "error" | "unknown" | "warn" | "ok"
    check: str
    detail: str


@dataclass
class Report:
    findings: List[Finding] = field(default_factory=list)

    def add(self, level: str, check: str, detail: str = "") -> None:
        self.findings.append(Finding(level, check, detail))

    @property
    def errors(self) -> List[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def unknowns(self) -> List[Finding]:
        return [f for f in self.findings if f.level == "unknown"]

    @property
    def warnings(self) -> List[Finding]:
        return [f for f in self.findings if f.level == "warn"]

    @property
    def verdict(self) -> str:
        if self.errors:
            return "FAILED"
        if self.unknowns:
            return "UNKNOWN"
        return "VERIFIED"

    @property
    def isolated(self) -> bool:
        """Verde solo cuando TODO se comprobó: lo no verificado bloquea (fail-closed)."""
        return self.verdict == "VERIFIED"


def bundled_proxy_hash() -> Optional[str]:
    """SHA-256 del proxy que trae esta instalación de PEPPER."""
    from pepper import manifest as evidence_manifest
    from pepper import proxy as proxy_module

    path = Path(proxy_module.__file__)
    return evidence_manifest.sha256_file(path) if path.is_file() else None


def resolve_compose(path: Path) -> Tuple[Dict[str, Any], bool]:
    """(compose, resuelto). Resuelto=True solo vía `docker compose config`.

    El fallback YAML no sustituye variables: `internal: ${INTERNAL:-false}` se ve
    inocente en el archivo y quita el aislamiento en ejecución. Por eso el
    fallback jamás puede producir un verde (C-01): quien lo use recibe UNKNOWN.
    """
    # `docker compose config` OMITE los servicios bajo `profiles:` que no estén activos.
    # Un servicio escondido ahí (el `restore`, que corre con la contraseña real de
    # producción; o un exfiltrador) pasaba en verde sin ser mirado (auditoría
    # 2026-09-11). Se activan TODOS los profiles declarados: se verifica lo que Docker
    # PUEDE ejecutar, no solo lo que ejecuta hoy.
    base = ["docker", "compose", "-f", str(path)]
    profiles = subprocess.run(base + ["config", "--profiles"], capture_output=True, text=True)
    if profiles.returncode == 0:
        for name in profiles.stdout.split():
            base += ["--profile", name]
    result = subprocess.run(base + ["config", "--format", "json"], capture_output=True, text=True)
    if result.returncode == 0:
        return json.loads(result.stdout), True
    try:
        import yaml  # noqa: WPS433 — solo como respaldo si no hay Docker
    except ImportError:
        raise RuntimeError(
            f"no pude resolver {path} con `docker compose config` "
            f"({result.stderr.strip()[:200]}) y pyyaml no está instalado"
        )
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}, False


def _internal_networks(compose: Dict[str, Any]) -> Dict[str, bool]:
    networks = compose.get("networks") or {}
    return {name: bool((spec or {}).get("internal")) for name, spec in networks.items()}


def _internal_subnets(compose: Dict[str, Any]) -> List[ipaddress.IPv4Network]:
    subnets = []
    for name, spec in (compose.get("networks") or {}).items():
        if not (spec or {}).get("internal"):
            continue
        for entry in ((spec.get("ipam") or {}).get("config") or []):
            if entry.get("subnet"):
                try:
                    subnets.append(ipaddress.ip_network(entry["subnet"], strict=False))
                except ValueError:
                    continue
    return subnets


def _service_networks(service: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    nets = service.get("networks")
    if not nets:
        return {"default": {}}
    if isinstance(nets, list):
        return {name: {} for name in nets}
    return {name: (spec or {}) for name, spec in nets.items()}


def _extra_hosts(service: Dict[str, Any]) -> Dict[str, str]:
    raw = service.get("extra_hosts") or {}
    if isinstance(raw, dict):
        return {k: str(v) for k, v in raw.items()}
    mapping = {}
    for item in raw:
        host, _, ip = str(item).partition(":")
        mapping[host] = ip
    return mapping


def _is_internal_ip(ip: str, subnets: Iterable[ipaddress.IPv4Network]) -> bool:
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(address in subnet for subnet in subnets)


def _volume_parts(volume: Any) -> Tuple[str, str, bool]:
    """(source, target, readonly) de un montaje en forma corta o larga."""
    if isinstance(volume, str):
        parts = volume.split(":")
        if len(parts) == 1:
            return "", parts[0], False
        readonly = len(parts) >= 3 and "ro" in parts[-1].split(",")
        return parts[0], parts[1], readonly
    if isinstance(volume, dict):
        return str(volume.get("source", "")), str(volume.get("target", "")), bool(volume.get("read_only"))
    return "", "", False


def _is_bind(source: str) -> bool:
    return source.startswith(("/", "./", "../", "~")) or "/" in source


def _parse_proxy_command(name: str, command: Any, report: Report) -> Optional[Tuple[str, int]]:
    """Acepta solo argv declarativo; nada de shell, flags extra ni valores ambiguos."""
    if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
        report.add("error", f"`{name}` (ingress) usa un command no verificable",
                   "el proxy debe declararse como lista argv; los strings pasan por shell y no se consideran seguros")
        return None
    if command[:3] != _PROXY_PREFIX:
        report.add("error", f"`{name}` (ingress) no ejecuta exactamente el proxy de PEPPER",
                   f"el prefijo obligatorio es {_PROXY_PREFIX!r}")
        return None
    options: Dict[str, str] = {}
    tail = command[3:]
    if len(tail) % 2:
        report.add("error", f"`{name}` (ingress) tiene argumentos incompletos: {tail!r}")
        return None
    for index in range(0, len(tail), 2):
        option, value = tail[index:index + 2]
        if option not in _PROXY_OPTIONS:
            report.add("error", f"`{name}` (ingress) agrega una opción no permitida: {option}")
            return None
        if option in options:
            report.add("error", f"`{name}` (ingress) repite la opción {option}")
            return None
        options[option] = value
    if options.get("--listen") != "0.0.0.0:8080":
        report.add("error", f"`{name}` (ingress) debe escuchar exactamente en 0.0.0.0:8080")
    upstream = options.get("--upstream")
    if not upstream:
        report.add("error", f"`{name}` (ingress) no declara --upstream")
        return None
    host, separator, raw_port = upstream.rpartition(":")
    if not separator or not host or not raw_port.isdigit() or not 1 <= int(raw_port) <= 65535:
        report.add("error", f"`{name}` (ingress) declara un upstream inválido: {upstream!r}")
        return None
    if "--timeout" in options:
        try:
            timeout = float(options["--timeout"])
        except ValueError:
            timeout = 0
        if timeout <= 0:
            report.add("error", f"`{name}` (ingress) declara un timeout inválido: {options['--timeout']!r}")
    return host.lower(), int(raw_port)


def _dependency_names(service: Dict[str, Any]) -> List[str]:
    dependencies = service.get("depends_on") or {}
    if isinstance(dependencies, dict):
        return [str(name) for name in dependencies]
    if isinstance(dependencies, list):
        return [str(name) for name in dependencies]
    return []


def _check_upstream(name: str, service: Dict[str, Any], upstream: Optional[Tuple[str, int]],
                    services: Dict[str, Any], internal: Dict[str, bool], report: Report) -> None:
    """El upstream debe ser una dependencia real compartida por una red interna."""
    if upstream is None:
        return
    dependencies = _dependency_names(service)
    if not dependencies:
        report.add("error", f"`{name}` (ingress) no declara depends_on",
                   "sin una dependencia explícita no se puede demostrar a qué aplicación debe reenviar")
        return
    ingress_networks = {network for network in _service_networks(service) if internal.get(network)}
    allowed: Dict[str, str] = {}
    for dependency in dependencies:
        target = services.get(dependency) or {}
        mode = str(target.get("network_mode") or "")
        if mode.startswith("service:") and mode[len("service:"):] in services:
            # la dependencia vive en la pila de red de otro servicio: sus direcciones son las de ese
            target = services.get(mode[len("service:"):]) or {}
        target_networks = _service_networks(target)
        shared = ingress_networks & {network for network in target_networks if internal.get(network)}
        if not shared:
            continue
        allowed[dependency.lower()] = dependency
        container_name = str(target.get("container_name") or "").strip().lower()
        if container_name:
            allowed[container_name] = dependency
        for network in shared:
            spec = target_networks.get(network) or {}
            address = str(spec.get("ipv4_address") or "").strip().lower()
            if address:
                allowed[address] = dependency
            for alias in spec.get("aliases") or []:
                allowed[str(alias).lower()] = dependency
    host, port = upstream
    if host not in allowed:
        targets = ", ".join(sorted(allowed)) or "ninguno"
        report.add("error", f"`{name}` (ingress) apunta a {host}:{port}, que no es una dependencia interna",
                   f"destinos demostrables: {targets}; un upstream arbitrario puede ser producción")
    else:
        report.add("ok", f"el upstream {host}:{port} corresponde a `{allowed[host]}` en una red interna")


def _named_volume_bind(spec: Any) -> Optional[str]:
    """Un volumen nombrado del compose respaldado por una ruta del host (`driver_opts: type: none,
    o: bind, device: /ruta`) ES un bind mount con otro nombre. → la ruta, o None si es un volumen normal."""
    if not isinstance(spec, dict):
        return None
    opts = spec.get("driver_opts") or {}
    if not isinstance(opts, dict):
        return None
    options = str(opts.get("o") or "")
    device = str(opts.get("device") or "")
    if "bind" in options.split(",") or str(opts.get("type") or "") == "none" or device.startswith("/"):
        return device or "(ruta no declarada)"
    return None


def _resolve_host_path(source: str, compose_dir: Optional[Path]) -> Optional[Path]:
    """La ruta real del host detrás de un bind; None si no se puede resolver (relativa sin
    compose_dir, `~`, error del sistema de archivos)."""
    if not source or source.startswith("~"):
        return None
    path = Path(source)
    if not path.is_absolute():
        if compose_dir is None:
            return None
        path = compose_dir / path
    try:
        return path.resolve()
    except OSError:
        return None


def _first_socket(directory: Path, budget: int = 5000) -> Optional[str]:
    """La primera ruta que sea socket dentro del directorio; "" si no hay ninguno; None si el
    directorio es demasiado grande para recorrerlo entero (no comprobable)."""
    seen = 0
    for root, dirs, files in os.walk(directory):
        for entry in dirs + files:
            seen += 1
            if seen > budget:
                return None
            candidate = Path(root) / entry
            try:
                if candidate.is_socket():
                    return str(candidate)
            except OSError:
                continue
    return ""


def _check_host_mount(name: str, label: str, source: str, readonly: bool, is_ingress: bool,
                      compose_dir: Optional[Path], report: Report) -> None:
    """Un montaje respaldado por el host: bind directo o volumen nombrado con `driver_opts` de bind.

    Se admiten archivos concretos y, de directorios, solo los que PEPPER mismo escribió junto al
    compose (`./stub`, `./proxy`) y no contienen sockets. `/var/run:/x:ro` daba verde: `docker.sock`
    no aparecía en la ruta declarada pero el directorio lo contiene, y `:ro` no vuelve de solo
    consulta la API del daemon (revisión 2026-09-22, P1). Lo que no existe en esta máquina no se
    puede comprobar y no da verde."""
    if _DOCKER_SOCKET in source:
        report.add("error", f"`{name}` monta el socket de Docker ({label})",
                   "con el socket, el contenedor controla Docker: puede crear un contenedor CON salida")
        return
    if not readonly and not is_ingress:
        report.add("error", f"`{name}` monta `{source}` del host con escritura ({label})",
                   "los montajes del host van :ro; con escritura, el legado escribe fuera del entorno desechable")
    resolved = _resolve_host_path(source, compose_dir)
    if resolved is None:
        report.add("unknown", f"`{name}` monta `{source}` del host ({label}) y no pude resolver la ruta",
                   "sin la ruta real no se sabe qué hay detrás")
        return
    if not resolved.exists():
        report.add("unknown", f"`{name}` monta `{source}` del host ({label}), que no existe en esta máquina",
                   "Docker crearía un directorio ahí; no se puede comprobar qué expone")
        return
    if resolved.is_socket():
        report.add("error", f"`{name}` monta el socket `{resolved}` del host ({label})",
                   "un socket es una API del host, no un archivo: :ro no la vuelve de solo consulta")
        return
    if resolved.is_dir():
        inside = compose_dir is not None and resolved.is_relative_to(compose_dir.resolve())
        if not inside:
            declared = f"`{source}` → " if str(resolved) != source else ""
            report.add("error", f"`{name}` monta el directorio {declared}`{resolved}` del host ({label})",
                       "un directorio expone todo lo que contenga —sockets de control incluidos, con otro prefijo—; "
                       "se montan archivos concretos, o directorios escritos por PEPPER junto al compose")
            return
        socket_path = _first_socket(resolved)
        if socket_path is None:
            report.add("unknown", f"`{name}` monta el directorio `{resolved}` ({label}) y es demasiado grande para recorrerlo",
                       "no se pudo comprobar que no contenga sockets")
        elif socket_path:
            report.add("error", f"`{name}` monta `{resolved}` ({label}), que contiene el socket `{socket_path}`",
                       "un socket dentro de un directorio montado es una API del host dentro del contenedor")


def _check_service_hardening(name: str, service: Dict[str, Any], is_ingress: bool, report: Report,
                             volumes: Optional[Dict[str, Any]] = None,
                             compose_dir: Optional[Path] = None) -> None:
    """Capacidades y montajes que reabren la salida aunque la red sea interna.

    Un volumen nombrado con `driver_opts` de bind pasaba como volumen normal: el legado escribía
    en `/tmp` del host con verde (auditoría 2026-09-21, P1-01). Se resuelven los volúmenes de
    nivel superior; los `external` no se pueden comprobar y no dan verde. Todo lo respaldado por
    el host, con o sin escritura, pasa por `_check_host_mount`."""
    if service.get("privileged"):
        report.add("error", f"`{name}` corre privileged",
                   "un contenedor privilegiado puede reconfigurar la red del host: no hay aislamiento posible")
    for namespace in _FORBIDDEN_NAMESPACES:
        if str(service.get(f"{namespace}_mode") or service.get(namespace) or "") == "host":
            report.add("error", f"`{name}` comparte el namespace {namespace} del host")
    if service.get("cap_add"):
        report.add("error", f"`{name}` agrega capacidades: {service['cap_add']}",
                   "CAP_NET_ADMIN/SYS_ADMIN permiten saltarse la red interna")
    if service.get("devices"):
        report.add("error", f"`{name}` monta dispositivos del host: {service['devices']}")
    for volume in (service.get("volumes") or []):
        source, target, readonly = _volume_parts(volume)
        if _DOCKER_SOCKET in target:
            report.add("error", f"`{name}` monta el socket de Docker",
                       "con el socket, el contenedor controla Docker: puede crear un contenedor CON salida")
            continue
        if not source:
            continue   # volumen anónimo: nace y muere con el entorno
        if _is_bind(source):
            _check_host_mount(name, "bind", source, readonly, is_ingress, compose_dir, report)
            continue
        if volumes is None or source not in volumes:
            report.add("unknown", f"`{name}` monta el volumen `{source}`, que el compose no declara",
                       "no se puede comprobar qué hay detrás")
            continue
        spec = volumes.get(source) or {}
        if isinstance(spec, dict) and spec.get("external"):
            report.add("unknown", f"`{name}` monta el volumen preexistente `{source}` (external)",
                       "no se puede comprobar qué hay detrás; con --live se inspecciona el volumen")
            continue
        device = _named_volume_bind(spec)
        if device is None:
            continue   # volumen normal de Docker: desechable
        if not device.startswith("/"):
            report.add("error", f"`{name}` monta `{source}`, un volumen nombrado con driver_opts de bind sin ruta absoluta",
                       "un bind sin ruta comprobable no cuenta como desechable")
            continue
        _check_host_mount(name, f"volumen nombrado `{source}` respaldado por `{device}`", device, readonly,
                          is_ingress, compose_dir, report)


def _check_ingress(name: str, service: Dict[str, Any], compose_dir: Optional[Path],
                   report: Report) -> Optional[Tuple[str, int]]:
    """El ingress no es un nombre: es el proxy de PEPPER, verificado (C-01).

    Llamar `ingress` a un `alpine sh -c exfiltrar` pasaba como aislado. Ahora:
    imagen python, comando que ejecuta el proxy, exactamente un montaje `:ro`
    cuyo SHA-256 coincide con el `pepper/proxy.py` de esta instalación.
    """
    image = str(service.get("image") or "")
    if not _PYTHON_IMAGE.fullmatch(image):
        report.add("error", f"`{name}` (ingress) usa una imagen no permitida: `{image or '?'}`",
                   "se admite únicamente la imagen oficial python:<versión>-alpine, opcionalmente fijada por digest")
    if service.get("build"):
        report.add("error", f"`{name}` (ingress) declara build",
                   "el único contenedor de entrada no puede ejecutar una imagen construida por el legacy")
    entrypoint = service.get("entrypoint")
    if entrypoint not in (None, "", []):
        report.add("error", f"`{name}` (ingress) sobrescribe entrypoint: {entrypoint!r}",
                   "un entrypoint se ejecuta antes que command y puede sustituir por completo al proxy")
    upstream = _parse_proxy_command(name, service.get("command"), report)
    volumes = service.get("volumes") or []
    if len(volumes) != 1:
        report.add("error", f"`{name}` (ingress) tiene {len(volumes)} montajes; debe tener exactamente 1 (el proxy, :ro)")
        return upstream
    source, target, readonly = _volume_parts(volumes[0])
    if (not readonly or target != "/pepper-proxy.py"
            or source.rstrip("/").rsplit("/", 1)[-1] != "proxy.py"):
        report.add("error", f"`{name}` (ingress) monta `{source}` — el único montaje permitido es el proxy de PEPPER, :ro")
        return upstream
    expected = bundled_proxy_hash()
    if expected is None:
        report.add("unknown", "no encontré pepper/proxy.py en esta instalación para verificar el hash del proxy")
        return upstream
    if compose_dir is None:
        report.add("unknown", "no pude verificar el hash del proxy montado (sin la ruta del compose)")
        return upstream
    mounted = (compose_dir / source).resolve() if not Path(source).is_absolute() else Path(source)
    if not mounted.is_file():
        report.add("error", f"el proxy montado no existe: {mounted}")
        return upstream
    from pepper import manifest as evidence_manifest
    actual = evidence_manifest.sha256_file(mounted)
    if actual != expected:
        report.add("error", "el proxy montado en el ingress NO es el de PEPPER (hash distinto)",
                   f"{mounted} difiere de pepper/proxy.py: un binario ajeno en el único contenedor con salida")
    else:
        report.add("ok", "el ingress monta exactamente el proxy de PEPPER (hash verificado, :ro)")
    return upstream


def _check_dns(name: str, dns: Any, subnets: Iterable[ipaddress.IPv4Network], report: Report) -> None:
    """`internal: true` bloquea los paquetes, no las preguntas.

    El resolver embebido de Docker contesta los alias de la red y REENVÍA todo lo demás
    al resolver configurado — por defecto el de la máquina; con VPN, el institucional.
    Un host del artefacto que no esté en los alias deja ahí una consulta con su nombre
    aunque ningún paquete de datos salga. Por eso cada servicio fija `dns:` a una IP
    sin nada detrás dentro de la subred interna: el sumidero. Sin eso no hay verde.
    """
    servers = [dns] if isinstance(dns, str) else list(dns or [])
    if not servers:
        report.add("unknown", f"`{name}` no fija `dns:`",
                   "los nombres que no sean alias se reenvían al resolver del host (con VPN, al institucional); "
                   "declara dns: [<IP libre dentro de la subred interna>]")
        return
    outside = [str(s) for s in servers if not _is_internal_ip(str(s), subnets)]
    if outside:
        report.add("error", f"`{name}` fija un DNS fuera de las redes internas: {', '.join(outside)}",
                   "un resolver externo es una salida: cada nombre consultado viaja hasta él")
        return
    report.add("ok", f"`{name}` resuelve solo dentro de la red interna (sumidero DNS {', '.join(map(str, servers))})")


def _check_publication_network(name: str, service: Dict[str, Any], services: Dict[str, Any],
                               compose: Dict[str, Any], internal: Dict[str, bool], report: Report) -> None:
    """La única red con salida es la de publicación del ingress, y solo él la usa.

    Docker no publica puertos de un contenedor que solo está en redes `internal`
    (el puerto no responde desde el host), así que el ingress necesita una red más.
    Su egress queda acotado por la identidad verificada del proxy (_check_ingress).
    """
    external = [net for net in _service_networks(service) if internal.get(net) is False]
    if not external:
        report.add("warn", f"`{name}` (ingress) no tiene red de publicación",
                   "Docker no publica puertos desde una red internal: el host no podrá entrar; agrega una red `edge` solo para el ingress")
        return
    if len(external) > 1:
        report.add("error", f"`{name}` (ingress) está en {len(external)} redes con salida: {', '.join(sorted(external))}",
                   "una sola red de publicación; cada red extra es superficie de salida sin necesidad")
        return
    net = external[0]
    if ((compose.get("networks") or {}).get(net) or {}).get("external"):
        report.add("unknown", f"la red de publicación `{net}` es preexistente (external): no puedo comprobar quién más la usa",
                   "declárala en el compose; con --live se verifica quién está conectado según Docker")
    others = sorted(other for other, spec in services.items()
                    if other != name and net in _service_networks(spec or {}))
    if others:
        report.add("error", f"la red de publicación `{net}` también la usan: {', '.join(others)}",
                   "solo el ingress verificado puede tener salida; cualquier otro servicio ahí alcanza producción")
    else:
        report.add("ok", f"`{name}` (ingress) publica por la red `{net}`, que ningún otro servicio usa")


def check_static(compose: Dict[str, Any], external_hosts: Optional[List[str]] = None,
                 ingress: str = DEFAULT_INGRESS, resolved: bool = True,
                 compose_dir: Optional[Path] = None) -> Report:
    """Verifica los invariantes de aislamiento sobre el compose resuelto."""
    report = Report()
    services: Dict[str, Any] = compose.get("services") or {}
    internal = _internal_networks(compose)
    subnets = _internal_subnets(compose)

    if not resolved:
        report.add("unknown", "el compose NO está resuelto por `docker compose config`",
                   "un ${VAR} en el YAML puede quitar `internal: true` sin que se vea; sin resolución no hay verde")
    elif "${" in json.dumps(compose):
        report.add("unknown", "el compose conserva variables sin sustituir (${…})")

    if not services:
        report.add("error", "el compose no declara servicios")
        return report
    if ingress not in services:
        report.add("error", f"el compose no declara el ingress verificado `{ingress}`")

    declared_internal = [n for n, is_int in internal.items() if is_int]
    if not declared_internal:
        report.add("error", "ninguna red es `internal: true`",
                   "todo contenedor tendría salida a internet y a la VPN de la máquina")
    else:
        report.add("ok", f"redes internas declaradas: {', '.join(sorted(declared_internal))}")

    aliases: Dict[str, str] = {}
    for name, service in services.items():
        service = service or {}
        is_ingress = name == ingress
        _check_service_hardening(name, service, is_ingress, report, volumes=compose.get("volumes") or {},
                                 compose_dir=compose_dir)

        mode = str(service.get("network_mode") or "")
        if mode:
            # `service:<otro>` comparte la pila de red de OTRO servicio del compose (así un
            # datasource a localhost llega a la base): hereda sus redes y su DNS, que se
            # verifican en ese servicio. Cualquier otro modo (host, container:<ajeno>) es fuga.
            peer = mode[len("service:"):] if mode.startswith("service:") else ""
            if peer and peer in services and peer != ingress and not is_ingress:
                if service.get("networks") or service.get("dns") or service.get("ports"):
                    report.add("error", f"`{name}` comparte la pila de red de `{peer}` y además declara redes, dns o puertos propios",
                               "Docker lo rechaza o lo ignora: la configuración efectiva es la del otro servicio")
                else:
                    report.add("ok", f"`{name}` comparte la pila de red del servicio `{peer}` (sus redes y su DNS se verifican en `{peer}`)")
                continue
            report.add("error", f"`{name}` usa network_mode: {mode}",
                       "comparte la pila de red del host o de un contenedor ajeno: alcanza todo lo que ese alcance")
            continue

        for net, spec in _service_networks(service).items():
            if internal.get(net) is None:
                report.add("error", f"`{name}` se conecta a la red `{net}`, que el compose no declara",
                           "una red no declarada usa el bridge por defecto: con salida")
            elif not internal[net] and not is_ingress:
                report.add("error", f"`{name}` se conecta a la red `{net}`, que NO es internal",
                           "solo el ingress verificado toca una red con salida, y solo para publicar su puerto")
            for alias in (spec.get("aliases") or []):
                aliases[str(alias).lower()] = name
        if is_ingress:
            _check_publication_network(name, service, services, compose, internal, report)

        for host, ip in _extra_hosts(service).items():
            if not _is_internal_ip(ip, subnets):
                report.add("error", f"`{name}` mapea el host `{host}` a {ip}, fuera de las redes internas",
                           "un extra_hosts a una IP externa reabre el camino a producción")

        _check_dns(name, service.get("dns"), subnets, report)

        published = [p for p in (service.get("ports") or [])]
        if published and not is_ingress:
            report.add("warn", f"`{name}` publica puertos al host: {_ports_text(published)}",
                       "expone datos del legacy al host y a su LAN; publica solo por el ingress o bindea a 127.0.0.1")
        if published and is_ingress:
            for port in published:
                host_ip = str(port.get("host_ip", "")) if isinstance(port, dict) else str(port).split(":")[0]
                if host_ip not in ("127.0.0.1", "::1", "localhost"):
                    report.add("warn", f"el ingress publica en todas las interfaces ({_ports_text([port])})",
                               'cualquier equipo de tu LAN alcanza el legacy: usa "127.0.0.1:<puerto>:8080"')

        if is_ingress:
            upstream = _check_ingress(name, service, compose_dir, report)
            _check_upstream(name, service, upstream, services, internal, report)

    for host in (external_hosts or []):
        key = host.strip().lower()
        if not key:
            continue
        if key in aliases:
            report.add("ok", f"el host externo `{host}` resuelve al stub (`{aliases[key]}`)")
        else:
            report.add("error", f"el host externo `{host}` no está declarado como alias de ningún servicio",
                       "sin alias, el contenedor lo resuelve por DNS real: si hay salida, va a producción")

    return report


def _ports_text(ports: List[Any]) -> str:
    parts = []
    for port in ports:
        if isinstance(port, dict):
            parts.append(f"{port.get('published', '?')}→{port.get('target', '?')}")
        else:
            parts.append(str(port))
    return ", ".join(parts)


def _inspect_json(name: str) -> Optional[Dict[str, Any]]:
    out = subprocess.run(["docker", "inspect", name], capture_output=True, text=True)
    if out.returncode != 0:
        return None
    try:
        data = json.loads(out.stdout)
        return data[0] if isinstance(data, list) and data else None
    except ValueError:
        return None


def _live_volume_bind(name: str) -> Optional[str]:
    """`docker volume inspect`: "" si es un volumen normal, la ruta si está respaldado por el host,
    None si no se pudo inspeccionar."""
    if not name:
        return None
    out = subprocess.run(["docker", "volume", "inspect", name, "--format", "{{json .Options}}"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        return None
    try:
        options = json.loads(out.stdout.strip() or "null") or {}
    except ValueError:
        return None
    device = _named_volume_bind({"driver_opts": options})
    return device or ""


def _check_live_mounts(service: str, mounts: List[Dict[str, Any]], is_ingress: bool,
                       compose_dir: Optional[Path], report: Report) -> None:
    """Los `Mounts` de `docker inspect`, uno por uno. Cada montaje respaldado por el host se
    resuelve con o sin escritura: un volumen nombrado sobre `/var/run` en :ro solo se inspeccionaba
    si era RW y daba verde (revisión 2026-09-22, P1)."""
    for mount in mounts:
        source = str(mount.get("Source", ""))
        if _DOCKER_SOCKET in source or _DOCKER_SOCKET in str(mount.get("Destination", "")):
            report.add("error", f"`{service}` tiene montado el socket de Docker",
                       "el contenedor controla Docker: puede crear otro contenedor con salida")
            continue
        if is_ingress:
            continue   # sus montajes se verifican aparte (exactamente el proxy, :ro)
        readonly = not bool(mount.get("RW", True))
        mount_type = str(mount.get("Type") or "")
        if mount_type == "bind":
            _check_host_mount(service, "bind, según Docker", source, readonly, False, compose_dir, report)
        elif mount_type == "volume":
            volume_name = str(mount.get("Name") or "")
            device = _live_volume_bind(volume_name)
            if device is None:
                report.add("error", f"`{service}`: no pude inspeccionar el volumen `{volume_name}`",
                           "un volumen que no se deja inspeccionar no cuenta como desechable")
            elif device:
                _check_host_mount(service, f"volumen `{volume_name}` respaldado por `{device}`, según Docker",
                                  device, readonly, False, compose_dir, report)


def _network_members(network: str) -> Optional[List[str]]:
    """Nombres de los contenedores conectados a una red, según Docker (None si no se deja inspeccionar)."""
    out = subprocess.run(["docker", "network", "inspect", network, "--format", "{{json .Containers}}"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        return None
    try:
        members = json.loads(out.stdout.strip() or "{}") or {}
    except ValueError:
        return None
    return sorted(str((entry or {}).get("Name") or key) for key, entry in members.items())


def _check_live_publication_network(service: str, container: str, external: List[str], report: Report) -> None:
    """Lo mismo que _check_publication_network, pero preguntándole a Docker quién está conectado."""
    if not external:
        report.add("warn", f"`{service}` (ingress) no tiene red de publicación en ejecución",
                   "Docker no publica puertos desde una red internal: el host no podrá entrar")
        return
    if len(external) > 1:
        report.add("error", f"`{service}` (ingress) está en {len(external)} redes con salida: {', '.join(external)}",
                   "una sola red de publicación")
        return
    network = external[0]
    members = _network_members(network)
    if members is None:
        report.add("error", f"`{service}`: no pude ver quién está conectado a la red de publicación `{network}`",
                   "una red que no se deja inspeccionar no cuenta como exclusiva")
        return
    others = [member for member in members if member != container]
    if others:
        report.add("error", f"la red de publicación `{network}` tiene otros contenedores: {', '.join(others)}",
                   "solo el ingress verificado puede tener salida")
    else:
        report.add("ok", f"`{service}` (ingress) publica por `{network}`, que ningún otro contenedor usa (según Docker)")


def _network_subnets(network: str) -> List[ipaddress.IPv4Network]:
    out = subprocess.run(["docker", "network", "inspect", network, "--format", "{{json .IPAM.Config}}"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        return []
    try:
        config = json.loads(out.stdout.strip() or "[]") or []
    except ValueError:
        return []
    subnets = []
    for entry in config:
        subnet = (entry or {}).get("Subnet")
        if subnet:
            try:
                subnets.append(ipaddress.ip_network(subnet, strict=False))
            except ValueError:
                continue
    return subnets


def _live_remote_peers(container: str) -> Optional[List[str]]:
    """Destinos remotos con los que el contenedor tiene conexiones abiertas, según su
    propia pila de red. El hash del proxy demuestra QUÉ código se montó; esto
    demuestra CON QUIÉN está hablando de verdad."""
    out = subprocess.run(["docker", "exec", container, "sh", "-c",
                          "cat /proc/net/tcp /proc/net/tcp6 2>/dev/null"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        return None
    rows = [line.split() for line in out.stdout.splitlines() if ":" in line]
    rows = [r for r in rows if len(r) >= 4 and ":" in r[1] and ":" in r[2]]
    # Los puertos en LISTEN son los que el contenedor OFRECE. Una conexión cuyo puerto
    # local es uno de ellos es ENTRANTE (el navegador del humano llegando al puerto
    # publicado), no una salida: contarla como fuga hacía que isolate dijera
    # NO AISLADO justo mientras alguien usaba el sistema.
    listening = {r[1].rsplit(":", 1)[1] for r in rows if r[3] == "0A"}
    peers = set()
    for parts in rows:
        if parts[3] == "0A":
            continue
        if parts[1].rsplit(":", 1)[1] in listening:
            continue  # entrante
        hexip = parts[2].split(":")[0]
        if len(hexip) == 8:           # IPv4, little-endian
            try:
                ip = ipaddress.ip_address(int.from_bytes(bytes.fromhex(hexip), "little"))
            except ValueError:
                continue
            if ip.is_unspecified or ip.is_loopback:
                continue
            peers.add(str(ip))
    return sorted(peers)


def _check_live_ingress_peers(service: str, container: str, upstream: Optional[Tuple[str, int]],
                              subnets: List[ipaddress.IPv4Network], report: Report) -> None:
    """El único contenedor con salida no puede estar hablando con nada fuera del entorno."""
    peers = _live_remote_peers(container)
    if peers is None:
        report.add("unknown", f"`{service}` (ingress): no pude leer con quién tiene conexiones abiertas",
                   "sin eso no se demuestra que el único contenedor con salida solo habla con el app")
        return
    permitido = {upstream[0]} if upstream else set()
    fuera = []
    for peer in peers:
        if peer in permitido:
            continue
        try:
            address = ipaddress.ip_address(peer)
        except ValueError:
            fuera.append(peer)
            continue
        if any(address in subnet for subnet in subnets):
            continue
        fuera.append(peer)
    if fuera:
        report.add("error", f"`{service}` (ingress) tiene conexiones abiertas fuera del entorno: {', '.join(fuera)}",
                   "el único contenedor con salida debe hablar únicamente con el app interno")
    else:
        report.add("ok", f"`{service}` (ingress) solo tiene conexiones al entorno interno"
                          + (f" ({', '.join(peers)})" if peers else " (ninguna abierta)"))


def _published_loopback_url(info: Dict[str, Any]) -> Optional[str]:
    ports = ((info.get("NetworkSettings") or {}).get("Ports") or {})
    for bindings in ports.values():
        for binding in bindings or []:
            host_port = str(binding.get("HostPort") or "")
            if host_port:
                return f"http://127.0.0.1:{host_port}/"
    return None


def browser_policy_ok(policy: str) -> bool:
    """La política que el ingress debe imponer: todo destino 'self' y reporte de bloqueos."""
    return "default-src 'self'" in policy and "report-uri /__pepper/csp-report" in policy


def _check_live_browser_policy(service: str, info: Dict[str, Any], report: Report) -> None:
    """El navegador del humano es parte del perímetro y ningún contenedor lo ve.

    Se pide la raíz por loopback —solo 127.0.0.1, jamás otro destino— y se exige
    la Content-Security-Policy de PEPPER en la respuesta: sin ella el navegador
    cargaría iframes, scripts e imágenes de los hosts del artefacto (con VPN, de
    producción) por fuera de los contenedores."""
    url = _published_loopback_url(info)
    if url is None:
        report.add("unknown", f"`{service}` (ingress) no publica ningún puerto",
                   "sin puerto publicado no se puede comprobar la política del navegador")
        return
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=8) as response:
            policy = response.headers.get("Content-Security-Policy") or ""
    except urllib.error.HTTPError as error:
        policy = error.headers.get("Content-Security-Policy") or ""
    except (urllib.error.URLError, OSError, ValueError) as error:
        report.add("unknown", f"`{service}` (ingress): no pude pedir {url} para comprobar la política del navegador",
                   str(error)[:120])
        return
    if browser_policy_ok(policy):
        report.add("ok", f"`{service}` (ingress) impone al navegador la política de PEPPER: solo 127.0.0.1, con reporte de bloqueos")
    else:
        report.add("error", f"`{service}` (ingress) NO impone la política del navegador en {url}",
                   "sin Content-Security-Policy el navegador del humano carga iframes, scripts e imágenes de los "
                   "hosts del artefacto: fuga por fuera de los contenedores")


def check_live(compose_path: Path, external_hosts: Optional[List[str]] = None,
               ingress: str = DEFAULT_INGRESS) -> Report:
    """Verifica el aislamiento sobre los contenedores en ejecución, según Docker.

    Fail-closed (C-01): sin contenedores no hay nada verificado; una red o un
    contenedor que no se pudo inspeccionar bloquea el verde.
    """
    report = Report()
    ps = subprocess.run(
        ["docker", "compose", "-f", str(compose_path), "ps", "--format", "json"],
        capture_output=True, text=True,
    )
    if ps.returncode != 0:
        report.add("error", "no pude listar los contenedores", ps.stderr.strip()[:200])
        return report

    containers = []
    for line in ps.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            containers.append(json.loads(line))
        elif line.startswith("["):
            containers.extend(json.loads(line))
    if not containers:
        # Sin contenedores no hay fuga que demostrar ni verde que dar: NO VERIFICADO
        # (bloquea igual, pero no dice "el entorno puede alcanzar producción" de un entorno apagado).
        report.add("unknown", "no hay contenedores en ejecución para este compose",
                   "no hay nada que verificar: levanta el entorno y repite — el verde vivo exige el entorno arriba")
        return report

    network_internal: Dict[str, Optional[bool]] = {}

    def is_internal(network: str) -> Optional[bool]:
        if network not in network_internal:
            out = subprocess.run(["docker", "network", "inspect", network, "--format", "{{.Internal}}"],
                                 capture_output=True, text=True)
            network_internal[network] = (out.stdout.strip() == "true") if out.returncode == 0 else None
        return network_internal[network]

    expected_proxy = bundled_proxy_hash()
    live_subnets: List[ipaddress.IPv4Network] = []
    compose_dir = compose_path.resolve().parent

    for container in containers:
        name = container.get("Name") or container.get("name") or "?"
        service = container.get("Service") or container.get("service") or name
        info = _inspect_json(name)
        if info is None:
            report.add("error", f"`{service}`: no pude inspeccionar el contenedor `{name}`",
                       "sin inspección no hay nada verificado")
            continue

        host_config = info.get("HostConfig") or {}
        if host_config.get("Privileged"):
            report.add("error", f"`{service}` corre privileged (según Docker)")
        if host_config.get("CapAdd"):
            report.add("error", f"`{service}` agrega capacidades en ejecución: {host_config['CapAdd']}")
        if host_config.get("Devices"):
            report.add("error", f"`{service}` monta dispositivos del host en ejecución")
        network_mode = str(host_config.get("NetworkMode") or "")
        if network_mode == "host":
            report.add("error", f"`{service}` comparte la red del host en ejecución")
        shared_peer: Optional[Dict[str, Any]] = None
        if network_mode.startswith("container:"):
            shared_peer = _inspect_json(network_mode[len("container:"):])
            if shared_peer is None:
                report.add("error", f"`{service}` comparte la pila de red de un contenedor que no pude inspeccionar",
                           "sin inspección no hay nada verificado")
                continue
        for key, label in (("PidMode", "pid"), ("IpcMode", "ipc"), ("UTSMode", "uts")):
            if str(host_config.get(key) or "") == "host":
                report.add("error", f"`{service}` comparte el namespace {label} del host en ejecución")
        _check_live_mounts(service, info.get("Mounts") or [], service == ingress, compose_dir, report)

        if shared_peer is not None:
            peer_name = str(shared_peer.get("Name") or "?").lstrip("/")
            peer_services = {c.get("Name") or c.get("name"): c.get("Service") or c.get("service") for c in containers}
            if peer_name not in peer_services:
                report.add("error", f"`{service}` comparte la pila de red de `{peer_name}`, que no es de este compose",
                           "un contenedor ajeno puede tener salida")
                continue
            networks = list(((shared_peer.get("NetworkSettings") or {}).get("Networks") or {}).keys())
            report.add("ok", f"`{service}` comparte la pila de red de `{peer_services[peer_name]}` (según Docker)")
        else:
            networks = list(((info.get("NetworkSettings") or {}).get("Networks") or {}).keys())
        if not networks:
            report.add("error", f"`{service}`: no pude leer sus redes",
                       "sin redes legibles no hay nada verificado")
            continue
        external_networks: List[str] = []
        container_subnets: List[ipaddress.IPv4Network] = []
        for network in networks:
            internal = is_internal(network)
            if internal is None:
                report.add("error", f"`{service}`: no pude inspeccionar la red `{network}`",
                           "una red que no se deja inspeccionar no cuenta como interna")
            elif internal:
                report.add("ok", f"`{service}` está en la red interna `{network}`")
                nets = _network_subnets(network)
                live_subnets.extend(nets)
                container_subnets.extend(nets)
            elif service != ingress:
                report.add("error", f"`{service}` está conectado a `{network}`, que NO es interna en Docker",
                           "solo el ingress verificado toca una red con salida")
            else:
                external_networks.append(network)
        # DNS según Docker, no según el YAML: lo que no sea alias debe morir dentro
        dns_owner = (shared_peer.get("HostConfig") or {}) if shared_peer is not None else host_config
        _check_dns(service, dns_owner.get("Dns"), container_subnets, report)
        if service == ingress:
            _check_live_publication_network(service, name, external_networks, report)

        if service == ingress:
            config = info.get("Config") or {}
            image = str(config.get("Image") or "")
            if not _PYTHON_IMAGE.fullmatch(image):
                report.add("error", f"`{service}` (ingress) ejecuta una imagen no permitida: `{image or '?'}`")
            entrypoint = config.get("Entrypoint")
            if entrypoint not in (None, "", []):
                report.add("error", f"`{service}` (ingress) ejecuta un entrypoint inesperado: {entrypoint!r}")
            live_upstream = _parse_proxy_command(service, config.get("Cmd"), report)
            _check_live_ingress_peers(service, name, live_upstream, live_subnets, report)
            _check_live_browser_policy(service, info, report)

            mounts = info.get("Mounts") or []
            proxy_mounts = [m for m in mounts if str(m.get("Destination", "")) == "/pepper-proxy.py"]
            if len(mounts) != 1:
                report.add("error", f"`{service}` (ingress) tiene {len(mounts)} montajes vivos; debe tener exactamente uno")
            if len(proxy_mounts) != 1 or expected_proxy is None:
                report.add("error", f"`{service}` (ingress) no monta el proxy de PEPPER (según Docker)")
            else:
                mount = proxy_mounts[0]
                source = Path(str(mount.get("Source", "")))
                if mount.get("RW", True):
                    report.add("error", "el proxy del ingress está montado con escritura; debe ser :ro")
                elif not source.is_file():
                    report.add("unknown", f"no pude leer el proxy montado desde el host: {source}")
                else:
                    from pepper import manifest as evidence_manifest
                    if evidence_manifest.sha256_file(source) != expected_proxy:
                        report.add("error", "el proxy que corre en el ingress NO es el de PEPPER (hash distinto)")
                    else:
                        report.add("ok", "el ingress vivo ejecuta el proxy de PEPPER (hash verificado, :ro)")

    for host in (external_hosts or []):
        for container in containers:
            name = container.get("Name") or container.get("name")
            service = container.get("Service") or container.get("service") or name
            if service == ingress:
                continue
            out = subprocess.run(["docker", "exec", name, "getent", "hosts", host],
                                 capture_output=True, text=True)
            resolved = out.stdout.split()[0] if out.stdout.split() else ""
            if not resolved:
                report.add("ok", f"`{service}`: `{host}` no resuelve (sin DNS externo)")
            else:
                private = resolved.startswith(("10.", "172.", "192.168."))
                report.add("ok" if private else "error",
                           f"`{service}`: `{host}` resuelve a {resolved}",
                           "" if private else "resuelve a una IP pública: el contenedor puede llamar al servicio real")
    return report


def render(report: Report, title: str) -> str:
    lines = [f"# {title}", ""]
    if report.verdict == "VERIFIED":
        lines.append("**AISLADO (verificado)** — ningún contenedor del legacy puede alcanzar nada fuera de su red.")
    elif report.verdict == "FAILED":
        lines.append(f"**NO AISLADO** — {len(report.errors)} fuga(s). El entorno puede alcanzar producción; no lo levantes ni observes hasta corregir.")
    else:
        lines.append(f"**NO VERIFICADO** — {len(report.unknowns)} comprobación(es) pendientes. Lo no verificado bloquea igual que una fuga (fail-closed).")
    lines.append("")
    for level, label in (("error", "Fugas"), ("unknown", "No verificado"), ("warn", "Avisos"), ("ok", "Verificado")):
        items = [f for f in report.findings if f.level == level]
        if not items:
            continue
        lines += [f"## {label}", ""]
        for finding in items:
            detail = f" — {finding.detail}" if finding.detail else ""
            lines.append(f"- {finding.check}{detail}")
        lines.append("")
    return "\n".join(lines)
