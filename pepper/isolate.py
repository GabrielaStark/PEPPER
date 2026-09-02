"""Verificación de aislamiento de un entorno rehidratado.

Un entorno rehidratado corre con la configuración del legacy: sus IPs, sus hosts,
sus credenciales de producción. Si tiene salida de red — y la máquina del ingeniero
suele tener VPN a la red institucional — el legacy **alcanza producción**: una vista
con `dblink`, un cliente de un bus, un job al arrancar. Basta una lectura.

Por eso el aislamiento no puede depender de que el agente se acuerde de escribirlo
en el compose (Principio 3: lo determinístico no se delega al agente). Este módulo
lo verifica sobre el compose **resuelto** — el que Docker va a ejecutar de verdad,
con sus variables ya sustituidas — y, con `--live`, sobre los contenedores.

Invariante: **ningún contenedor del legacy puede alcanzar nada fuera de su red.**
La única excepción es el ingress, un reenviador puro que publica el puerto de la
aplicación hacia el host y no monta artefactos ni datos.
"""

from __future__ import annotations

import ipaddress
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

DEFAULT_INGRESS = "ingress"


@dataclass
class Finding:
    level: str  # "error" | "warn" | "ok"
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
    def warnings(self) -> List[Finding]:
        return [f for f in self.findings if f.level == "warn"]

    @property
    def isolated(self) -> bool:
        return not self.errors


def resolve_compose(path: Path) -> Dict[str, Any]:
    """El compose **resuelto**: variables sustituidas, defaults aplicados.

    Se pide a Docker porque es lo que realmente va a ejecutar; un valor escondido
    en un .env puede quitar el aislamiento sin que se vea en el YAML.
    """
    result = subprocess.run(
        ["docker", "compose", "-f", str(path), "config", "--format", "json"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return json.loads(result.stdout)
    try:
        import yaml  # noqa: WPS433 — solo como respaldo si no hay Docker
    except ImportError:
        raise RuntimeError(
            f"no pude resolver {path} con `docker compose config` "
            f"({result.stderr.strip()[:200]}) y pyyaml no está instalado"
        )
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


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


def _volume_is_proxy_mount(volume: Any) -> bool:
    """True solo para el montaje legítimo del ingress: el proxy de PEPPER, de solo lectura.

    Cualquier otra cosa montada ahí es sospechosa aunque sea :ro — el ingress es el
    único contenedor con salida, y lo que vive dentro queda del lado expuesto.
    """
    if isinstance(volume, str):
        parts = volume.split(":")
        readonly = len(parts) >= 3 and "ro" in parts[-1].split(",")
        source = parts[0]
    elif isinstance(volume, dict):
        readonly = bool(volume.get("read_only"))
        source = str(volume.get("source", ""))
    else:
        return False
    name = source.rstrip("/").rsplit("/", 1)[-1]
    return readonly and name in ("proxy.py", "proxy")


def check_static(compose: Dict[str, Any], external_hosts: Optional[List[str]] = None,
                 ingress: str = DEFAULT_INGRESS) -> Report:
    """Verifica los invariantes de aislamiento sobre el compose resuelto."""
    report = Report()
    services: Dict[str, Any] = compose.get("services") or {}
    internal = _internal_networks(compose)
    subnets = _internal_subnets(compose)

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

        if is_ingress and service.get("volumes"):
            # El proxy de PEPPER se monta :ro en el ingress; eso es la herramienta,
            # no datos del legacy. Todo lo demás queda del lado con salida: aviso.
            foreign = [v for v in service["volumes"] if not _volume_is_proxy_mount(v)]
            if foreign:
                report.add("warn", "el ingress monta volúmenes ajenos al proxy de PEPPER",
                           "debe ser un reenviador puro: sin artefactos ni datos del legacy")
            else:
                report.add("ok", "el ingress solo monta el proxy de PEPPER (:ro)")

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


def check_live(compose_path: Path, external_hosts: Optional[List[str]] = None,
               ingress: str = DEFAULT_INGRESS) -> Report:
    """Verifica el aislamiento sobre los contenedores en ejecución, según Docker.

    No pregunta al compose sino a Docker: qué redes tiene cada contenedor y si esas
    redes son internas de verdad. Un contenedor levantado con otro compose, o
    reconectado a mano, se ve aquí y no en el archivo.
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
        report.add("warn", "no hay contenedores en ejecución para este compose")
        return report

    network_internal: Dict[str, bool] = {}

    def is_internal(network: str) -> bool:
        if network not in network_internal:
            out = subprocess.run(["docker", "network", "inspect", network, "--format", "{{.Internal}}"],
                                 capture_output=True, text=True)
            network_internal[network] = out.stdout.strip() == "true"
        return network_internal[network]

    for container in containers:
        name = container.get("Name") or container.get("name") or "?"
        service = container.get("Service") or container.get("service") or name
        out = subprocess.run(
            ["docker", "inspect", name, "--format", "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}"],
            capture_output=True, text=True,
        )
        networks = out.stdout.split()
        if not networks:
            report.add("warn", f"`{service}`: no pude leer sus redes", out.stderr.strip()[:120])
            continue
        for network in networks:
            if is_internal(network):
                report.add("ok", f"`{service}` está en la red interna `{network}`")
            elif service == ingress:
                report.add("ok", f"`{service}` (ingress) usa `{network}` para publicar el puerto")
            else:
                report.add("error", f"`{service}` está conectado a `{network}`, que NO es interna en Docker",
                           "este contenedor tiene salida ahora mismo: bájalo antes de observar nada")

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
                report.add("ok" if resolved.startswith("10.") or resolved.startswith("172.") or resolved.startswith("192.168.")
                           else "error",
                           f"`{service}`: `{host}` resuelve a {resolved}",
                           "" if resolved.startswith(("10.", "172.", "192.168."))
                           else "resuelve a una IP pública: el contenedor puede llamar al servicio real")
    return report


def render(report: Report, title: str) -> str:
    lines = [f"# {title}", ""]
    if report.isolated:
        lines.append("**AISLADO** — ningún contenedor del legacy puede alcanzar nada fuera de su red.")
    else:
        lines.append(f"**NO AISLADO** — {len(report.errors)} fuga(s). El entorno puede alcanzar producción; no lo levantes ni observes hasta corregir.")
    lines.append("")
    for level, label in (("error", "Fugas"), ("warn", "Avisos"), ("ok", "Verificado")):
        items = [f for f in report.findings if f.level == level]
        if not items:
            continue
        lines += [f"## {label}", ""]
        for finding in items:
            detail = f" — {finding.detail}" if finding.detail else ""
            lines.append(f"- {finding.check}{detail}")
        lines.append("")
    return "\n".join(lines)
