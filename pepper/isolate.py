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

Invariante: **ningún contenedor del legacy puede alcanzar nada fuera de su red.**
La única excepción es el ingress — y no basta con llamarse `ingress`: debe ser el
proxy de PEPPER (imagen python, comando del proxy, un único montaje `:ro` cuyo
hash coincide con `pepper/proxy.py`).
"""

from __future__ import annotations

import ipaddress
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

DEFAULT_INGRESS = "ingress"

_FORBIDDEN_NAMESPACES = ("pid", "ipc", "uts")
_DOCKER_SOCKET = "docker.sock"


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
    result = subprocess.run(
        ["docker", "compose", "-f", str(path), "config", "--format", "json"],
        capture_output=True, text=True,
    )
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


def _check_service_hardening(name: str, service: Dict[str, Any], is_ingress: bool, report: Report) -> None:
    """Capacidades y montajes que reabren la salida aunque la red sea interna."""
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
        if _DOCKER_SOCKET in source or _DOCKER_SOCKET in target:
            report.add("error", f"`{name}` monta el socket de Docker",
                       "con el socket, el contenedor controla Docker: puede crear un contenedor CON salida")
        elif _is_bind(source) and not readonly and not is_ingress:
            report.add("error", f"`{name}` monta `{source}` del host con escritura",
                       "los montajes del host van :ro; con escritura, el legado escribe fuera del entorno desechable")


def _check_ingress(name: str, service: Dict[str, Any], compose_dir: Optional[Path], report: Report) -> None:
    """El ingress no es un nombre: es el proxy de PEPPER, verificado (C-01).

    Llamar `ingress` a un `alpine sh -c exfiltrar` pasaba como aislado. Ahora:
    imagen python, comando que ejecuta el proxy, exactamente un montaje `:ro`
    cuyo SHA-256 coincide con el `pepper/proxy.py` de esta instalación.
    """
    image = str(service.get("image") or "")
    if not image.startswith("python:"):
        report.add("error", f"`{name}` (ingress) usa la imagen `{image or '?'}`, no python",
                   "el ingress ES el proxy de PEPPER (python + pepper/proxy.py); cualquier otra cosa no es un reenviador verificado")
    command = service.get("command") or []
    command_text = " ".join(command) if isinstance(command, list) else str(command)
    if "proxy.py" not in command_text or "--upstream" not in command_text:
        report.add("error", f"`{name}` (ingress) no ejecuta el proxy de PEPPER: {command_text[:120] or '(sin comando)'}",
                   "se exige `python3 … proxy.py --listen … --upstream …`")
    volumes = service.get("volumes") or []
    if len(volumes) != 1:
        report.add("error", f"`{name}` (ingress) tiene {len(volumes)} montajes; debe tener exactamente 1 (el proxy, :ro)")
        return
    source, target, readonly = _volume_parts(volumes[0])
    if not readonly or source.rstrip("/").rsplit("/", 1)[-1] not in ("proxy.py",):
        report.add("error", f"`{name}` (ingress) monta `{source}` — el único montaje permitido es el proxy de PEPPER, :ro")
        return
    expected = bundled_proxy_hash()
    if expected is None:
        report.add("unknown", "no encontré pepper/proxy.py en esta instalación para verificar el hash del proxy")
        return
    if compose_dir is None:
        report.add("unknown", "no pude verificar el hash del proxy montado (sin la ruta del compose)")
        return
    mounted = (compose_dir / source).resolve() if not Path(source).is_absolute() else Path(source)
    if not mounted.is_file():
        report.add("error", f"el proxy montado no existe: {mounted}")
        return
    from pepper import manifest as evidence_manifest
    actual = evidence_manifest.sha256_file(mounted)
    if actual != expected:
        report.add("error", "el proxy montado en el ingress NO es el de PEPPER (hash distinto)",
                   f"{mounted} difiere de pepper/proxy.py: un binario ajeno en el único contenedor con salida")
    else:
        report.add("ok", "el ingress monta exactamente el proxy de PEPPER (hash verificado, :ro)")


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
        _check_service_hardening(name, service, is_ingress, report)

        if service.get("network_mode"):
            report.add("error", f"`{name}` usa network_mode: {service['network_mode']}",
                       "comparte la pila de red del host: alcanza todo lo que la máquina alcanza")
            continue

        for net, spec in _service_networks(service).items():
            if internal.get(net) is None:
                report.add("error", f"`{name}` se conecta a la red `{net}`, que el compose no declara",
                           "una red no declarada usa el bridge por defecto: con salida")
            elif not internal[net]:
                if is_ingress:
                    report.add("ok", f"`{name}` (ingress) es el único con salida, por la red `{net}`")
                else:
                    report.add("error", f"`{name}` se conecta a la red `{net}`, que NO es internal",
                               "este contenedor puede alcanzar internet y la VPN de la máquina")
            for alias in (spec.get("aliases") or []):
                aliases[str(alias).lower()] = name

        for host, ip in _extra_hosts(service).items():
            if not _is_internal_ip(ip, subnets):
                report.add("error", f"`{name}` mapea el host `{host}` a {ip}, fuera de las redes internas",
                           "un extra_hosts a una IP externa reabre el camino a producción")

        if service.get("dns"):
            report.add("warn", f"`{name}` declara servidores DNS propios: {service['dns']}",
                       "en una red interna no responden; si responden, hay salida")

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
            _check_ingress(name, service, compose_dir, report)

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
        report.add("error", "no hay contenedores en ejecución para este compose",
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
        for mount in info.get("Mounts") or []:
            source = str(mount.get("Source", ""))
            if _DOCKER_SOCKET in source or _DOCKER_SOCKET in str(mount.get("Destination", "")):
                report.add("error", f"`{service}` tiene montado el socket de Docker",
                           "el contenedor controla Docker: puede crear otro contenedor con salida")

        networks = list(((info.get("NetworkSettings") or {}).get("Networks") or {}).keys())
        if not networks:
            report.add("error", f"`{service}`: no pude leer sus redes",
                       "sin redes legibles no hay nada verificado")
            continue
        for network in networks:
            internal = is_internal(network)
            if internal is None:
                report.add("error", f"`{service}`: no pude inspeccionar la red `{network}`",
                           "una red que no se deja inspeccionar no cuenta como interna")
            elif internal:
                report.add("ok", f"`{service}` está en la red interna `{network}`")
            elif service == ingress:
                report.add("ok", f"`{service}` (ingress) usa `{network}` para publicar el puerto")
            else:
                report.add("error", f"`{service}` está conectado a `{network}`, que NO es interna en Docker",
                           "este contenedor tiene salida ahora mismo: bájalo antes de observar nada")

        if service == ingress:
            proxy_mounts = [m for m in (info.get("Mounts") or [])
                            if str(m.get("Destination", "")).endswith("proxy.py")]
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
