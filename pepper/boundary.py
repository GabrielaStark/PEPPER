"""La frontera de datos (D24) con alcance: qué autorizó una persona, para qué sistema y hacia dónde.

Antes `/pepper` escribía `{"remote": true}` y con eso repetía `package` con `--allow-sensitive
--acknowledge-unscanned`: una decisión tomada una vez valía para cualquier dato que apareciera
después, en ese sistema o en otro (revisión 2026-09-24). Ahora la autorización dice exactamente
qué cubre y `package` se detiene si el paquete trae algo fuera de eso:

  system        el perfil y la huella del legacy (hash de sus artefactos, sin las notas)
  destination   hacia dónde viaja el paquete (hoy: el agente remoto de discovery)
  categories    qué categorías de datos detectados puede llevar, ya sustituidas
                (credenciales quitadas; CURP, RFC, correo, CLABE, tarjeta con seudónimo)
  unscanned     qué archivos que PEPPER no puede leer pueden viajar, cada uno con su sha256

Cuando `package` encuentra algo fuera del alcance, escribe una PROPUESTA junto al paquete con el
alcance que haría falta y no arma nada. Una persona la revisa y `pepper authorize` la vuelve
autorización (o extiende la existente del mismo sistema), con su nombre y la fecha. La llave de
los seudónimos vive al lado de la autorización y nunca viaja.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from pepper import manifest as evidence_manifest

REMOTE_DESTINATION = "agente remoto de discovery"
PROPOSAL_SUFFIX = ".data-boundary.propuesta.json"
# Las notas del humano cambian sin que cambie el sistema: no entran en la huella.
_NOTE_SUFFIXES = {".md", ".txt"}


class BoundaryError(ValueError):
    """El paquete trae datos fuera de lo autorizado; quedó una propuesta para que una persona decida."""

    def __init__(self, message: str, proposal_path: Optional[Path] = None):
        super().__init__(message)
        self.proposal_path = proposal_path


def legacy_fingerprint(legacy_copy: Optional[Path]) -> str:
    """sha256 de la lista (ruta, sha256) de los artefactos del legacy, sin las notas."""
    if legacy_copy is None or not legacy_copy.is_dir():
        return "sin-legacy"
    digest = hashlib.sha256()
    for path in sorted(legacy_copy.rglob("*")):
        if path.is_file() and not path.is_symlink() and path.suffix.lower() not in _NOTE_SUFFIXES:
            digest.update(f"{path.relative_to(legacy_copy).as_posix()}\0{evidence_manifest.sha256_file(path)}\n".encode("utf-8"))
    return digest.hexdigest()


def proposal(staging: Path, profile_id: Optional[str], report, excluded: List[str]) -> Dict[str, Any]:
    """El alcance que este paquete necesita, sacado de lo que el escáner vio en la copia."""
    return {
        "system": {"profile_id": profile_id, "legacy_sha256": legacy_fingerprint(staging / "legacy")},
        "destination": REMOTE_DESTINATION,
        # completos: el alcance se decide con TODAS las categorías y TODOS los archivos no inspeccionados
        "categories": sorted(report.categories),
        "unscanned": {finding.path: evidence_manifest.sha256_file(staging / finding.path)
                      for finding in sorted(report.unscanned, key=lambda f: f.path)
                      if (staging / finding.path).is_file()},
        "excluded": sorted(excluded),
        "locations": _sample_locations(report.sensitive),
    }


def _sample_locations(findings, limit: int = 50) -> List[str]:
    """Para mostrar: la primera ubicación de cada categoría y después las demás, hasta `limit`."""
    first = {}
    for finding in findings:
        first.setdefault(finding.kind, finding)
    ordered = list(first.values()) + [f for f in findings if first.get(f.kind) is not f]
    return [f"{f.location} ({f.kind})" for f in ordered[:limit]]


def load(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    missing = [key for key in ("system", "destination", "categories", "unscanned", "decided_by", "date") if key not in data]
    if missing:
        raise ValueError(f"{path} no es una autorización de datos de PEPPER (falta {', '.join(missing)}); "
                         "se crea con `pepper authorize <propuesta> --by <nombre>`")
    return data


def out_of_scope(authorization: Dict[str, Any], needed: Dict[str, Any]) -> List[str]:
    """Lo que el paquete trae y la autorización no cubre; vacío si todo cabe."""
    problems: List[str] = []
    authorized_system = authorization.get("system") or {}
    if authorized_system.get("profile_id") != needed["system"].get("profile_id"):
        problems.append(f"la autorización es de otro sistema (perfil {authorized_system.get('profile_id')}; "
                        f"este paquete: {needed['system'].get('profile_id')})")
    elif authorized_system.get("legacy_sha256") != needed["system"].get("legacy_sha256"):
        problems.append("el legacy cambió desde que se autorizó (otra versión u otro sistema): sus artefactos no son "
                        "los que la persona aprobó")
    if authorization.get("destination") != needed["destination"]:
        problems.append(f"destino no autorizado: {needed['destination']}")
    extra = sorted(set(needed["categories"]) - set(authorization.get("categories") or []))
    if extra:
        problems.append(f"categorías de datos no autorizadas: {', '.join(extra)}")
    approved = authorization.get("unscanned") or {}
    for path, digest in needed["unscanned"].items():
        if path not in approved:
            problems.append(f"archivo no inspeccionado sin autorizar: {path}")
        elif approved[path] != digest:
            problems.append(f"archivo no inspeccionado cambió desde que se autorizó: {path}")
    return problems


def write_proposal(package_dir: Path, needed: Dict[str, Any], problems: List[str]) -> Path:
    path = package_dir.with_name(package_dir.name + PROPOSAL_SUFFIX)
    body = dict(needed, why=problems)
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def key_path(authorization_path: Path) -> Path:
    return authorization_path.with_name(authorization_path.stem + ".seudonimos.key")


def pseudonym_key(authorization_path: Path) -> bytes:
    path = key_path(authorization_path)
    if not path.is_file():
        raise ValueError(f"falta la llave de seudónimos {path}: se crea con `pepper authorize`; sin ella no hay sustitución consistente")
    return bytes.fromhex(path.read_text(encoding="utf-8").strip())


def authorize(proposal_path: Path, decided_by: str, out: Path, today: Optional[str] = None) -> Dict[str, Any]:
    """Convierte una propuesta en autorización (o extiende la del mismo sistema) y asegura la llave."""
    if not decided_by.strip():
        raise ValueError("--by: el nombre de la persona que autoriza es obligatorio")
    needed = json.loads(proposal_path.read_text(encoding="utf-8"))
    for key in ("system", "destination", "categories", "unscanned"):
        if key not in needed:
            raise ValueError(f"{proposal_path} no es una propuesta de `pepper package` (falta {key})")
    record = {"by": decided_by.strip(), "date": today or date.today().isoformat(),
              "categories": needed["categories"], "unscanned": sorted(needed["unscanned"])}
    if out.is_file():
        current = load(out)
        if current["system"] != needed["system"] or current["destination"] != needed["destination"]:
            raise ValueError(f"{out} autoriza otro sistema o de otra versión del legacy, u otro destino; no se mezcla. "
                             "Si el legacy de verdad cambió, que la persona borre esa autorización y decida de nuevo")
        authorization = current
        authorization["categories"] = sorted(set(current["categories"]) | set(needed["categories"]))
        authorization["unscanned"] = dict(current["unscanned"], **needed["unscanned"])
    else:
        authorization = {"system": needed["system"], "destination": needed["destination"],
                         "categories": sorted(needed["categories"]), "unscanned": dict(needed["unscanned"])}
    authorization["decided_by"] = record["by"]
    authorization["date"] = record["date"]
    authorization["history"] = list(authorization.get("history") or []) + [record]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(authorization, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    key = key_path(out)
    if not key.is_file():
        fd = os.open(str(key), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(secrets.token_hex(32) + "\n")
    return authorization
