#!/usr/bin/env python3
"""Guardia de datos: hook `PreToolUse` de Claude Code que impide al agente leer el legacy.

El orquestador de `/pepper` es un modelo remoto: todo lo que lee entra a su contexto y sale de la
máquina antes de cualquier autorización. La única barrera era una frase en markdown (auditoría
2026-09-29). Este hook es la barrera técnica: corre antes de cada herramienta, lee la llamada por
stdin (JSON de Claude Code) y la bloquea (exit 2, razón por stderr) cuando tocaría:

  - `legacy/` — los artefactos y el respaldo. Solo se permite LISTAR (ls, find, unzip -l, wc,
    sha256sum…) y leer con la herramienta Read un archivo de texto chico que el escáner de
    PEPPER declare limpio (para que `inspector-legacy` pueda leer un pom.xml o un MANIFEST).
    Un respaldo, un desplegable o un archivo con credenciales o datos de personas: nunca.
  - la evidencia cruda — `evidence/<sid>/screens/`, `http.jsonl`, `containers/`, `raw/`;
    `pepper-out/<sid>/correlated/` (antes de la sustitución). `explore.jsonl` y `session.json`
    sí se leen: lo que traen se declara en THREAT-MODEL.md.
  - `pepper-out/rehydrate/.env`, el compose y el restore rendidos (llevan la credencial del
    datasource): se pasan como argumento a `docker compose`, no se abren.
  - la base desechable: `docker … exec db`, `docker … logs db` y cualquier cliente de base (psql,
    mysql, sqlcmd…). La clave de usuario por rol se resuelve dentro del contenedor con
    `roles[].user_sql` de explore.json; el agente no la ve.
  - la autorización de datos (`pepper-out/data-boundary*.json`, `*.seudonimos.key`): solo la escribe
    `pepper authorize` desde la terminal de una persona.
  - escribir dentro de `legacy/` (el legacy es solo lectura) y editar este hook o
    `.claude/settings.json` desde el agente (un guardia que el vigilado puede apagar no es guardia).
    Quien desarrolla PEPPER lanza Claude Code con `PEPPER_GUARDIA_DEV=1` para editarlos: es una
    decisión de quien lanza el proceso, inalcanzable desde dentro de la sesión.

Código en línea (`python3 -c`, `perl -e`, un heredoc) no se puede inspeccionar: se bloquea cuando
menciona una ruta protegida QUE EXISTE en el workspace (en el repositorio de la herramienta, sin
legacy/, un script que diga "legacy/" en un comentario no es un riesgo) o los archivos del guardia.

Es fail-closed: un error interno bloquea. Y es una capa contra el descuido de un agente que sigue
instrucciones, no contra uno adversario: variables de entorno, alias o rutas construidas en tiempo
de ejecución no se resuelven aquí. Lo que sí lo es se dice en THREAT-MODEL.md.

Sin dependencias fuera de la biblioteca estándar; importa `pepper.sensitive` del propio workspace
solo para juzgar el contenido de un archivo de texto bajo `legacy/`.
"""

from __future__ import annotations

import fnmatch
import glob
import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

PROJECT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()
DEV = bool(os.environ.get("PEPPER_GUARDIA_DEV"))

# Rutas (relativas a la raíz del workspace, POSIX, patrones fnmatch) que ninguna herramienta abre.
NUNCA = (
    "legacy", "legacy/*",
    "evidence/*/screens", "evidence/*/screens/*", "evidence/*/http.jsonl", "evidence/*/containers", "evidence/*/containers/*",
    "evidence/*/raw", "evidence/*/raw/*",
    "pepper-out/*/correlated", "pepper-out/*/correlated/*",
    "pepper-out/rehydrate/.env",
    "pepper-out/data-boundary*.json", "pepper-out/*.seudonimos.key", "pepper-out/*.key",
)
# Se pueden nombrar como argumento (docker compose -f …) pero no abrir: llevan la credencial del datasource.
SOLO_ARGUMENTO = (
    "pepper-out/rehydrate/docker-compose.yml", "pepper-out/rehydrate/restore.sh",
    "pepper-out/rehydrate/*.properties", "pepper-out/rehydrate/*.groovy", "pepper-out/rehydrate/*.yml",
)
# El legacy es solo lectura; la autorización la escribe una persona; el guardia no se desactiva desde dentro.
NUNCA_ESCRIBIR = ("legacy", "legacy/*", "pepper-out/data-boundary*.json", "pepper-out/*.key")
GUARDIA = (".claude/settings.json", "scripts/guardia_datos.py")
# Qué mención en código en línea cuenta como riesgo: solo si eso existe aquí (o es el guardia).
MENCIONES = (
    ("legacy/", "legacy"), ("evidence/", "evidence"), ("correlated/", "pepper-out/*/correlated"),
    ("rehydrate/.env", "pepper-out/rehydrate/.env"), ("data-boundary", "pepper-out/data-boundary*.json"),
    (".seudonimos.key", "pepper-out/*.seudonimos.key"),
)

# Comandos que LEEN o COPIAN contenido: con ellos toda ruta protegida bloquea, incluidas las de solo argumento.
LECTORES = {
    "cat", "head", "tail", "less", "more", "grep", "egrep", "fgrep", "rg", "ag", "ack", "sed", "awk", "cut", "sort",
    "uniq", "strings", "xxd", "hexdump", "od", "base64", "base32", "jq", "yq", "tr", "tee", "cp", "mv", "rsync", "scp",
    "zip", "gzip", "gunzip", "zcat", "bzcat", "xzcat", "diff", "cmp", "comm", "paste", "join", "nl", "rev", "tac",
    "sqlite3", "open", "xdg-open", "code", "vim", "vi", "nano", "emacs", "install", "dd", "split", "csplit", "iconv",
    "fold", "expand", "column", "pr", "look", "curl", "wget", "python", "python3", "perl", "ruby", "node", "php",
}
# Comandos que solo LISTAN: nombres, tamaños, hashes. Con ellos una ruta bajo legacy/ se permite.
LISTADORES = {"ls", "tree", "du", "wc", "file", "stat", "sha256sum", "sha1sum", "md5sum", "shasum", "readlink",
              "realpath", "test", "[", "basename", "dirname", "find", "unzip", "tar", "jar", "zipinfo"}
CLIENTES_DE_BASE = {"psql", "pg_dump", "pg_restore", "mysql", "mysqldump", "mariadb", "mariadb-dump", "sqlcmd",
                    "sqlplus", "db2", "mongosh", "mongo", "redis-cli", "isql", "osql", "bcp"}
INLINE_CODE = {"python", "python3", "perl", "ruby", "node", "php", "bash", "sh", "zsh", "dash", "ksh", "eval", "exec", "xargs"}
# Sufijos que son un respaldo o un desplegable: nunca se leen aunque parezcan texto.
DUMP_SUFFIXES = {".sql", ".dump", ".bak", ".dmp", ".backup", ".gz", ".bz2", ".xz", ".zip", ".tar", ".tgz", ".war",
                 ".jar", ".ear", ".dll", ".exe", ".bacpac", ".mdb", ".accdb", ".sqlite", ".db", ".csv", ".tsv", ".xls", ".xlsx"}
MAX_READ_BYTES = 2_000_000
OPERADORES = {"|", "||", "&&", ";", "&", "(", ")", "\n"}


class Bloqueo(Exception):
    pass


# ------------------------------------------------------------------ rutas

def _relative(token: str) -> Optional[str]:
    """La ruta relativa POSIX al workspace de un token que parece ruta, o None si no está dentro."""
    text = token.strip().strip("\"'")
    if not text or text.startswith("-") and "=" not in text:
        return None
    if "=" in text and not text.startswith(("/", "./", "../", "~")):
        text = text.split("=", 1)[1]   # --config=pepper-out/rehydrate/.env
    text = os.path.expanduser(text)
    try:
        path = Path(text)
        if not path.is_absolute():
            path = PROJECT / path
        # sin resolver symlinks (podrían no existir); normalizar `..`
        normalized = Path(os.path.normpath(str(path)))
        return normalized.relative_to(PROJECT).as_posix()
    except (ValueError, OSError):
        return None


def _matches(relative: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(relative, pattern) or fnmatch.fnmatchcase(relative, pattern + "/*")
               for pattern in patterns)


def _protected(relative: str, also: Tuple[str, ...] = ()) -> bool:
    return _matches(relative, NUNCA) or (bool(also) and _matches(relative, also))


def _under_legacy(relative: str) -> bool:
    return relative == "legacy" or relative.startswith("legacy/")


def _exists(relative_pattern: str) -> bool:
    return bool(glob.glob(str(PROJECT / relative_pattern)))


def _risky_mentions(command: str) -> List[str]:
    """Rutas protegidas que el comando nombra Y que existen en este workspace; el guardia siempre."""
    found = [text for text, pattern in MENCIONES if text in command and _exists(pattern)]
    if not DEV and ("guardia_datos" in command or ".claude/settings" in command):
        found.append("el guardia")
    return found


# ------------------------------------------------------------------ Read bajo legacy/

def _legacy_read_allowed(relative: str) -> Optional[str]:
    """None si un Read de este archivo de legacy/ se permite; si no, el porqué."""
    path = PROJECT / relative
    if path.is_dir():
        return "es un directorio: lista con `ls`"
    if not path.is_file():
        return "no existe"
    if path.suffix.lower() in DUMP_SUFFIXES:
        return f"un {path.suffix} es un respaldo o un desplegable: se lee con `pepper map`, nunca desde el agente"
    try:
        size = path.stat().st_size
    except OSError:
        return "ilegible"
    if size > MAX_READ_BYTES:
        return f"pesa {size} bytes: lo que un archivo así trae lo saca `pepper map`, no un Read"
    sys.path.insert(0, str(PROJECT))
    try:
        from pepper.sensitive import scan, uninspectable_kind  # type: ignore
    except Exception:  # noqa: BLE001 — sin escáner no hay forma de saber que está limpio
        return "no se pudo cargar el escáner de PEPPER para comprobar que el archivo está limpio"
    kind = uninspectable_kind(path)
    if kind:
        return f"no se puede inspeccionar ({kind})"
    report = scan([("legacy", path.parent, lambda _d, names: [n for n in names if n != path.name])])
    if report.categories:
        return ("trae " + ", ".join(sorted(report.categories)) + " (credenciales o datos de personas): "
                "lo que necesitas de él lo saca `pepper map`/`pepper rehydrate` redactado")
    return None


# ------------------------------------------------------------------ Bash

_HEREDOC_RE = re.compile(r"<<-?[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1[^\n]*\n.*?^\2[ \t]*$", re.S | re.M)


def _strip_heredocs(command: str) -> str:
    """El cuerpo de un heredoc no son argumentos: un commit cuya descripción dice "legacy/" no
    escribe en legacy/. Lo que un heredoc pueda leer o escribir se juzga aparte, por menciones."""
    return _HEREDOC_RE.sub(lambda m: m.group(0).split("\n", 1)[0], command)


def _segments(command: str) -> List[List[str]]:
    lexer = shlex.shlex(_strip_heredocs(command), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    tokens = list(lexer)
    segments: List[List[str]] = [[]]
    for token in tokens:
        if token in OPERADORES or set(token) <= set("|&;()"):
            segments.append([])
        else:
            segments[-1].append(token)
    return [seg for seg in segments if seg]


def _command_name(segment: List[str]) -> Tuple[str, List[str]]:
    """Nombre del comando saltando `sudo`, `env`, `time`, asignaciones VAR=… y prefijos de ruta."""
    rest = list(segment)
    while rest and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", rest[0]) or rest[0] in ("sudo", "env", "time", "nice", "command", "builtin")):
        rest.pop(0)
    if not rest:
        return "", []
    return os.path.basename(rest[0]), rest[1:]


def _is_pepper_core(name: str, args: List[str]) -> bool:
    return name in ("python", "python3") and len(args) >= 2 and args[0] == "-m" and args[1] == "pepper"


def _is_listing(name: str, args: List[str]) -> bool:
    if name not in LISTADORES:
        return False
    if name == "find" and any(a in ("-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fls") for a in args):
        return False
    if name == "unzip" and not any(a.startswith(("-l", "-Z", "-z")) for a in args):
        return False
    if name == "tar" and not any(a.startswith("-t") or (not a.startswith("-") and "t" in a and "x" not in a and "c" not in a)
                                 for a in args[:1]):
        return False
    if name == "jar" and not any("t" in a and "x" not in a for a in args[:1]):
        return False
    return True


def _check_bash(command: str) -> None:
    mentions = _risky_mentions(command)
    try:
        segments = _segments(command)
    except ValueError:
        if mentions:
            raise Bloqueo("no se pudo interpretar el comando y menciona rutas protegidas: se bloquea por seguridad")
        return
    for segment in segments:
        name, args = _command_name(segment)
        if not name:
            continue
        if _is_pepper_core(name, args):
            continue   # el núcleo maneja esos datos con sus propios redactores
        if name in CLIENTES_DE_BASE:
            raise Bloqueo(f"`{name}` abre la base desechable desde el agente: sus filas son datos de producción. "
                          "La clave de usuario por rol se resuelve dentro del contenedor con `roles[].user_sql`")
        if name == "docker":
            _check_docker(args)
        if name in INLINE_CODE and (any(a in ("-c", "-e", "-r", "--eval", "-I") for a in args) or "<<" in command):
            if mentions:
                raise Bloqueo("código en línea (`-c`, `-e`, heredoc) que menciona " + ", ".join(mentions)
                              + ": no se puede comprobar qué lee o escribe")
        reader = name in LECTORES or name == "cd" or name == "pushd"
        listing = _is_listing(name, args)
        for token in args if name != "cd" else segment[1:]:
            relative = _relative(token)
            if relative is None:
                continue
            if _matches(relative, GUARDIA) and not DEV and name in ("rm", "mv", "cp", "tee", "sed", "install", "truncate", "chmod", "ln"):
                raise Bloqueo(f"`{relative}` es el guardia de datos: no se toca desde el agente")
            if reader and _protected(relative, SOLO_ARGUMENTO):
                raise Bloqueo(f"`{name}` sobre `{relative}`: contenido del legacy o de la evidencia cruda; no entra al contexto del agente")
            if listing:
                continue   # nombres, tamaños, hashes: sin contenido
            if _matches(relative, NUNCA_ESCRIBIR) and _is_write(name, segment):
                raise Bloqueo(f"`{relative}`: el legacy es solo lectura y la autorización de datos la escribe una persona")
            if _protected(relative):
                raise Bloqueo(f"`{name}` sobre `{relative}`: solo se lista (ls, find, unzip -l, wc, sha256sum); el contenido lo procesa el núcleo")
        # redirecciones: `< legacy/x` lee, `> legacy/x` escribe
        for index, token in enumerate(segment):
            if token in ("<", "<<<") and index + 1 < len(segment):
                relative = _relative(segment[index + 1])
                if relative and _protected(relative, SOLO_ARGUMENTO):
                    raise Bloqueo(f"redirección desde `{relative}`: contenido protegido")
            if token in (">", ">>") and index + 1 < len(segment):
                relative = _relative(segment[index + 1])
                if relative and (_matches(relative, NUNCA_ESCRIBIR) or (_matches(relative, GUARDIA) and not DEV)):
                    raise Bloqueo(f"redirección hacia `{relative}`: solo lectura")


def _is_write(name: str, segment: List[str]) -> bool:
    return name in ("rm", "mv", "cp", "tee", "touch", "mkdir", "rmdir", "install", "truncate", "chmod", "chown", "ln",
                    "sed", "unzip", "tar", "dd", "rsync", "scp", "git") or ">" in segment or ">>" in segment


def _check_docker(args: List[str]) -> None:
    """`docker … exec db`, `docker … logs db`, `docker cp` y cualquier cliente de base dentro de un exec."""
    words = [a for a in args if not a.startswith("-")]
    if "cp" in words:
        raise Bloqueo("`docker cp` saca archivos de un contenedor del legacy: no")
    for verb in ("exec", "logs", "run", "attach"):
        if verb in words:
            after = words[words.index(verb) + 1:]
            target = after[0] if after else ""
            if target == "db" or re.search(r"(^|[-_])db([-_]|$)", target):
                raise Bloqueo(f"`docker … {verb} {target}`: la base desechable tiene datos de producción; el explorador y "
                              "`pepper rehydrate` hablan con ella por el núcleo, el agente no")
            if verb == "exec" and any(os.path.basename(a) in CLIENTES_DE_BASE for a in after):
                raise Bloqueo("cliente de base dentro de `docker exec`: no desde el agente")


# ------------------------------------------------------------------ herramientas de archivo

def _check_read(tool: str, payload: dict) -> None:
    target = payload.get("file_path") or payload.get("notebook_path") or payload.get("path") or ""
    relative = _relative(str(target)) if target else None
    if tool == "Read":
        if relative is None:
            return
        if _under_legacy(relative):
            why = _legacy_read_allowed(relative)
            if why:
                raise Bloqueo(f"Read de `{relative}`: {why}")
            return
        if _protected(relative, SOLO_ARGUMENTO):
            raise Bloqueo(f"Read de `{relative}`: contenido del legacy o de la evidencia cruda; no entra al contexto del agente")
        return
    if tool == "Grep":
        scope = relative if relative is not None else ""
        if relative is not None and (_protected(relative, SOLO_ARGUMENTO) or _under_legacy(relative)):
            raise Bloqueo(f"Grep en `{relative}`: devolvería líneas del legacy o de la evidencia cruda. Usa `pepper map` "
                          "o acota a docs/pepper/, pepper-out/<sid>/package/")
        if scope in ("", ".") and (_exists("legacy") or _exists("evidence")):
            raise Bloqueo("Grep sobre todo el workspace con legacy/ o evidence/ presentes: acota `path` (docs/pepper, "
                          "pepper-out/<sid>/package, pepper/)")
        # el patrón glob no puede meter una ruta protegida
        for extra in (payload.get("glob") or "", payload.get("pattern") or ""):
            if isinstance(extra, str) and ("legacy/" in extra or "evidence/" in extra):
                raise Bloqueo("Grep con un glob que entra a legacy/ o evidence/")
        return
    if tool == "Glob":
        return   # solo nombres


def _check_write(tool: str, payload: dict) -> None:
    target = payload.get("file_path") or payload.get("notebook_path") or ""
    relative = _relative(str(target)) if target else None
    if relative is None:
        return
    if _matches(relative, NUNCA_ESCRIBIR) or _under_legacy(relative):
        raise Bloqueo(f"{tool} en `{relative}`: el legacy es solo lectura y la autorización de datos la escribe una persona "
                      "con `pepper authorize` en su terminal")
    if _matches(relative, GUARDIA) and not DEV:
        raise Bloqueo(f"{tool} en `{relative}`: el guardia de datos no se edita desde el agente "
                      "(quien desarrolla PEPPER lanza Claude Code con PEPPER_GUARDIA_DEV=1)")


def check(tool: str, payload: dict) -> None:
    if tool == "Bash":
        _check_bash(str(payload.get("command") or ""))
    elif tool in ("Read", "Grep", "Glob"):
        _check_read(tool, payload)
    elif tool in ("Edit", "MultiEdit", "Write", "NotebookEdit"):
        _check_write(tool, payload)


def main() -> int:
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
        tool = str(event.get("tool_name") or "")
        payload = event.get("tool_input") or {}
        if not isinstance(payload, dict):
            payload = {}
        check(tool, payload)
        return 0
    except Bloqueo as why:
        print(f"guardia de datos de PEPPER: bloqueado — {why}", file=sys.stderr)
        return 2
    except Exception as error:  # noqa: BLE001 — fail-closed: un guardia que se cae no deja pasar
        print(f"guardia de datos de PEPPER: error interno ({type(error).__name__}: {error}); se bloquea por seguridad", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
