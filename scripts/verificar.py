#!/usr/bin/env python3
"""Auto-verificación de PEPPER — el Regression Shield del propio framework.

Valida todo lo verificable por máquina en el repo:
  1. Frontmatters YAML de agentes, skills y comandos (parsean; name = archivo o
     carpeta; skills declaradas existen; argument-hint es string). Con pyyaml si
     está instalado; si no, con un parser mínimo suficiente para este repo.
  2. Fences de código balanceados en todos los .md.
  3. Links internos de markdown resuelven (archivo y ancla).
  4. Nombres de comandos/agentes/skills citados en prosa existen en disco.
  5. Los scripts Python (núcleo, scripts, tests) compilan.
  6. Los contratos son JSON Schema válidos y las instancias del repo validan.
  7. Cada `python3 -m pepper <comando> --bandera` citado en la documentación existe en el CLI
     (comando y banderas), y cada comando del CLI está en REFERENCIA.md.
  8. La tabla de perfiles (PERFILES.md, profiles/README.md) coincide con `profiles/` en id y estado.
  9. Rutas canónicas: `explore.json` vive en `pepper-out/`, nunca se cita en `docs/pepper/`.

Los puntos 7–9 existen porque la documentación contradecía al código en verde (auditoría 2026-09-29):
un comando en CLAUDE.md mandaba explore.json a docs/pepper/, la tabla de perfiles listaba 2 de 4 y
TROUBLESHOOTING describía un fallback que el código ya no tenía.

Uso: python3 scripts/verificar.py   →   exit 0 = verde.
"""
import argparse
import ast
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # sin pyyaml, un parser mínimo cubre los frontmatters de este repo
    yaml = None

RAIZ = Path(__file__).resolve().parent.parent


class FrontmatterError(ValueError):
    pass


def _sin_comillas(valor):
    valor = valor.strip()
    if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "\"'":
        return valor[1:-1]
    return valor


def parse_frontmatter(texto):
    """Frontmatter YAML → dict. Con pyyaml si está; si no, `clave: valor` y listas `- item`."""
    if yaml is not None:
        try:
            return yaml.safe_load(texto)
        except yaml.YAMLError as e:
            raise FrontmatterError(str(e).splitlines()[0])
    datos, clave_lista = {}, None
    for linea in texto.splitlines():
        if not linea.strip():
            continue
        if linea.lstrip().startswith("- ") and clave_lista:
            datos[clave_lista].append(_sin_comillas(linea.lstrip()[2:]))
            continue
        if ":" not in linea or linea.startswith((" ", "\t")):
            raise FrontmatterError(f"línea no reconocida: {linea!r}")
        clave, _, valor = linea.partition(":")
        clave = clave.strip()
        if valor.strip():
            datos[clave], clave_lista = _sin_comillas(valor), None
        else:
            datos[clave], clave_lista = [], clave
    return datos
sys.path.insert(0, str(RAIZ))
ERRORES = []
IGNORAR = {".git", "pepper-out", "__pycache__", "node_modules", "analysis", "legacy", "evidence", "worktrees"}
# El PRODUCTO del workspace (lo que PEPPER escribe sobre el legacy) no es la
# herramienta: se verifica la herramienta, no los reportes de quien la usa.
IGNORAR_RUTAS = ("docs/pepper",)
# Tokens con forma de nombre que no son comandos/agentes/skills.
TOLERADOS = {"pepper-out", "pepper-discovery", "pepper-proxy", "pepper-stub", "pepper-home"}


def error(msg):
    ERRORES.append(msg)


def archivos_md():
    return [p for p in RAIZ.rglob("*.md")
            if not (set(p.parts) & IGNORAR)
            and not p.relative_to(RAIZ).as_posix().startswith(IGNORAR_RUTAS)]


def verifica_frontmatter():
    agentes = sorted((RAIZ / ".claude/agents").glob("*.md"))
    skills = sorted((RAIZ / ".claude/skills").glob("*/SKILL.md"))
    comandos = sorted((RAIZ / ".claude/commands").glob("*.md"))
    skills_en_disco = {p.parent.name for p in skills}

    for p in agentes + skills + comandos:
        rel = p.relative_to(RAIZ)
        texto = p.read_text(encoding="utf-8")
        if not texto.startswith("---"):
            error(f"{rel}: sin frontmatter YAML")
            continue
        try:
            fm = parse_frontmatter(texto.split("---")[1])
        except FrontmatterError as e:
            error(f"{rel}: YAML inválido — {e}")
            continue
        if not isinstance(fm, dict) or "description" not in fm:
            error(f"{rel}: frontmatter sin description")
            continue
        hint = fm.get("argument-hint")
        if hint is not None and not isinstance(hint, str):
            error(f"{rel}: argument-hint parsea como {type(hint).__name__} — va entre comillas")
        if p in agentes:
            if fm.get("name") != p.stem:
                error(f"{rel}: name '{fm.get('name')}' no coincide con el archivo '{p.stem}'")
            for s in fm.get("skills") or []:
                if s not in skills_en_disco:
                    error(f"{rel}: skill declarada '{s}' no existe en .claude/skills/")
        if p in skills and fm.get("name") != p.parent.name:
            error(f"{rel}: name '{fm.get('name')}' no coincide con la carpeta '{p.parent.name}'")


def verifica_fences():
    for p in archivos_md():
        n3 = n4 = 0
        for linea in p.read_text(encoding="utf-8").splitlines():
            if linea.startswith("````"):
                n4 += 1
            elif linea.startswith("```"):
                n3 += 1
        if n3 % 2 or n4 % 2:
            error(f"{p.relative_to(RAIZ)}: fences sin pareja (```={n3}, ````={n4})")


def slug(titulo):
    t = re.sub(r"[^\w\s-]", "", titulo.strip().lower())
    return t.replace(" ", "-")


def anclas_de(path):
    anclas = set()
    en_fence = False
    for linea in path.read_text(encoding="utf-8").splitlines():
        if linea.startswith("```"):
            en_fence = not en_fence
            continue
        m = re.match(r"#{1,6}\s+(.*)", linea)
        if m and not en_fence:
            anclas.add(slug(m.group(1)))
    return anclas


def verifica_links():
    for p in archivos_md():
        for m in re.finditer(r"\[[^\]]*\]\(([^)\s]+)\)", p.read_text(encoding="utf-8")):
            destino = m.group(1)
            if destino.startswith(("http://", "https://", "mailto:")):
                continue
            ruta, _, ancla = destino.partition("#")
            objetivo = (p.parent / ruta).resolve() if ruta else p
            if ruta and not objetivo.exists():
                error(f"{p.relative_to(RAIZ)}: link roto → {destino}")
                continue
            if ancla and objetivo.suffix == ".md" and ancla not in anclas_de(objetivo):
                error(f"{p.relative_to(RAIZ)}: ancla inexistente → {destino}")


def verifica_nombres():
    validos = (
        {p.stem for p in (RAIZ / ".claude/agents").glob("*.md")}
        | {p.parent.name for p in (RAIZ / ".claude/skills").glob("*/SKILL.md")}
        | {p.stem for p in (RAIZ / ".claude/commands").glob("*.md")}
        | TOLERADOS
    )
    patron = re.compile(
        r"\b((?:inspector|rehidratador|observador|descubridor|pepper|evidencia|perfil|discovery)-[a-z-]+)"
    )
    for p in archivos_md():
        for m in patron.finditer(p.read_text(encoding="utf-8")):
            token = m.group(1).rstrip("-")
            if token in validos:
                continue
            if any(v.startswith(token) for v in validos):
                continue
            error(f"{p.relative_to(RAIZ)}: nombre citado inexistente → '{token}'")


def verifica_scripts():
    rutas = list((RAIZ / "pepper").rglob("*.py")) + list((RAIZ / "scripts").glob("*.py")) + list((RAIZ / "tests").glob("*.py"))
    for p in rutas:
        try:
            ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        except SyntaxError as e:
            error(f"{p.relative_to(RAIZ)}: no compila — línea {e.lineno}: {e.msg}")


def verifica_contratos():
    try:
        import jsonschema
    except ImportError:
        error("falta jsonschema (pip install -r requirements-dev.txt): no se verificaron los contratos")
        return
    for p in sorted((RAIZ / "schemas").glob("*.schema.json")):
        try:
            jsonschema.Draft202012Validator.check_schema(json.loads(p.read_text(encoding="utf-8")))
        except (ValueError, jsonschema.SchemaError) as e:
            error(f"{p.relative_to(RAIZ)}: schema inválido — {str(e).splitlines()[0]}")
    from pepper.validate import validate_file

    instancias = (
        list((RAIZ / "profiles").glob("*/profile.json"))
        + list((RAIZ / "profiles").glob("*/parsers/*.json"))
        + list((RAIZ / "examples").rglob("session.json"))
        + list((RAIZ / "examples").rglob("funcional.json"))
    )
    for p in sorted(instancias):
        try:
            for msg in validate_file(p):
                error(f"{p.relative_to(RAIZ)}: no valida — {msg}")
        except ValueError as e:
            error(f"{p.relative_to(RAIZ)}: {e}")


_COMANDO_CITADO = re.compile(r"python3 -m pepper (\w[\w-]*)([^\n`]*)")
_BANDERA = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]*)")
_DOCS_CON_COMANDOS = ("README.md", "AGENTS.md", "CLAUDE.md", "docs/documentacion", ".claude", "profiles", "pepper/README.md",
                      "tests/README.md", "examples")


def _cli():
    sys.path.insert(0, str(RAIZ))
    from pepper.cli import build_parser

    parser = build_parser()
    comandos = {}
    for accion in parser._actions:
        if isinstance(accion, argparse._SubParsersAction):
            for nombre, sub in accion.choices.items():
                banderas = set()
                for a in sub._actions:
                    banderas.update(o for o in a.option_strings if o.startswith("--"))
                comandos[nombre] = banderas
    return comandos


def verifica_comandos():
    """Lo que la documentación dice que se puede teclear, se puede teclear."""
    try:
        comandos = _cli()
    except Exception as e:  # noqa: BLE001
        error(f"no pude construir el CLI para comparar la documentación: {e}")
        return
    referencia = (RAIZ / "docs/documentacion/REFERENCIA.md").read_text(encoding="utf-8")
    for nombre in sorted(comandos):
        if f"python3 -m pepper {nombre}" not in referencia:
            error(f"docs/documentacion/REFERENCIA.md: el comando `{nombre}` del CLI no aparece")
    for p in archivos_md():
        rel = p.relative_to(RAIZ).as_posix()
        if not rel.startswith(_DOCS_CON_COMANDOS):
            continue
        for m in _COMANDO_CITADO.finditer(p.read_text(encoding="utf-8")):
            nombre, resto = m.group(1), m.group(2)
            if nombre in ("…", "...") or nombre not in comandos:
                if nombre not in ("…", "..."):
                    error(f"{rel}: cita `pepper {nombre}`, que el CLI no tiene")
                continue
            for bandera in _BANDERA.findall(resto):
                if bandera not in comandos[nombre]:
                    error(f"{rel}: `pepper {nombre}` no tiene la bandera {bandera}")


_FILA_PERFIL = re.compile(r"^\|\s*\[?`?([a-z0-9-]+)`?(?:\]\([^)]*\))?\s*\|\s*\**`?(draft|validated)`?\**\s*\|", re.M)


def verifica_perfiles():
    """La tabla de perfiles de la documentación es la de disco: mismos ids, mismo estado."""
    en_disco = {}
    for perfil in sorted((RAIZ / "profiles").glob("*/profile.json")):
        try:
            datos = json.loads(perfil.read_text(encoding="utf-8"))
        except ValueError:
            continue
        en_disco[datos.get("id", perfil.parent.name)] = datos.get("status", "?")
    for doc in ("docs/documentacion/PERFILES.md", "profiles/README.md"):
        texto = (RAIZ / doc).read_text(encoding="utf-8")
        en_doc = {m.group(1): m.group(2) for m in _FILA_PERFIL.finditer(texto)}
        for pid, estado in en_disco.items():
            if pid not in en_doc:
                error(f"{doc}: el perfil `{pid}` existe en profiles/ y no está en la tabla")
            elif en_doc[pid] != estado:
                error(f"{doc}: el perfil `{pid}` está `{estado}` en disco y `{en_doc[pid]}` en la tabla")
        for pid in en_doc:
            if pid not in en_disco:
                error(f"{doc}: la tabla lista `{pid}`, que no existe en profiles/")


def verifica_rutas_canonicas():
    """explore.json lleva la contraseña de prueba: vive en pepper-out/, y ningún documento puede mandarlo a docs/pepper/."""
    for p in archivos_md():
        rel = p.relative_to(RAIZ).as_posix()
        for numero, linea in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if "docs/pepper/explore.json" in linea and "nunca" not in linea.lower() and "no " not in linea.lower():
                error(f"{rel}:{numero}: cita docs/pepper/explore.json; explore.json vive en pepper-out/")


def main():
    verifica_frontmatter()
    verifica_fences()
    verifica_links()
    verifica_nombres()
    verifica_scripts()
    verifica_contratos()
    verifica_comandos()
    verifica_perfiles()
    verifica_rutas_canonicas()
    if ERRORES:
        print(f"❌ verificar.py: {len(ERRORES)} problema(s)")
        for e in ERRORES:
            print(f"  - {e}")
        sys.exit(1)
    print("✅ PEPPER verificado: frontmatters, fences, links, nombres, scripts, contratos, comandos citados, "
          "tabla de perfiles y rutas canónicas en orden.")


if __name__ == "__main__":
    main()
