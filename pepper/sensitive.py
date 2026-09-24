"""Clasificación local y conservadora de datos antes de armar un paquete.

El objetivo no es prometer anonimización automática: es impedir que Claude Code
o cualquier otro agente remoto reciba datos sensibles por accidente. El scanner
solo reporta ubicaciones y categorías; nunca copia el valor encontrado.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

_MAX_TEXT_BYTES = 2_000_000
_MAX_FINDINGS = 200
# Una sola lista para el escáner Y para lo que Package copia: lo que se copia se
# escanea, y lo que no se escanea no se copia (.idea/dataSources.local.xml guarda
# contraseñas de base; antes se copiaba sin mirarse — auditoría 2026-09-11).
IGNORED_DIRS = {".git", "node_modules", "target", "__pycache__", ".idea", ".vscode"}
IGNORED_SUFFIXES = {".class", ".pyc"}
_IGNORED_DIRS, _IGNORED_SUFFIXES = IGNORED_DIRS, IGNORED_SUFFIXES

_KEYWORDS = r"(?:password|passwd|passphrase|pwd|psw|contrase\w*|clave|secret|token|api[_-]?key|client[_-]?secret|credencial)"
# clave=valor, clave: valor, clave => valor; con sufijos (db_password_prod, passwordEncrypted)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)[\"']?" + _KEYWORDS + r"\w*[\"']?\s*(?:[:=]|=>)\s*[\"']?([^\s\"'<>()]{4,})"
)
_SECRET_XML = re.compile(
    r"(?i)<" + _KEYWORDS + r"[^>]*>\s*([^<\s]{4,})\s*</"
)
# SQL: CREATE ROLE … PASSWORD 'x'; CREATE USER MAPPING … OPTIONS (user 'u', password 'x') — el caso de D19
_SECRET_SQL = re.compile(r"(?i)\b(?:password|passwd)\s+'([^']{1,})'")
# esquema://usuario:clave@host (jdbc, postgres://, amqp://, http://)
_SECRET_URL = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/:@\"']+:([^\s@\"']{1,})@")
_SECRET_AUTH = re.compile(r"(?i)\bauthorization\s*[:=]\s*(?:basic|bearer)\s+([^\s\"']{8,})")
_PRIVATE_KEY = re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")   # RSA, EC, OPENSSH, DSA, ENCRYPTED, PGP…
# Archivos que SON material sensible por su nombre, aunque sean binarios.
_SENSITIVE_NAMES = {".pgpass", ".netrc", ".htpasswd", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}
_SENSITIVE_SUFFIXES = {".jks", ".p12", ".pfx", ".key", ".pem", ".keystore", ".ppk"}
_CLABE = re.compile(r"\b\d{18}\b")
_TARJETA = re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b")
_CURP = re.compile(r"\b[A-Z][AEIOUX][A-Z]{2}\d{6}[HM][A-Z]{5}[A-Z0-9]\d\b", re.IGNORECASE)
# RFC (persona moral 3 letras, física 4) con fecha válida y homoclave que termina en dígito o A,
# solo en mayúsculas: un patrón más laxo bloquearía identificadores inocentes de logs y SQL.
_RFC = re.compile(r"\b[A-ZÑ]{3,4}\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])[A-Z0-9]{2}[0-9A]\b")
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_SAFE_VALUES = {
    "******", "*****", "xxxx", "xxxxx", "[redactado]", "[redacted]", "${secret}", "${password}",
}


@dataclass(frozen=True)
class Finding:
    kind: str
    path: str
    line: Optional[int] = None

    @property
    def location(self) -> str:
        return f"{self.path}:{self.line}" if self.line else self.path


@dataclass
class Report:
    sensitive: List[Finding] = field(default_factory=list)
    unscanned: List[Finding] = field(default_factory=list)

    def add_sensitive(self, kind: str, path: str, line: int) -> None:
        finding = Finding(kind, path, line)
        if finding not in self.sensitive and len(self.sensitive) < _MAX_FINDINGS:
            self.sensitive.append(finding)

    def add_unscanned(self, kind: str, path: str) -> None:
        finding = Finding(kind, path)
        if finding not in self.unscanned and len(self.unscanned) < _MAX_FINDINGS:
            self.unscanned.append(finding)


Ignore = Callable[[str, List[str]], List[str]]


def _iter_files(root: Path, ignore: Optional[Ignore] = None) -> Iterable[Path]:
    """Enumera sin seguir enlaces; los symlinks los rechaza Package por separado."""
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        ignored = set(ignore(directory, dirnames + filenames)) if ignore else set()
        ignored.update(_IGNORED_DIRS)
        dirnames[:] = sorted(name for name in dirnames if name not in ignored)
        for name in sorted(filenames):
            if name in ignored:
                continue
            path = Path(directory) / name
            if path.suffix.lower() not in _IGNORED_SUFFIXES:
                yield path


def _looks_binary(sample: bytes) -> bool:
    if b"\x00" in sample:
        return True
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _safe_secret_value(value: str) -> bool:
    normalized = value.strip().strip("\"'").lower()
    # lo que PEPPER ya quitó (notas redactadas, credenciales sustituidas) no es un secreto
    return (normalized in _SAFE_VALUES or normalized.startswith(("${", "[redactado", "[credencial]"))
            or set(normalized) <= {"*", "x", "-"})


def _scan_text(text: str, display: str, report: Report) -> None:
    for number, line in enumerate(text.splitlines(), 1):
        if _PRIVATE_KEY.search(line):
            report.add_sensitive("private_key", display, number)
        for match in _SECRET_ASSIGNMENT.finditer(line):
            # `String token = request.getHeader(` es código, no un secreto: el valor es una llamada
            if line[match.end(1):match.end(1) + 1] == "(":
                continue
            if not _safe_secret_value(match.group(1)):
                report.add_sensitive("credential", display, number)
        for pattern in (_SECRET_XML, _SECRET_SQL, _SECRET_URL, _SECRET_AUTH):
            for match in pattern.finditer(line):
                if not _safe_secret_value(match.group(1)):
                    report.add_sensitive("credential", display, number)
        if _CLABE.search(line):
            report.add_sensitive("clabe", display, number)
        if _TARJETA.search(line):
            report.add_sensitive("tarjeta", display, number)
        if _CURP.search(line):
            report.add_sensitive("curp", display, number)
        if _RFC.search(line):
            report.add_sensitive("rfc", display, number)
        if _EMAIL.search(line):
            report.add_sensitive("email", display, number)


def scan(roots: Iterable[Tuple[str, Path, Optional[Ignore]]]) -> Report:
    """Escanea raíces y devuelve únicamente categorías/ubicaciones.

    Los binarios, los archivos grandes y los que no se dejan decodificar completos
    se marcan como no inspeccionados: afirmar que están limpios sin haberlos leído
    sería otra forma de inventar evidencia.
    """
    report = Report()
    for label, root, ignore in roots:
        if root is None or not root.is_dir():
            continue
        for path in _iter_files(root, ignore):
            relative = path.relative_to(root).as_posix()
            display = f"{label}/{relative}" if label else relative
            if path.is_symlink():
                report.add_unscanned("symlink", display)
                continue
            if path.name.lower() in _SENSITIVE_NAMES or path.suffix.lower() in _SENSITIVE_SUFFIXES:
                report.add_sensitive("key_material", display, None)
                continue
            try:
                size = path.stat().st_size
                if size > _MAX_TEXT_BYTES:
                    report.add_unscanned("large_file", display)
                    continue
                data = path.read_bytes()
            except OSError:
                report.add_unscanned("unreadable", display)
                continue
            if _looks_binary(data[:8192]):
                report.add_unscanned("binary", display)
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                # UTF-8 en los primeros 8 KB y otra codificación después (un SQL viejo en
                # latin-1): no se puede afirmar que se leyó completo → no inspeccionado.
                report.add_unscanned("undecodable", display)
                continue
            _scan_text(text, display, report)
    return report


def summarize(findings: Iterable[Finding], limit: int = 8) -> str:
    items = list(findings)
    shown = ", ".join(f"{item.location} ({item.kind})" for item in items[:limit])
    if len(items) > limit:
        shown += f", … y {len(items) - limit} más"
    return shown


def is_key_material(path: Path) -> bool:
    """Un archivo que ES material de llave por su nombre (keystore, .pem, id_rsa…)."""
    return path.name.lower() in _SENSITIVE_NAMES or path.suffix.lower() in _SENSITIVE_SUFFIXES


# Sustitución: lo que el escáner detecta en texto, reemplazado en la COPIA que viaja.
# Las credenciales se quitan; los datos de personas se sustituyen por un seudónimo estable
# (HMAC con una llave local que no viaja): la misma CURP es el mismo seudónimo en el log,
# en el mapa y en la sesión siguiente, así que el agente puede seguir a "la misma persona"
# sin ver quién es. No es anonimización: un nombre propio o un dato sin patrón no se ve.
CREDENTIAL_TOKEN = "[CREDENCIAL]"
PRIVATE_KEY_TOKEN = "[LLAVE PRIVADA EXCLUIDA]"
_PII = (("tarjeta", "TARJETA", _TARJETA), ("clabe", "CLABE", _CLABE), ("curp", "CURP", _CURP),
        ("rfc", "RFC", _RFC), ("email", "CORREO", _EMAIL))


def _normalize_pii(kind: str, value: str) -> str:
    if kind in ("tarjeta", "clabe"):
        return re.sub(r"\D", "", value)
    return value.lower() if kind == "email" else value.upper()


def pseudonym(kind: str, value: str, key: bytes) -> str:
    label = dict((k, t) for k, t, _ in _PII).get(kind, kind.upper())
    digest = hmac.new(key, f"{kind}:{_normalize_pii(kind, value)}".encode("utf-8"), hashlib.sha256).hexdigest()
    return f"[{label}-{digest[:10]}]"


def _credential_spans(line: str) -> List[Tuple[int, int]]:
    spans: List[Tuple[int, int]] = []
    for match in _SECRET_ASSIGNMENT.finditer(line):
        if line[match.end(1):match.end(1) + 1] == "(" or _safe_secret_value(match.group(1)):
            continue
        spans.append(match.span(1))
    for pattern in (_SECRET_XML, _SECRET_SQL, _SECRET_URL, _SECRET_AUTH):
        for match in pattern.finditer(line):
            if not _safe_secret_value(match.group(1)):
                spans.append(match.span(1))
    merged: List[Tuple[int, int]] = []
    for start, end in sorted(spans):
        # una barra invertida final es el escape de la comilla que cierra (JSON dentro de JSON):
        # quitarla rompería el archivo
        while end > start and line[end - 1] == "\\":
            end -= 1
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def pseudonymize_text(text: str, key: bytes) -> Tuple[str, Dict[str, int]]:
    """(texto sustituido, conteo por categoría). Conserva el número de líneas: las referencias
    archivo:línea de la evidencia siguen apuntando al mismo lugar."""
    counts: Dict[str, int] = {}
    out: List[str] = []
    in_key = False
    for line in text.split("\n"):
        if _PRIVATE_KEY.search(line) or in_key:
            in_key = not re.search(r"-----END [A-Z ]*PRIVATE KEY-----", line)
            counts["private_key"] = counts.get("private_key", 0) + 1
            out.append(PRIVATE_KEY_TOKEN)
            continue
        spans = _credential_spans(line)
        for start, end in reversed(spans):
            line = line[:start] + CREDENTIAL_TOKEN + line[end:]
        if spans:
            counts["credential"] = counts.get("credential", 0) + len(spans)
        for kind, _, pattern in _PII:
            def swap(match: "re.Match[str]", kind: str = kind) -> str:
                counts[kind] = counts.get(kind, 0) + 1
                return pseudonym(kind, match.group(0), key)
            line = pattern.sub(swap, line)
        out.append(line)
    return "\n".join(out), counts
