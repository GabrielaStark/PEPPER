"""Lector de configuración Groovy COMPILADA: el DSL de ConfigSlurper (Grails 1.x/2.x) sin fuente.

Un WAR de Grails no trae `DataSource.groovy` ni `Config.groovy`: trae sus clases
compiladas (`DataSource.class`, `DataSource$_run_closure3_closure6_closure10.class`…).
Ahí está el ambiente que el artefacto espera — la URL del datasource de production,
el usuario, los cron de los jobs — y este módulo lo reconstruye leyendo la salida de
`javap -p -c` de esas clases. Sin ejecutar nada.

Lo que el bytecode de Groovy 1.7–2.x deja ver, y en qué se apoya la lectura:

  · Cada bloque `nombre { … }` es una clase interna `X$_run_closureN` (o
    `X$__clinit__closureN` si es un campo estático). El NOMBRE del bloque no está en
    la clase hija: está en la tabla de call sites de la clase madre
    (`$createCallSiteArray_1`), y el índice que la madre carga (`ldc int N; aaload`)
    justo antes de `new X$_closureN` dice cuál.
  · `clave = valor` dentro de un bloque compila a `ldc valor; ldc "clave";
    ScriptBytecodeAdapter.setGroovyObjectProperty`. El valor va ANTES que la clave.
  · `a.b.c = valor` en el nivel del script compila a `ldc valor` y una cadena de
    `callGetProperty` (a, b) antes de `ldc "c"; setProperty`.
  · `[k: v, …]` compila a `ldc k; ldc v; …; createMap`.
  · Las cadenas con `$variable` son `GStringImpl`: las partes literales van en orden
    y cada variable es un `callGetProperty` en medio.

Todo lo que no sea literal (una llamada, una referencia a otra configuración) se
conserva como referencia `${ruta.punteada}` en vez de inventarle valor.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


_LDC_STR = re.compile(r"\bldc2?_?w?\s+#\d+\s+// String (.*)$")
_LDC_INT = re.compile(r"\bldc2?_?w?\s+#\d+\s+// (?:int|long|float|double) (-?[\d.]+)l?$")
_NEW_CLASS = re.compile(r"\bnew\s+#\d+\s+// class ([\w/$]+)$")
_PUTSTATIC = re.compile(r"\bputstatic\s+#\d+\s+// Field ([\w/$]+\.)?(\w+):")
_PUTFIELD = re.compile(r"\bputfield\s+#\d+\s+// Field ([\w/$]+\.)?(\w+):")
_GETSTATIC_BOOL = re.compile(r"\bgetstatic\s+#\d+\s+// Field java/lang/Boolean\.(TRUE|FALSE):")
_CALLSITE = re.compile(r"\binvokeinterface\s+#\d+,\s*\d+\s+// InterfaceMethod org/codehaus/groovy/runtime/callsite/CallSite\.(\w+):")
_SET_PROPERTY = re.compile(r"ScriptBytecodeAdapter\.(setGroovyObjectProperty|setProperty|setGroovyObjectField|setField):")
_CREATE_MAP = re.compile(r"ScriptBytecodeAdapter\.createMap:")
_GSTRING_INIT = re.compile(r"GStringImpl\.\"<init>\":")
_GSTRING_NEW = re.compile(r"\bnew\s+#\d+\s+// class org/codehaus/groovy/runtime/GStringImpl$")
_ACONST_NULL = re.compile(r"\baconst_null$")
_INVOKE_DYNAMIC = re.compile(r"ScriptBytecodeAdapter\.(invokeMethodN|invokeMethodOnCurrentN|invokeMethodOnSuperN|invokeMethod0|invokeMethodOnCurrent0|invokeNewN):")
_CLOSURE_SUFFIX = re.compile(r"_(?:run_|_clinit__|\w+_)?closure\d+$")


@dataclass
class Ref:
    """Un valor que no es literal: la ruta que el código lee (p. ej. config.openboxes.jobs.x.cron)."""
    path: str

    def __str__(self) -> str:
        return "${" + self.path + "}"


@dataclass
class ClassRead:
    """Lo que se sacó de UNA clase: sus call sites, sus hijos y sus asignaciones."""
    fqn: str
    callsites: List[str] = field(default_factory=list)
    children: Dict[str, str] = field(default_factory=dict)      # clase hija → nombre del bloque
    assignments: List[Tuple[List[str], str, Any]] = field(default_factory=list)  # (prefijo, clave, valor)
    maps: List[Tuple[Optional[str], Dict[str, Any]]] = field(default_factory=list)  # (método que lo recibe, {k: v})
    gstrings: List[str] = field(default_factory=list)            # cadenas con variables, ya reconstruidas
    fields: Dict[str, Any] = field(default_factory=dict)         # campos a los que se asignó un mapa literal
    events: List[Tuple[str, Any]] = field(default_factory=list)  # en orden: string, gstring, map, child, assign


def split_methods(javap_out: str) -> Dict[str, List[str]]:
    """{nombre-de-método: líneas de su cuerpo}; un nombre repetido (sobrecargas) se concatena.

    Un header de javap va con dos espacios y termina en `;`: `  public java.lang.Object doCall(java.lang.Object);`.
    El inicializador estático sale como `  static {};`."""
    methods: Dict[str, List[str]] = {}
    current: Optional[str] = None
    for line in javap_out.splitlines():
        if line.startswith("  ") and not line.startswith("   ") and line.rstrip().endswith(";"):
            stripped = line.strip()
            if stripped == "static {};":
                current = "<clinit>"
            elif "(" in stripped:
                current = stripped.split("(", 1)[0].split()[-1]
            else:
                current = None    # un campo
                continue
            methods.setdefault(current, [])
            continue
        if current is not None and line.startswith("    "):
            methods[current].append(line)
    return methods


def _callsites(methods: Dict[str, List[str]]) -> List[str]:
    """La tabla `$createCallSiteArray_1`: índice → nombre del método/propiedad llamado."""
    names: Dict[int, str] = {}
    body = [line for name, lines in methods.items() if name.startswith("$createCallSiteArray") for line in lines]
    pending: Optional[int] = None
    for line in body:
        m = _LDC_INT.search(line)
        if m and "." not in m.group(1):
            pending = int(m.group(1))
            continue
        m = _LDC_STR.search(line)
        if m and pending is not None:
            names[pending] = m.group(1)
            pending = None
    if not names:
        return []
    return [names.get(i, "") for i in range(max(names) + 1)]


def _parent_fqn(fqn: str) -> Optional[str]:
    """`Config$_run_closure5_closure20` → `Config$_run_closure5`; `Config$_run_closure5` → `Config`."""
    if "$" not in fqn:
        return None
    head, _, tail = fqn.rpartition("$")
    if not re.search(r"_closure\d+$", tail):
        return None
    remainder = re.sub(r"_closure\d+$", "", tail)
    if "closure" not in remainder:
        return head          # `_run`, `__clinit__`, `_<método>`: el padre es la clase del script
    return f"{head}${remainder}"


_GETSTATIC_CONST = re.compile(r"\bgetstatic\s+#\d+\s+// Field (?:[\w/$]+\.)?(\$const\$\d+):")
_PUTSTATIC_CONST = re.compile(r"\bputstatic\s+#\d+\s+// Field (?:[\w/$]+\.)?(\$const\$\d+):")


def _constants(lines: List[str]) -> Dict[str, Any]:
    """Groovy guarda los números literales en campos `$const$N` que llena `<clinit>`."""
    consts: Dict[str, Any] = {}
    pending: Optional[str] = None
    for line in lines:
        m = _LDC_INT.search(line)
        if m:
            pending = m.group(1)
            continue
        m = _PUTSTATIC_CONST.search(line)
        if m and pending is not None:
            try:
                consts[m.group(1)] = int(pending) if "." not in pending else float(pending)
            except ValueError:
                consts[m.group(1)] = pending
            pending = None
    return consts


def read_class(fqn: str, javap_out: str) -> ClassRead:
    """Una pasada lineal por método (`run`, `doCall`, `<init>`, `<clinit>`): literales, call sites,
    hijos y asignaciones. Los literales no sobreviven de un método a otro."""
    methods = split_methods(javap_out)
    read = ClassRead(fqn=fqn, callsites=_callsites(methods))
    consts = _constants(methods.get("<clinit>", []))
    # javap nombra al constructor con el nombre de la clase (`public a.b.C();`), no `<init>`
    if fqn in methods and "<init>" not in methods:
        methods["<init>"] = methods[fqn]
    for name in ("run", "doCall", "<init>", "<clinit>"):
        if methods.get(name):
            _read_method(read, methods[name], consts)
    return read


class _Chain(Ref):
    """Una cadena de propiedades convertida en literal; `before_key` marca que la cortó la clave."""
    before_key = False


def _read_method(read: ClassRead, lines: List[str], consts: Dict[str, Any]) -> None:
    fqn = read.fqn
    literals: List[Any] = []           # literales en orden (str, int, bool, Ref, None)
    prop_chain: List[str] = []         # callGetProperty acumulados desde el último corte
    site_stack: List[int] = []         # índices de call site cargados y aún no invocados
    pending_int: Optional[str] = None
    in_gstring = 0
    gstring_parts: List[str] = []
    gstring_vars: List[str] = []
    last_child: Optional[str] = None
    last_map: Optional[int] = None

    def site_name(index: Optional[int]) -> str:
        return read.callsites[index] if index is not None and 0 <= index < len(read.callsites) else ""

    def flush_chain(before_key: bool = False) -> None:
        if prop_chain:
            ref = _Chain(".".join(prop_chain))
            ref.before_key = before_key
            literals.append(ref)
            prop_chain.clear()

    for line in lines:
        m = _LDC_STR.search(line)
        if m:
            text = m.group(1)
            if in_gstring:
                gstring_parts.append(text)
            else:
                flush_chain(before_key=True)
                literals.append(text)
                read.events.append(("string", text))
            pending_int = None
            continue
        m = _LDC_INT.search(line)
        if m:
            pending_int = m.group(1)
            continue
        if pending_int is not None and re.search(r"\baaload$", line):
            if "." not in pending_int:
                site_stack.append(int(pending_int))
            pending_int = None
            continue
        if pending_int is not None and re.search(r"(Integer|Long|Double|Float|Short|Byte)\.valueOf:", line):
            try:
                literals.append(int(pending_int) if "." not in pending_int else float(pending_int))
            except ValueError:
                literals.append(pending_int)
            pending_int = None
            continue
        if pending_int is not None and not re.search(r"\b(aload|dup|iconst|bipush|sipush|getstatic)", line):
            pending_int = None
        m = _GETSTATIC_CONST.search(line)
        if m and m.group(1) in consts:
            literals.append(consts[m.group(1)])
            continue
        m = _GETSTATIC_BOOL.search(line)
        if m:
            literals.append(m.group(1) == "TRUE")
            continue
        if _ACONST_NULL.search(line):
            literals.append(None)
            continue
        if _GSTRING_NEW.search(line):
            in_gstring += 1
            gstring_parts, gstring_vars = [], []
            continue
        if in_gstring and _GSTRING_INIT.search(line):
            in_gstring -= 1
            text = ""
            for i, part in enumerate(gstring_parts):
                text += part
                if i < len(gstring_vars):
                    text += "{" + gstring_vars[i] + "}"
            for extra in gstring_vars[len(gstring_parts):]:
                text += "{" + extra + "}"
            read.gstrings.append(text)
            read.events.append(("gstring", text))
            literals.append(text)
            prop_chain.clear()
            continue
        m = _CALLSITE.search(line)
        if m:
            kind = m.group(1)
            name = site_name(site_stack.pop() if site_stack else None)
            if kind in ("callGetProperty", "callGroovyObjectGetProperty", "callGetPropertySafe"):
                if in_gstring:
                    gstring_vars.append(name)
                elif name:
                    prop_chain.append(name)
            elif kind in ("call", "callCurrent", "callStatic", "callConstructor", "callSafe"):
                if last_map is not None and last_map == len(read.maps) - 1 and name:
                    method, pairs = read.maps[last_map]
                    if method is None:
                        read.maps[last_map] = (name, pairs)
                    last_map = None
                # una llamada consume sus argumentos: los literales que quedaban eran de ella
                if not in_gstring:
                    literals.clear()
                if prop_chain and not in_gstring:
                    literals.append(Ref(".".join(prop_chain) + "." + name + "()"))
                    prop_chain.clear()
                read.events.append(("invoke", name))
            continue
        if _INVOKE_DYNAMIC.search(line):
            # `"/ruta/$id"(…)`: el nombre del método es una expresión; la llamada cierra el mapeo
            literals.clear()
            read.events.append(("invoke", ""))
            continue
        m = _NEW_CLASS.search(line)
        if m:
            child = m.group(1).replace("/", ".")
            if child.startswith(fqn.split("$")[0] + "$") and "closure" in child and child != fqn:
                read.children[child] = site_name(site_stack[-1] if site_stack else None)
                read.events.append(("child", child))
                last_child = child
            continue
        m = _PUTSTATIC.search(line) or _PUTFIELD.search(line)
        if m:
            if last_child and not read.children.get(last_child):
                read.children[last_child] = m.group(2)
            elif literals and isinstance(literals[-1], dict):
                read.fields[m.group(2)] = literals[-1]
            last_child = None
            literals.clear()
            prop_chain.clear()
            continue
        if _CREATE_MAP.search(line):
            flush_chain()
            items = literals[:]
            literals.clear()
            if len(items) % 2 == 1:
                items = items[1:]
            pairs: Dict[str, Any] = {}
            for k, v in zip(items[0::2], items[1::2]):
                if isinstance(k, str):
                    pairs[k] = v
            read.maps.append((None, pairs))
            read.events.append(("map", pairs))
            last_map = len(read.maps) - 1
            literals.append(pairs)
            continue
        m = _SET_PROPERTY.search(line)
        if m:
            flush_chain(before_key=True)
            key = literals.pop() if literals and isinstance(literals[-1], str) else None
            if key is None:
                literals.clear()
                continue
            prefix: List[str] = []
            if m.group(1) in ("setProperty", "setField") and literals and isinstance(literals[-1], _Chain) \
                    and literals[-1].before_key and len(literals) >= 2:
                prefix = literals.pop().path.split(".")
            value = literals.pop() if literals else None
            if isinstance(value, dict) and last_map is not None and last_map < len(read.maps):
                read.maps[last_map] = (key, read.maps[last_map][1])
            read.assignments.append((prefix, key, value))
            read.events.append(("assign", (prefix, key, value)))
            literals.clear()
            continue


def read_config(outputs: Dict[str, str], root_fqn: str) -> Dict[str, Any]:
    """{ruta.punteada: valor} de un script de configuración y todas sus closures.

    `outputs` es {fqn: salida de javap -p -c}. Las clases que no descienden de
    `root_fqn` se ignoran. Un valor no literal queda como `Ref`."""
    reads: Dict[str, ClassRead] = {}
    for fqn, out in outputs.items():
        if fqn == root_fqn or fqn.startswith(root_fqn + "$"):
            reads[fqn] = read_class(fqn, out)

    names: Dict[str, str] = {}   # clase → nombre de bloque
    for read in reads.values():
        for child, name in read.children.items():
            if name:
                names[child] = name

    def path_of(fqn: str) -> List[str]:
        parts: List[str] = []
        current: Optional[str] = fqn
        while current and current != root_fqn:
            name = names.get(current)
            if name:
                parts.append(name)
            current = _parent_fqn(current)
        return list(reversed(parts))

    values: Dict[str, Any] = {}
    for fqn, read in reads.items():
        base = path_of(fqn)
        for prefix, key, value in read.assignments:
            values[".".join(base + prefix + [key])] = value
        for method, pairs in read.maps:
            if method:
                continue
            for k, v in pairs.items():
                values.setdefault(".".join(base + [k]), v)
    return values


def environments(values: Dict[str, Any], env_key: str = "environments") -> Dict[str, Dict[str, Any]]:
    """{entorno: {clave: valor}} con lo del nivel raíz como base: development/test/production…"""
    base = {k: v for k, v in values.items() if not k.startswith(env_key + ".")}
    envs: Dict[str, Dict[str, Any]] = {}
    for key, value in values.items():
        if key.startswith(env_key + "."):
            rest = key[len(env_key) + 1:]
            env, _, sub = rest.partition(".")
            envs.setdefault(env, dict(base))[sub] = value
    return envs
