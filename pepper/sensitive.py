"""Clasificación local y conservadora de datos antes de armar un paquete.

El objetivo no es prometer anonimización automática: es impedir que Claude Code
o cualquier otro agente remoto reciba datos sensibles por accidente. El scanner
solo reporta ubicaciones y categorías; nunca copia el valor encontrado.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple

_MAX_TEXT_BYTES = 2_000_000
_MAX_FINDINGS = 200
_IGNORED_DIRS = {".git", "node_modules", "target", "__pycache__", ".idea", ".vscode"}
_IGNORED_SUFFIXES = {".class", ".pyc"}

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)[\"']?(?:password|passwd|pwd|contrase(?:ña|na)|clave|secret|token|api[_-]?key|client[_-]?secret)"
    r"[\"']?\s*[:=]\s*[\"']?([^\s\"'#,;<>]{4,})"
)
_SECRET_XML = re.compile(
    r"(?i)<(?:password|passwd|pwd|contrase(?:ña|na)|clave|secret|token|api[_-]?key|client[_-]?secret)[^>]*>"
    r"\s*([^<\s]{4,})\s*</"
)
_PRIVATE_KEY = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")
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
    return normalized in _SAFE_VALUES or normalized.startswith("${") or set(normalized) <= {"*", "x", "-"}


def _scan_text(text: str, display: str, report: Report) -> None:
    for number, line in enumerate(text.splitlines(), 1):
        if _PRIVATE_KEY.search(line):
            report.add_sensitive("private_key", display, number)
        for match in _SECRET_ASSIGNMENT.finditer(line):
            if not _safe_secret_value(match.group(1)):
                report.add_sensitive("credential", display, number)
        for match in _SECRET_XML.finditer(line):
            if not _safe_secret_value(match.group(1)):
                report.add_sensitive("credential", display, number)
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
            display = f"{label}/{relative}"
            if path.is_symlink():
                report.add_unscanned("symlink", display)
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
