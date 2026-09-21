"""Mecanismos de `pepper map` para artefactos Grails (Groovy compilado, sin fuente).

Grails no declara rutas con anotaciones: las acciones de un controlador son closures
(`def list = { … }`, Grails 1.x) o métodos (`def list() { … }`, Grails 2+) y la URL
sale por convención `/{controlador}/{acción}`; `UrlMappings.groovy` agrega o
reescribe rutas; los jobs de Quartz llevan su cron en `Config.groovy` o en un bloque
`triggers`. Todo eso es bytecode dentro del WAR y aquí se lee con el lector de
`groovyconfig`. El perfil dice dónde buscar (raíz de clases, paquetes, patrones);
este módulo sabe cómo compila Groovy.

Tres mecanismos:

  groovy_config_values       Config/DataSource → jobs (cron, enabled) y notas de configuración
  groovy_controller_actions  controladores → acciones → rutas por convención, con allowedMethods
  groovy_url_mappings        UrlMappings → rutas declaradas (con sus plantillas)
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pepper.inspect import groovyconfig as gc
from pepper.inspect import jvm

_SECRET_KEY_RE = re.compile(r"(?i)pass|pwd|psw|secret|token|credencial|api.?key|_key$|\.key$|salt|seed")
_URL_CRED_RE = re.compile(r"(?i)(://[^\s/:@]+:)[^\s@]+@")
_GROOVY_INTERNAL = re.compile(r"^(?:\$|super\$|this\$|get|set|is)[A-Z$]|^(?:getMetaClass|setMetaClass|invokeMethod|getProperty|setProperty|<init>|<clinit>|__\$swapInit|main|run|doCall|call)$")


def _lower_first(name: str) -> str:
    return name[:1].lower() + name[1:] if name else name


def _read_all(artifact: Path, spec: Dict[str, Any], tools: Dict[str, Any], mechanism: str,
              report: Any, class_filter: Optional[str] = None) -> Tuple[List[Tuple[str, Path]], Dict[str, str]]:
    """Clases del artefacto que casan `class_filter` (regex sobre el fqn), con su javap -p -c."""
    if not tools.get("javap"):
        report.gap(f"{mechanism}: falta `javap` (JDK) en PATH; no se leyó el bytecode")
        return [], {}
    if not (artifact.is_file() and artifact.suffix.lower() in (".war", ".jar", ".ear", ".zip")):
        report.gap(f"{mechanism}: el artefacto no es un archivo zip/WAR")
        return [], {}
    with tempfile.TemporaryDirectory() as tmp:
        classes = jvm.collect_classes(artifact, spec.get("class_root", "WEB-INF/classes"),
                                      spec.get("package_prefixes", []), False, Path(tmp))
        if class_filter:
            classes = [(fqn, root) for fqn, root in classes if re.search(class_filter, fqn)]
        if not classes:
            report.gap(f"{mechanism}: ninguna clase casa {class_filter!r} bajo '{spec.get('class_root', 'WEB-INF/classes')}' en {artifact.name}")
            return [], {}
        outputs, notes = jvm.javap_outputs(tools, classes, ["-p", "-c"])
        report.notes.extend(f"{mechanism}: {n}" for n in notes)
    missing = [fqn for fqn, _ in classes if fqn not in outputs]
    if missing:
        shown = ", ".join(missing[:5]) + (f" … y {len(missing) - 5} más" if len(missing) > 5 else "")
        report.gap(f"{mechanism}: {len(missing)} de {len(classes)} clases no se pudieron leer con javap: {shown}")
    return classes, outputs


def _resolve(value: Any, config: Dict[str, Any]) -> Any:
    """Un `Ref` a la configuración (`config.openboxes.jobs.x.cronExpression`) → su valor, si existe."""
    if not isinstance(value, gc.Ref):
        return value
    path = value.path
    for prefix in ("config.", "grailsApplication.config.", "ConfigurationHolder.config.", "CH.config."):
        if path.startswith(prefix):
            path = path[len(prefix):]
            break
    if path in config:
        return config[path]
    tail = [k for k in config if k.endswith("." + path)]
    return config[tail[0]] if len(tail) == 1 else value


def _clean_value(key: str, value: Any) -> str:
    if _SECRET_KEY_RE.search(key):
        return "[REDACTADO]"
    text = str(value)
    text = _URL_CRED_RE.sub(r"\1[REDACTADO]@", text)
    return text[:120]


def extract_config_values(artifact: Path, spec: Dict[str, Any], report: Any, tools: Dict[str, Any]) -> None:
    """Config/DataSource compilados → jobs con cron y notas; los job classes con `triggers` completan la lista."""
    scripts: List[str] = spec.get("scripts") or ["Config", "DataSource"]
    job_key_re = re.compile(spec.get("job_key_pattern") or r"(?i)cron")
    note_re = re.compile(spec.get("note_key_pattern") or r"(?i)url$|host|server|smtp|ldap|locations|\.enabled$|active$")
    job_class_re = spec.get("job_class_pattern")
    max_notes = int(spec.get("max_notes", 60))
    filters = [rf"^(?:{re.escape(s)})(?:\$|$)" for s in scripts]
    if job_class_re:
        filters.append(job_class_re.rstrip("$") + r"(?:\$|$)")   # también sus closures (`triggers`)
    classes, outputs = _read_all(artifact, spec, tools, "groovy_config_values", report, "|".join(filters))
    if not outputs:
        return

    config: Dict[str, Any] = {}
    per_script: Dict[str, Dict[str, Any]] = {}
    for script in scripts:
        values = gc.read_config(outputs, script)
        if values:
            per_script[script] = values
            config.update(values)
    if not per_script:
        report.gap(f"groovy_config_values: ninguno de {', '.join(scripts)} se pudo reconstruir del bytecode")
        return

    covered_keys = set()   # prefijos de Config que ya cubrió el bloque `triggers` de una clase de job
    if job_class_re:
        for fqn, _ in classes:
            if not re.search(job_class_re, fqn) or "$" in fqn:
                continue
            root = gc.read_class(fqn, outputs.get(fqn, ""))
            simple = fqn.rsplit(".", 1)[-1]
            for child, field_name in root.children.items():
                if field_name != "triggers" or child not in outputs:
                    continue
                trig = gc.read_class(child, outputs[child])
                for method, pairs in trig.maps:
                    if method not in ("cron", "simple") and "cronExpression" not in pairs:
                        continue
                    raw = pairs.get("cronExpression")
                    schedule = _resolve(raw, config)
                    detail = f"trigger {pairs.get('name', method)}"
                    if isinstance(raw, gc.Ref):
                        key = re.sub(r"^(?:config|grailsApplication\.config|ConfigurationHolder\.config|CH\.config)\.", "", raw.path)
                        prefix = key.rsplit(".", 1)[0]
                        covered_keys.add(prefix)
                        enabled = config.get(prefix + ".enabled")
                        detail += f" · Config: {key}" + (f" · enabled={str(enabled).lower()}" if enabled is not None else "")
                    if schedule is None and method == "simple":
                        schedule = f"cada {pairs.get('repeatInterval', '?')} ms"
                    report.jobs.append({"name": simple, "schedule": str(schedule) if schedule is not None else "(no literal)",
                                        "detail": detail, "evidence": f"{simple}.class (bytecode, triggers)"})
    jobs_seen = {(j["name"], j["schedule"]) for j in report.jobs}
    for script, values in per_script.items():
        for key, value in sorted(values.items()):
            if job_key_re.search(key) and value is not None and not isinstance(value, dict):
                name = key.rsplit(".", 1)[0] if "." in key else key
                if name in covered_keys:
                    continue
                enabled = values.get(name + ".enabled")
                job = {"name": name, "schedule": str(_resolve(value, config)),
                       "evidence": f"{script}.class (bytecode, groovy_config_values)"}
                if enabled is not None:
                    job["detail"] = f"enabled={str(enabled).lower()}"
                if (name, job["schedule"]) not in jobs_seen:
                    jobs_seen.add((name, job["schedule"]))
                    report.jobs.append(job)
    notes = 0
    for script, values in per_script.items():
        for key, value in sorted(values.items()):
            if notes >= max_notes:
                break
            if not note_re.search(key) or isinstance(value, dict):
                continue
            if value is None or (isinstance(value, gc.Ref)):
                continue
            report.notes.append(f"config {script}: {key} = {_clean_value(key, value)}")
            notes += 1



def _public_action_methods(javap_out: str) -> List[str]:
    """Grails 2+: `def list() { }` es un método público sin argumentos que devuelve Object."""
    names: List[str] = []
    for line in javap_out.splitlines():
        m = re.match(r"^  public (?:java\.lang\.Object|void|[\w.$]+) (\w+)\(\);$", line)
        if m and not _GROOVY_INTERNAL.match(m.group(1)) and m.group(1) not in names:
            names.append(m.group(1))
    return names


def extract_controller_actions(artifact: Path, spec: Dict[str, Any], report: Any, tools: Dict[str, Any]) -> None:
    """Controladores → acciones (closures asignadas en el constructor; opcionalmente métodos) → rutas."""
    class_re = spec.get("class_pattern") or r"Controller$"
    suffix = spec.get("strip_suffix", "Controller")
    template = spec.get("route_template", "/{controller}/{action}")
    rest_re = spec.get("rest_class_pattern")
    classes, outputs = _read_all(artifact, spec, tools, "groovy_controller_actions", report, class_re + r"(?:\$|$)")
    if not outputs:
        return
    controllers = 0
    for fqn, _ in classes:
        if "$" in fqn or not re.search(class_re, fqn):
            continue
        out = outputs.get(fqn)
        if not out:
            continue
        read = gc.read_class(fqn, out)
        simple = fqn.rsplit(".", 1)[-1]
        actions = [name for child, name in read.children.items()
                   if name and child.startswith(fqn + "$_closure") and not name.startswith("$")]
        if spec.get("methods_as_actions"):
            for name in _public_action_methods(out):
                if name not in actions:
                    actions.append(name)
        if not actions:
            continue
        controllers += 1
        allowed = read.fields.get("allowedMethods") or {}
        controller = _lower_first(simple[:-len(suffix)] if suffix and simple.endswith(suffix) else simple)
        kind = "rest_endpoint" if rest_re and re.search(rest_re, fqn) else "http_route"
        for action in actions:
            verb = allowed.get(action)
            verbs = [verb] if isinstance(verb, str) else (list(verb) if isinstance(verb, (list, tuple)) else [""])
            for http in verbs:
                report.entrypoints.append({
                    "kind": kind, "method": str(http or "").upper(),
                    "path": template.replace("{controller}", controller).replace("{action}", action),
                    "handler": f"{simple}.{action}",
                    "evidence": f"{simple}.class (bytecode, closure/método de acción)",
                })
    if not controllers:
        report.gap(f"groovy_controller_actions: ninguna clase que case {class_re!r} tiene acciones reconocibles")


_MAPPING_KEYS = {"controller", "action", "view", "uri", "namespace", "parseRequest", "redirect", "plugin"}


def extract_url_mappings(artifact: Path, spec: Dict[str, Any], report: Any, tools: Dict[str, Any]) -> None:
    """UrlMappings: cada `"/ruta/$var"(controller: …, action: …)` o con bloque `{ controller = … }`."""
    class_re = spec.get("class_pattern") or r"^UrlMappings$"
    classes, outputs = _read_all(artifact, spec, tools, "groovy_url_mappings", report, class_re.rstrip("$") + r"(?:\$|$)")
    if not outputs:
        return
    roots = [fqn for fqn, _ in classes if "$" not in fqn and re.search(class_re, fqn)]
    if not roots:
        report.gap(f"groovy_url_mappings: ninguna clase casa {class_re!r}")
        return
    root = roots[0]
    reads = {fqn: gc.read_class(fqn, out) for fqn, out in outputs.items() if fqn == root or fqn.startswith(root + "$")}
    mapping_closures = [child for child, name in reads[root].children.items() if child in reads]
    if not mapping_closures:
        report.gap(f"groovy_url_mappings: {root} no tiene un bloque de mapeos reconocible")
        return
    routes: List[Dict[str, Any]] = []

    def flush(path: Optional[str], attrs: Dict[str, Any]) -> None:
        if not path:
            return
        controller = attrs.get("controller")
        action = attrs.get("action")
        handler = ""
        if isinstance(controller, str):
            handler = controller + (f".{action}" if isinstance(action, str) else "")
        elif attrs.get("view"):
            handler = f"vista {attrs['view']}"
        elif attrs.get("uri"):
            handler = f"→ {attrs['uri']}"
        verbs: List[str] = [""]
        if isinstance(action, dict):
            verbs = list(action.keys())
        for verb in verbs:
            act = action[verb] if isinstance(action, dict) else action
            h = handler if not isinstance(action, dict) else f"{controller}.{act}" if isinstance(controller, str) else str(act)
            routes.append({"kind": "rest_endpoint" if path.startswith("/api") else "http_route",
                           "method": str(verb).upper(), "path": path, "handler": h,
                           "evidence": f"{root}.class (bytecode, UrlMappings)"})

    for closure in mapping_closures:
        read = reads[closure]
        # Un mapeo es todo lo que pasa entre dos invocaciones: el bytecode construye los
        # argumentos (mapa, bloque) y el nombre (la ruta) en el orden que le conviene al
        # compilador, así que la ruta puede aparecer antes o después de sus atributos. La
        # ruta es la GString de la ventana o, si no hay, la primera cadena con forma de ruta
        # (o un código HTTP) que ningún mapa consumió como clave o valor.
        gstrings: List[str] = []
        strings: List[str] = []
        attrs: Dict[str, Any] = {}
        consumed: set = set()

        def close() -> None:
            path = gstrings[-1] if gstrings else next(
                (t for t in strings if t not in consumed and (t.startswith("/") or (t.isdigit() and len(t) == 3))), None)
            if path and path.isdigit():
                path = f"HTTP {path}"
            flush(path, dict(attrs))
            gstrings.clear(); strings.clear(); attrs.clear(); consumed.clear()

        for kind, payload in read.events:
            if kind == "gstring":
                gstrings.append(payload)
            elif kind == "string" and isinstance(payload, str):
                strings.append(payload)
            elif kind == "map":
                for k, v in payload.items():
                    consumed.add(k)
                    if isinstance(v, str):
                        consumed.add(v)
                    if k in _MAPPING_KEYS:
                        attrs[k] = v
            elif kind == "child" and payload in reads:
                for prefix, key, value in reads[payload].assignments:
                    if key in _MAPPING_KEYS:
                        attrs[key] = value
            elif kind == "invoke":
                close()
        close()
    seen = set()
    for route in routes:
        key = (route["method"], route["path"], route["handler"])
        if key not in seen:
            seen.add(key)
            report.entrypoints.append(route)
    if not routes:
        report.gap(f"groovy_url_mappings: {root} no produjo ninguna ruta")
