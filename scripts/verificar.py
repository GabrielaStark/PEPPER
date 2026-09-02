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

Uso: python3 scripts/verificar.py   →   exit 0 = verde.
"""
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
IGNORAR = {".git", "pepper-out", "__pycache__", "node_modules"}
# Tokens con forma de nombre que no son comandos/agentes/skills.
TOLERADOS = {"pepper-out", "pepper-discovery", "pepper-proxy"}


def error(msg):
    ERRORES.append(msg)


def archivos_md():
    return [p for p in RAIZ.rglob("*.md") if not (set(p.parts) & IGNORAR)]


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
        + list((RAIZ / "examples").rglob("runtime-discovery.json"))
    )
    for p in sorted(instancias):
        try:
            for msg in validate_file(p):
                error(f"{p.relative_to(RAIZ)}: no valida — {msg}")
        except ValueError as e:
            error(f"{p.relative_to(RAIZ)}: {e}")


def main():
    verifica_frontmatter()
    verifica_fences()
    verifica_links()
    verifica_nombres()
    verifica_scripts()
    verifica_contratos()
    if ERRORES:
        print(f"❌ verificar.py: {len(ERRORES)} problema(s)")
        for e in ERRORES:
            print(f"  - {e}")
        sys.exit(1)
    print("✅ PEPPER verificado: frontmatters, fences, links, nombres, scripts y contratos en orden.")


if __name__ == "__main__":
    main()
