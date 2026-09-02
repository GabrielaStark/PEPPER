"""Colector genérico de contenedores: copia la ventana observada, igual cada vez.

Lo mecánico de Observe (Principio 3): dado el compose del entorno y la ventana
del flujo, pide a Docker el tramo de logs de **cada** contenedor —con el margen
declarado— y lo deja en `evidence/<session_id>/` con el layout que Correlate
espera:

    evidence/<session_id>/
    ├── http.jsonl                stdout del ingress: el proxy de PEPPER
    └── containers/
        ├── <servicio>.log        stdout del contenedor
        └── <servicio>.err.log    stderr (ahí vive p. ej. el log de PostgreSQL
                                  cuando log_destination=stderr)

No interpreta ni filtra nada: archivos vacíos no se escriben, y lo que no se
pudo capturar se reporta como saltado con su razón. El `session.json` y las
fuentes del perfil (archivos dentro de contenedores) siguen siendo del agente
observador: esto captura lo que Docker ve, que es igual para cualquier stack.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_MARGIN_S = 30
DEFAULT_INGRESS = "ingress"


def container_name(project: str, service: str, spec: Dict[str, Any]) -> str:
    return spec.get("container_name") or f"{project}-{service}-1"


def _require_aware(moment: datetime, label: str) -> datetime:
    if moment.tzinfo is None:
        raise ValueError(f"{label} no trae zona horaria; sin ella las fuentes no se alinean")
    return moment


def collect(compose: Dict[str, Any], session_id: str, start: datetime, end: datetime,
            out_root: Path, project: Optional[str] = None, margin_s: int = DEFAULT_MARGIN_S,
            ingress: str = DEFAULT_INGRESS, docker_bin: str = "docker") -> List[Dict[str, Any]]:
    """Captura el tramo [start-margen, end+margen] de cada servicio del compose."""
    _require_aware(start, "observed_start")
    _require_aware(end, "observed_end")
    if end < start:
        raise ValueError("la ventana termina antes de empezar")

    services: Dict[str, Any] = compose.get("services") or {}
    if not services:
        raise ValueError("el compose no declara servicios")
    project = project or str(compose.get("name") or "").strip() or "legacy"

    session_dir = out_root / session_id
    containers_dir = session_dir / "containers"
    if (session_dir / "http.jsonl").exists() or (containers_dir.exists() and any(containers_dir.iterdir())):
        raise FileExistsError(
            f"evidence/{session_id} ya tiene capturas: no sobrescribas una sesión, usa otro session_id"
        )

    since = (start - timedelta(seconds=margin_s)).isoformat()
    until_moment = end + timedelta(seconds=margin_s)
    until = until_moment.isoformat()

    summary: List[Dict[str, Any]] = []
    now = datetime.now(start.tzinfo)
    if now < until_moment:
        # docker logs solo devuelve lo ya emitido: correr antes de end+margen deja
        # la captura incompleta sin que nadie lo note (pasó en la primera corrida real).
        summary.append({"warning": f"la ventana + margen termina en {until} y aún no llega: "
                                   "la captura queda incompleta; vuelve a correr collect después de esa hora"})
    for service, spec in services.items():
        spec = spec or {}
        if spec.get("profiles"):
            summary.append({"service": service, "skipped": "servicio bajo demanda (profiles); no corre en la ventana"})
            continue
        name = container_name(project, service, spec)
        # --timestamps para todo lo que no sea el ingress: muchos legacies loggean sin
        # fecha (WildFly: "18:36:30,213 ERROR …"); el prefijo RFC3339 de Docker da a
        # cada línea un timestamp completo en UTC, venga como venga el formato del app.
        # El ingress queda puro: su stdout ES http.jsonl y ya trae ts propio.
        command = [docker_bin, "logs", "--since", since, "--until", until]
        if service != ingress:
            command.insert(2, "--timestamps")
        result = subprocess.run(command + [name], capture_output=True, text=True)
        if result.returncode != 0:
            summary.append({"service": service, "skipped": f"docker logs falló: {result.stderr.strip()[:200]}"})
            continue

        if service == ingress:
            targets = [(session_dir / "http.jsonl", result.stdout),
                       (containers_dir / f"{service}.err.log", result.stderr)]
        else:
            targets = [(containers_dir / f"{service}.log", result.stdout),
                       (containers_dir / f"{service}.err.log", result.stderr)]
        for path, content in targets:
            if not content:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            summary.append({
                "service": service,
                "file": str(path.relative_to(out_root)),
                "lines": content.count("\n") + (0 if content.endswith("\n") else 1),
            })
        if not any(content for _, content in targets):
            summary.append({"service": service, "skipped": "sin líneas en la ventana"})
    return summary
