"""`regex_extractor`: el mecanismo genérico de `pepper map` para un stack con fuente en texto.

Una familia nueva (PHP, Django, Rails, Node con fuente) declara sus rutas, sus jobs y sus
hosts externos en archivos de texto con una forma regular: `Route::get('/x', …)`,
`path('x/', vista)`, `get 'x', to: 'c#a'`, `$schedule->command('x')->dailyAt(…)`. Hasta la
auditoría 2026-09-29 no había forma de leerlos sin escribir un lector en Python por
tecnología; este mecanismo lo hace con DATOS: el perfil declara qué miembros del
artefacto mirar, una regex con grupos nombrados y a qué superficie del mapa va cada
match. Los grupos se mapean a los campos que `schemas/system-map.schema.json` exige para
esa superficie; la evidencia es `miembro:línea`.

    {"mechanism": "regex_extractor", "surface": "entrypoints",
     "member_patterns": ["routes/.*\\\\.php$"],
     "pattern": "Route::(?P<method>get|post)\\\\('(?P<path>[^']+)'",
     "flags": "i", "defaults": {"method": "GET"}, "transforms": {"method": "upper"}}

Fail-honest, como el resto del mapa: un patrón inválido, una superficie que no existe, un
grupo que el contrato no admite o un `member_patterns` que no casa con nada se declaran
como hueco con nombre; nunca un traceback, nunca un cero silencioso. Dos matches con el
mismo contenido (misma ruta, mismo verbo…) son una sola entrada: la primera línea donde
apareció es su evidencia.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from pathlib import Path

MECHANISM = "regex_extractor"

# Por superficie: qué grupos nombrados admite el contrato del mapa, cuáles son obligatorios,
# qué vale por defecto y qué vocabularios cerrados hay que respetar.
_SURFACES: Dict[str, Dict[str, Any]] = {
    "entrypoints": {
        "fields": ("kind", "method", "path", "handler", "auth"),
        "required": ("path",),
        "defaults": {"kind": "http_route"},
        "enums": {"kind": ("http_route", "rest_endpoint", "screen", "public_route"),
                  "auth": ("public", "authenticated", "unknown")},
    },
    "jobs": {
        "fields": ("name", "schedule", "signature", "detail"),
        "required": ("name",),
        "defaults": {},
        "enums": {},
    },
    "external_dependencies": {
        "fields": ("name", "kind", "target", "detail"),
        "required": ("name",),
        "defaults": {"kind": "other"},
        "enums": {"kind": ("rest", "db", "foreign_db", "smtp", "bus", "web", "file", "other")},
        # `host` es el nombre natural para un externo: vale como `name` y como `target`
        "aliases": {"host": ("name", "target")},
    },
}
_REPORT_ATTR = {"entrypoints": "entrypoints", "jobs": "jobs", "external_dependencies": "external"}
_FLAGS = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL, "x": re.VERBOSE}


def _leading_slash(value: str) -> str:
    return value if value.startswith("/") else "/" + value


TRANSFORMS: Dict[str, Callable[[str], str]] = {
    "upper": str.upper,
    "lower": str.lower,
    "strip": str.strip,
    "last_segment": lambda value: value.rsplit(".", 1)[-1],
    "leading_slash": _leading_slash,
}


def _compile(spec: Dict[str, Any]) -> Tuple[Optional["re.Pattern[str]"], Optional[str]]:
    """→ (regex, problema). El problema es texto para el hueco; nunca una excepción."""
    pattern = spec.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        return None, "no declara `pattern`"
    flags = 0
    for letter in str(spec.get("flags") or ""):
        if letter not in _FLAGS:
            return None, f"flag desconocida {letter!r} en `flags` (válidas: {''.join(_FLAGS)})"
        flags |= _FLAGS[letter]
    try:
        regex = re.compile(pattern, flags)
    except re.error as error:
        return None, f"patrón inválido ({error}): {pattern[:120]!r}"
    if not regex.groupindex:
        return None, f"el patrón no captura ningún grupo nombrado (?P<campo>…): {pattern[:120]!r}"
    return regex, None


def _transform_chain(spec: Dict[str, Any]) -> Tuple[Dict[str, List[Callable[[str], str]]], Optional[str]]:
    chains: Dict[str, List[Callable[[str], str]]] = {}
    for field, names in (spec.get("transforms") or {}).items():
        listed = [names] if isinstance(names, str) else list(names or [])
        chain: List[Callable[[str], str]] = []
        for name in listed:
            if name not in TRANSFORMS:
                return {}, f"transform desconocida {name!r} para `{field}` (válidas: {', '.join(TRANSFORMS)})"
            chain.append(TRANSFORMS[name])
        chains[field] = chain
    return chains, None


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def extract(artifact: Path, spec: Dict[str, Any], report: Any) -> None:
    """Corre la regex del perfil sobre los miembros de texto y emite elementos de la superficie declarada."""
    from pepper.inspect.systemmap import iter_members, match_any_pattern

    surface = spec.get("surface")
    contract = _SURFACES.get(str(surface))
    label = f"{MECHANISM}({surface})"
    if contract is None:
        report.gap(f"{MECHANISM}: `surface` debe ser una de {', '.join(_SURFACES)}; el perfil dice {surface!r}")
        return
    regex, problem = _compile(spec)
    if problem or regex is None:
        report.gap(f"{label}: {problem}")
        return
    chains, problem = _transform_chain(spec)
    if problem:
        report.gap(f"{label}: {problem}")
        return
    member_patterns = spec.get("member_patterns") or []
    if not member_patterns:
        report.gap(f"{label}: no declara `member_patterns`; sin ellos no se sabe qué archivos leer")
        return
    exclude = spec.get("exclude_patterns") or []
    aliases: Dict[str, Tuple[str, ...]] = contract.get("aliases", {})
    allowed = set(contract["fields"]) | set(aliases)
    unknown = sorted(set(regex.groupindex) - allowed)
    if unknown:
        report.gap(f"{label}: el patrón captura grupos que el contrato del mapa no admite en `{surface}`: "
                   f"{', '.join(unknown)} (admitidos: {', '.join(sorted(allowed))})")
        return
    for field in list((spec.get("defaults") or {})) + list(chains):
        if field not in allowed:
            report.gap(f"{label}: `{field}` (en defaults/transforms) no es un campo de `{surface}` "
                       f"(admitidos: {', '.join(sorted(allowed))})")
            return

    target: List[Dict[str, Any]] = getattr(report, _REPORT_ATTR[str(surface)])
    seen = set()
    members_read = 0
    matches = 0
    rejected: List[str] = []
    for name, data in iter_members(artifact):
        if not match_any_pattern(member_patterns, name) or match_any_pattern(exclude, name):
            continue
        members_read += 1
        text = data.decode("utf-8", errors="replace")
        for match in regex.finditer(text):
            matches += 1
            element, why = _element(match.groupdict(), spec, contract, chains)
            if why:
                if len(rejected) < 5:
                    rejected.append(f"{name}:{_line_of(text, match.start())} ({why})")
                continue
            key = tuple(sorted(element.items()))
            if key in seen:
                continue
            seen.add(key)
            element["evidence"] = f"{name}:{_line_of(text, match.start())}"
            target.append(element)
    if members_read == 0:
        report.gap(f"{label}: ningún miembro de {artifact.name} casa `member_patterns` "
                   f"({', '.join(member_patterns)}): el perfil busca donde no hay nada")
        return
    if rejected:
        report.gap(f"{label}: {len(rejected)} match(es) no entran al mapa porque no cumplen el contrato: "
                   + "; ".join(rejected))
    if matches == 0:
        report.notes.append(f"{label}: {members_read} miembro(s) leídos y el patrón no casó en ninguno")
    else:
        report.notes.append(f"{label}: {len(seen)} elemento(s) distintos de {matches} match(es) en {members_read} miembro(s)")


def _element(groups: Dict[str, Optional[str]], spec: Dict[str, Any], contract: Dict[str, Any],
             chains: Dict[str, List[Callable[[str], str]]]) -> Tuple[Dict[str, Any], Optional[str]]:
    """Grupos capturados → elemento del contrato, o (…, por qué no)."""
    values: Dict[str, str] = {}
    for group, value in groups.items():
        if value is None:
            continue
        for field in contract.get("aliases", {}).get(group, (group,)):
            values.setdefault(field, value)
    defaults = dict(contract["defaults"])
    defaults.update(spec.get("defaults") or {})
    for field, value in defaults.items():
        values.setdefault(field, str(value))
    for field, chain in chains.items():
        if field in values:
            for transform in chain:
                values[field] = transform(values[field])
    for field in contract["required"]:
        if not values.get(field):
            return {}, f"sin `{field}`"
    for field, vocabulary in contract["enums"].items():
        if field in values and values[field] not in vocabulary:
            return {}, f"`{field}`={values[field]!r} no está en el vocabulario {'/'.join(vocabulary)}"
    return {field: values[field] for field in contract["fields"] if field in values}, None
