"""Lectores genéricos de configuración: clave=valor, JSON y XML, y la URL de un datasource.

Hasta la auditoría 2026-09-29 `rehydrate.datasource` sabía leer dos frameworks (YAML de
Spring, Groovy compilado) y una sola forma de URL (`jdbc:motor://host:puerto/base`). Un
`.env` de Laravel, un `appsettings.json` de .NET, un `Web.config`, un `database.yml` de
Rails o un `standalone.xml` de WildFly respondían BLOCKED con un motivo falso ("no dice a
qué conectarse"). Aquí viven los tres FORMATOS de texto que cubren esos casos; el perfil
dice en qué archivos buscar (`datasource.files`), qué clave es cada cosa (`datasource.keys`)
y, si la URL no es JDBC, con qué regex leerla (`datasource.url_pattern`).

Sin dependencias fuera de la biblioteca estándar. Sin adivinar: lo que un archivo no dice,
el que llama lo declara como faltante con el nombre del archivo y de la clave.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

# La forma JDBC de siempre, ahora con grupos nombrados: es el `url_pattern` por defecto.
JDBC_URL_PATTERN = r"jdbc:(?P<engine>\w+)://(?P<host>[A-Za-z0-9.-]+)(?::(?P<port>\d+))?/(?P<db>[A-Za-z0-9_]+)"
_URL_GROUPS = ("engine", "host", "port", "db", "user", "password")
_REQUIRED_URL_GROUPS = ("host", "db")


class ConfigError(ValueError):
    """Un archivo o un patrón que no se entiende; el mensaje dice cuál y por qué."""


# ------------------------------------------------------------------ clave=valor

def _unquote(value: str) -> str:
    """Quita el comentario al final y las comillas que envuelven el valor (`"s3c#ret"  # nota` → `s3c#ret`)."""
    value = value.strip()
    if value[:1] in ("\"", "'"):
        closing = value.find(value[0], 1)
        if closing > 0:
            return value[1:closing]
    for marker in (" #", " ;", "\t#"):
        if marker in value:
            value = value.split(marker, 1)[0].rstrip()
    return value


def parse_key_value(text: str) -> Dict[str, str]:
    """`.env` / `.properties` / `.ini` / YAML plano → {clave.punteada: valor}.

    Acepta `CLAVE=valor` (con `export` delante o no), `clave: valor` con anidamiento por
    sangría (así un `database.yml` de Rails da `production.host`), y secciones `[nombre]` de
    un `.ini`, que prefijan sus claves (`[database]` + `host=…` → `database.host`). Comentarios
    con `#`, `;` o `!`; listas `- item` se ignoran. Una clave se decide por el separador que
    aparece primero: `DB_URL=postgres://u:p@h/db` es `=`; `url: jdbc:x` es `:`."""
    flat: Dict[str, str] = {}
    stack: List[Tuple[int, str]] = []
    section = ""
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith(("#", ";", "!")):
            continue
        header = re.fullmatch(r"\[([^\]]+)\]", stripped)
        if header:
            section, stack = header.group(1).strip(), []
            continue
        if stripped.startswith("- ") or stripped == "---":
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export "):].strip()
        eq, colon = stripped.find("="), stripped.find(":")
        if eq == -1 and colon == -1:
            continue
        if eq != -1 and (colon == -1 or eq < colon):
            key = stripped[:eq].strip()
            path = f"{section}.{key}" if section else key
            flat[path] = _unquote(stripped[eq + 1:])
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        key, value = stripped[:colon].strip(), _unquote(stripped[colon + 1:])
        if key == "<<":
            continue   # `<<: *default` (mezcla YAML): no es una clave, y el anclaje no se expande — lo que falte se pide
        if value.startswith(("&", "*")) and " " not in value:
            value = ""   # `default: &default` define un anclaje: la clave es un padre, no un valor
        while stack and stack[-1][0] >= indent:
            stack.pop()
        path = ".".join([k for _, k in stack] + [key])
        if section:
            path = f"{section}.{path}"
        if value:
            flat[path] = value
        stack.append((indent, key))
    return flat


# ------------------------------------------------------------------ JSON

def parse_json_flat(text: str, filename: str = "") -> Dict[str, str]:
    """JSON → {clave.punteada: valor escalar}. Las listas se indexan (`a.0.b`)."""
    try:
        data = json.loads(text)
    except ValueError as error:
        raise ConfigError(f"{filename or 'el JSON'} no es JSON válido: {str(error)[:120]}")
    flat: Dict[str, str] = {}

    def walk(node: Any, prefix: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{prefix}.{key}" if prefix else str(key))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{prefix}.{index}" if prefix else str(index))
        elif node is not None and not isinstance(node, (dict, list)):
            flat[prefix] = node if isinstance(node, str) else json.dumps(node)

    walk(data, "")
    return flat


# ------------------------------------------------------------------ XML

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _parse_xml(text: str, filename: str = "") -> ET.Element:
    try:
        return ET.fromstring(text)
    except ET.ParseError as error:
        raise ConfigError(f"{filename or 'el XML'} no es XML válido: {error}")


def parse_xml_flat(text: str, filename: str = "") -> Dict[str, str]:
    """XML → {ruta/de/elementos: texto, ruta/@atributo: valor}; los hermanos repetidos llevan `[n]`."""
    root = _parse_xml(text, filename)
    flat: Dict[str, str] = {}

    def walk(element: ET.Element, prefix: str) -> None:
        for name, value in element.attrib.items():
            flat[f"{prefix}/@{_local(name)}"] = value
        text_value = (element.text or "").strip()
        if text_value:
            flat[prefix] = text_value
        seen: Dict[str, int] = {}
        for child in element:
            name = _local(child.tag)
            index = seen.get(name, 0)
            seen[name] = index + 1
            walk(child, f"{prefix}/{name}" + (f"[{index}]" if index else ""))

    walk(root, _local(root.tag))
    return flat


_SEGMENT = re.compile(r"^(?P<tag>[\w.-]+)(?:\[@(?P<attr>[\w.:-]+)=(?P<value>[^\]]*)\])?$")


def lookup_xml(text: str, path: str, filename: str = "") -> Optional[str]:
    """Ruta simple: `configuration/connectionStrings/add[@name=Default]/@connectionString`.

    Segmentos separados por `/`; `tag[@attr=valor]` filtra hermanos por atributo; un último
    segmento `@attr` devuelve el atributo, si no, el texto del elemento. Los espacios de
    nombres se ignoran (se compara el nombre local). None si la ruta no existe."""
    root = _parse_xml(text, filename)
    segments = [s for s in path.strip("/").split("/") if s]
    if not segments:
        raise ConfigError(f"ruta XML vacía para {filename or 'el XML'}")
    attribute: Optional[str] = None
    if segments[-1].startswith("@"):
        attribute = segments.pop()[1:]
    if not segments:
        raise ConfigError(f"la ruta XML {path!r} no nombra ningún elemento")
    first = _SEGMENT.match(segments[0])
    if not first or _local(root.tag) != first.group("tag"):
        return None
    if first.group("attr") and root.attrib.get(first.group("attr")) != first.group("value"):
        return None
    current: List[ET.Element] = [root]
    for segment in segments[1:]:
        m = _SEGMENT.match(segment)
        if not m:
            raise ConfigError(f"segmento de ruta XML no reconocido: {segment!r} (se admite `tag` o `tag[@attr=valor]`)")
        following: List[ET.Element] = []
        for element in current:
            for child in element:
                if _local(child.tag) != m.group("tag"):
                    continue
                if m.group("attr") and child.attrib.get(m.group("attr")) != m.group("value"):
                    continue
                following.append(child)
        current = following
        if not current:
            return None
    element = current[0]
    if attribute is not None:
        return element.attrib.get(attribute)
    return (element.text or "").strip() or None


# ------------------------------------------------------------------ la URL

def compile_url_pattern(pattern: Optional[str]) -> "re.Pattern[str]":
    """La regex con que se lee la URL del datasource: la del perfil o la JDBC de siempre.

    Una del perfil debe capturar al menos `host` y `db`; `engine`, `port`, `user` y `password`
    son opcionales (`Server=h;Database=db;User Id=u;Password=p` no dice el motor)."""
    if not pattern:
        return re.compile(JDBC_URL_PATTERN)
    try:
        regex = re.compile(pattern)
    except re.error as error:
        raise ConfigError(f"datasource.url_pattern inválida ({error}): {pattern!r}")
    missing = [g for g in _REQUIRED_URL_GROUPS if g not in regex.groupindex]
    if missing:
        raise ConfigError(f"datasource.url_pattern debe capturar los grupos nombrados {', '.join(_REQUIRED_URL_GROUPS)} "
                          f"(faltan: {', '.join(missing)}); opcionales: engine, port, user, password")
    unknown = sorted(set(regex.groupindex) - set(_URL_GROUPS))
    if unknown:
        raise ConfigError(f"datasource.url_pattern captura grupos que no significan nada para el plan: {', '.join(unknown)} "
                          f"(válidos: {', '.join(_URL_GROUPS)})")
    return regex


def parse_datasource_url(url: str, pattern: Optional[str] = None) -> Dict[str, str]:
    """→ {engine?, host, port?, db, user?, password?} o ConfigError si la URL no casa."""
    regex = compile_url_pattern(pattern)
    m = regex.search(url)
    if not m:
        how = "con la url_pattern del perfil" if pattern else "como URL JDBC (jdbc:motor://host[:puerto]/base); si no es JDBC, el perfil declara datasource.url_pattern"
        raise ConfigError(f"no entiendo la URL del datasource {how}: {url!r}")
    # una contraseña vacía (`Password=`) es un valor, no una ausencia: solo se descarta lo que no casó
    return {key: value for key, value in m.groupdict().items() if value is not None}
