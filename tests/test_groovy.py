"""Groovy compilado: el lector de configuración (`groovyconfig`) y los mecanismos Grails de `pepper map`.

Hermético: la "salida de javap" se fabrica con un mini-DSL que emite las mismas líneas que
javap -p -c produce para Groovy 1.7–2.x (call sites, closures, setGroovyObjectProperty,
createMap, GStringImpl), y un javap de mentira las imprime por clase.
"""

import json
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pepper.inspect import groovyconfig as gc  # noqa: E402
from pepper.inspect.systemmap import build_map, coverage, route_pattern  # noqa: E402


# ------------------------------------------------------------ mini-DSL → texto javap

def klass(fqn, callsites=(), ctor=(), methods=None, fields=()):
    """Una clase como la imprime `javap -p -c`. `methods` = {nombre: [instrucciones]}."""
    simple = fqn.rsplit(".", 1)[-1]
    lines = [f"class {fqn} extends groovy.lang.Script {{"]
    for f in fields:
        lines.append(f"  private java.lang.Object {f};")
    lines += [f"  public {fqn}(java.lang.Object, java.lang.Object);", "    Code:"] + _code(ctor)
    for name, body in (methods or {}).items():
        header = "  static {};" if name == "<clinit>" else f"  public java.lang.Object {name}(java.lang.Object);"
        lines += [header, "    Code:"] + _code(body)
    lines += ["  private static void $createCallSiteArray_1(java.lang.String[]);", "    Code:"]
    for i, name in enumerate(callsites):
        lines += [f"       1: ldc           #1                  // int {i}", f"       3: ldc           #2                  // String {name}",
                  "       5: aastore"]
    lines.append("}")
    return "\n".join(lines)


def _code(instructions):
    out = []
    for ins in instructions:
        kind, _, arg = ins.partition(":")
        if kind == "S":
            out.append(f"      12: ldc           #49                 // String {arg}")
        elif kind == "I":          # carga del call site N (ldc int; aaload)
            out += [f"       5: ldc           #25                 // int {arg}", "       7: aaload"]
        elif kind == "INT":        # entero literal
            out += [f"       5: ldc           #25                 // int {arg}",
                    "       7: invokestatic  #86                 // Method java/lang/Integer.valueOf:(I)Ljava/lang/Integer;"]
        elif kind == "TRUE":
            out.append("       4: getstatic     #43                 // Field java/lang/Boolean.TRUE:Ljava/lang/Boolean;")
        elif kind == "FALSE":
            out.append("       4: getstatic     #43                 // Field java/lang/Boolean.FALSE:Ljava/lang/Boolean;")
        elif kind == "N":
            out.append(f"       9: new           #27                 // class {arg.replace('.', '/')}")
        elif kind == "SET":
            out.append("      14: invokestatic  #55                 // Method org/codehaus/groovy/runtime/ScriptBytecodeAdapter.setGroovyObjectProperty:(Ljava/lang/Object;Ljava/lang/Class;Lgroovy/lang/GroovyObject;Ljava/lang/String;)V")
        elif kind == "SETP":
            out.append("      14: invokestatic  #55                 // Method org/codehaus/groovy/runtime/ScriptBytecodeAdapter.setProperty:(Ljava/lang/Object;Ljava/lang/Class;Ljava/lang/Object;Ljava/lang/String;)V")
        elif kind == "GETP":
            out.append("      53: invokeinterface #47,  2           // InterfaceMethod org/codehaus/groovy/runtime/callsite/CallSite.callGetProperty:(Ljava/lang/Object;)Ljava/lang/Object;")
        elif kind == "CALL":
            out.append("      82: invokeinterface #57,  3           // InterfaceMethod org/codehaus/groovy/runtime/callsite/CallSite.callCurrent:(Lgroovy/lang/GroovyObject;Ljava/lang/Object;)Ljava/lang/Object;")
        elif kind == "INVOKE":
            out.append("      90: invokestatic  #99                 // Method org/codehaus/groovy/runtime/ScriptBytecodeAdapter.invokeMethodOnCurrentN:(Ljava/lang/Class;Lgroovy/lang/GroovyObject;Ljava/lang/String;[Ljava/lang/Object;)Ljava/lang/Object;")
        elif kind == "MAP":
            out.append("      79: invokestatic  #53                 // Method org/codehaus/groovy/runtime/ScriptBytecodeAdapter.createMap:([Ljava/lang/Object;)Ljava/util/Map;")
        elif kind == "PUTF":
            out.append(f"      20: putfield      #88                 // Field {arg}:Ljava/lang/Object;")
        elif kind == "PUTS":
            out.append(f"      45: putstatic     #260                // Field {arg}:Ljava/lang/Object;")
        elif kind == "GS":         # GString: partes separadas por | y variables como {nombre} → GETP en medio
            out.append("      10: new           #78                 // class org/codehaus/groovy/runtime/GStringImpl")
            parts = arg.split("|")
            for i, part in enumerate(parts):
                if i % 2 == 0:
                    out.append(f"      26: ldc           #82                 // String {part}")
                else:
                    out += [f"       5: ldc           #25                 // int {part}", "       7: aaload",
                            "      53: invokeinterface #47,  2           // InterfaceMethod org/codehaus/groovy/runtime/callsite/CallSite.callGetProperty:(Ljava/lang/Object;)Ljava/lang/Object;"]
            out.append("      34: invokespecial #85                 // Method org/codehaus/groovy/runtime/GStringImpl.\"<init>\":([Ljava/lang/Object;[Ljava/lang/String;)V")
        else:
            raise ValueError(ins)
    return out


# DataSource.groovy: dataSource { username = "root"; password = "s3" } + environments { development {…} production { dataSource { url = … } } }
DATASOURCE = {
    "DataSource": klass("DataSource", callsites=["setBinding", "dataSource", "environments"],
                        methods={"run": ["I:1", "N:DataSource$_run_closure1", "CALL", "I:2", "N:DataSource$_run_closure2", "CALL"]}),
    "DataSource$_run_closure1": klass("DataSource$_run_closure1", callsites=["doCall"],
                                      methods={"doCall": ["S:root", "S:username", "SET", "S:s3", "S:password", "SET", "INT:5", "S:maxPoolSize", "SET", "TRUE", "S:pooled", "SET"]}),
    "DataSource$_run_closure2": klass("DataSource$_run_closure2", callsites=["development", "production"],
                                      methods={"doCall": ["I:0", "N:DataSource$_run_closure2_closure3", "CALL", "I:1", "N:DataSource$_run_closure2_closure4", "CALL"]}),
    "DataSource$_run_closure2_closure3": klass("DataSource$_run_closure2_closure3", callsites=["dataSource"],
                                               methods={"doCall": ["I:0", "N:DataSource$_run_closure2_closure3_closure5", "CALL"]}),
    "DataSource$_run_closure2_closure3_closure5": klass("DataSource$_run_closure2_closure3_closure5",
                                                        methods={"doCall": ["S:jdbc:mysql://localhost:3306/app_dev", "S:url", "SET"]}),
    "DataSource$_run_closure2_closure4": klass("DataSource$_run_closure2_closure4", callsites=["dataSource"],
                                               methods={"doCall": ["I:0", "N:DataSource$_run_closure2_closure4_closure6", "CALL"]}),
    "DataSource$_run_closure2_closure4_closure6": klass("DataSource$_run_closure2_closure4_closure6",
                                                        methods={"doCall": ["S:jdbc:mysql://localhost:3306/app", "S:url", "SET"]}),
}

# Config.groovy: openboxes.jobs.limpieza.cronExpression = "0 */5 * * * ?" ; openboxes.jobs.limpieza.enabled = true ; mail.host = "smtp.correo.example"
CONFIG = {
    # los call sites de una cadena a.b.c van en la tabla del más externo al más interno (c, b, a)
    "Config": klass("Config", callsites=["setBinding", "mail", "limpieza", "jobs", "openboxes"],
                    methods={"run": ["S:0 */5 * * * ?", "I:2", "I:3", "I:4", "GETP", "GETP", "GETP", "S:cronExpression", "SETP",
                                     "TRUE", "I:2", "I:3", "I:4", "GETP", "GETP", "GETP", "S:enabled", "SETP",
                                     "I:1", "N:Config$_run_closure1", "CALL"]}),
    "Config$_run_closure1": klass("Config$_run_closure1", methods={"doCall": ["S:smtp.correo.example", "S:host", "SET", "S:secreta", "S:password", "SET"]}),
}

# LimpiezaJob: static triggers = { cron name: "limpiezaTrigger", cronExpression: config.openboxes.jobs.limpieza.cronExpression }
JOB = {
    "app.jobs.LimpiezaJob": klass("app.jobs.LimpiezaJob", methods={"<clinit>": ["N:app.jobs.LimpiezaJob$__clinit__closure1", "PUTS:triggers"]}),
    "app.jobs.LimpiezaJob$__clinit__closure1": klass(
        "app.jobs.LimpiezaJob$__clinit__closure1", callsites=["cron", "cronExpression", "limpieza", "jobs", "openboxes", "config"],
        methods={"doCall": ["I:0", "S:name", "S:limpiezaTrigger", "S:cronExpression", "I:1", "I:2", "I:3", "I:4", "I:5",
                            "GETP", "GETP", "GETP", "GETP", "GETP", "MAP", "CALL"]}),
}

# ProductoController: def list = {…}; def save = {…}; static allowedMethods = [save: "POST"]
CONTROLLER = {
    "app.ProductoController": klass("app.ProductoController", fields=["list", "save"],
                                    ctor=["N:app.ProductoController$_closure1", "PUTF:list", "N:app.ProductoController$_closure2", "PUTF:save"],
                                    methods={"<clinit>": ["S:save", "S:POST", "MAP", "PUTS:allowedMethods"]}),
}

# UrlMappings: "/$controller/$action?/$id?" {} ; "/api/productos/$id"(parseRequest: true) { controller = "productoApi"; action = [GET: "read"] } ; "500"(view: "/error")
URLMAPPINGS = {
    "UrlMappings": klass("UrlMappings", methods={"<clinit>": ["N:UrlMappings$__clinit__closure1", "PUTS:mappings"]}),
    "UrlMappings$__clinit__closure1": klass(
        "UrlMappings$__clinit__closure1", callsites=["controller", "action", "id"],
        methods={"doCall": ["GS:/|0|/|1|?/|2|?", "N:UrlMappings$__clinit__closure1_closure2", "INVOKE",
                            "S:parseRequest", "TRUE", "MAP", "N:UrlMappings$__clinit__closure1_closure3", "GS:/api/productos/|2|", "INVOKE",
                            "S:500", "S:view", "S:/error", "MAP", "INVOKE"]}),
    "UrlMappings$__clinit__closure1_closure2": klass("UrlMappings$__clinit__closure1_closure2", methods={"doCall": []}),
    "UrlMappings$__clinit__closure1_closure3": klass("UrlMappings$__clinit__closure1_closure3",
                                                     methods={"doCall": ["S:productoApi", "S:controller", "SET", "S:GET", "S:read", "MAP", "S:action", "SET"]}),
}

ALL = {**DATASOURCE, **CONFIG, **JOB, **CONTROLLER, **URLMAPPINGS}


class LectorTest(unittest.TestCase):
    def test_reconstruye_environments_y_valores(self):
        values = gc.read_config(DATASOURCE, "DataSource")
        self.assertEqual(values["dataSource.username"], "root")
        self.assertEqual(values["dataSource.password"], "s3")
        self.assertEqual(values["dataSource.maxPoolSize"], 5)
        self.assertIs(values["dataSource.pooled"], True)
        self.assertEqual(values["environments.production.dataSource.url"], "jdbc:mysql://localhost:3306/app")
        self.assertEqual(values["environments.development.dataSource.url"], "jdbc:mysql://localhost:3306/app_dev")
        envs = gc.environments(values)
        self.assertEqual(envs["production"]["dataSource.url"], "jdbc:mysql://localhost:3306/app")
        self.assertEqual(envs["production"]["dataSource.username"], "root", "lo del nivel raíz es la base de cada entorno")

    def test_rutas_punteadas_en_el_script_y_bloques(self):
        values = gc.read_config(CONFIG, "Config")
        self.assertEqual(values["openboxes.jobs.limpieza.cronExpression"], "0 */5 * * * ?")
        self.assertIs(values["openboxes.jobs.limpieza.enabled"], True)
        self.assertEqual(values["mail.host"], "smtp.correo.example")

    def test_un_valor_no_literal_queda_como_referencia(self):
        read = gc.read_class("app.jobs.LimpiezaJob$__clinit__closure1", JOB["app.jobs.LimpiezaJob$__clinit__closure1"])
        method, pairs = read.maps[0]
        self.assertEqual(method, "cron")
        self.assertEqual(pairs["name"], "limpiezaTrigger")
        self.assertEqual(str(pairs["cronExpression"]), "${config.openboxes.jobs.limpieza.cronExpression}")

    def test_constructor_closures_y_mapa_estatico(self):
        read = gc.read_class("app.ProductoController", CONTROLLER["app.ProductoController"])
        self.assertEqual(read.children, {"app.ProductoController$_closure1": "list", "app.ProductoController$_closure2": "save"})
        self.assertEqual(read.fields["allowedMethods"], {"save": "POST"})

    def test_gstring_con_variables(self):
        read = gc.read_class("UrlMappings$__clinit__closure1", URLMAPPINGS["UrlMappings$__clinit__closure1"])
        self.assertEqual(read.gstrings, ["/{controller}/{action}?/{id}?", "/api/productos/{id}"])


FAKE_JAVAP = r'''#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
classes = args[args.index("-classpath") + 2:]
canned = json.load(open(%r))
for fqn in classes:
    if fqn in canned:
        print(canned[fqn])
'''


class MecanismosTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        canned = root / "canned.json"
        canned.write_text(json.dumps(ALL), encoding="utf-8")
        javap = root / "javap"
        javap.write_text(FAKE_JAVAP % str(canned), encoding="utf-8")
        javap.chmod(javap.stat().st_mode | stat.S_IEXEC)
        self.tools = {"javap": str(javap)}
        self.war = root / "app.war"
        with zipfile.ZipFile(self.war, "w") as z:
            for fqn in ALL:
                z.writestr("WEB-INF/classes/" + fqn.replace(".", "/") + ".class", b"\xca\xfe\xba\xbe fake")

    def tearDown(self):
        self._tmp.cleanup()

    def _map(self, extractors):
        return build_map(self.war, extractors, "perfil", tools=self.tools)

    def test_config_values_da_jobs_con_cron_resuelto_y_notas_sin_secretos(self):
        m = self._map([{"mechanism": "groovy_config_values", "package_prefixes": ["Config", "app/jobs/"],
                        "scripts": ["Config"], "job_key_pattern": r"\.cronExpression$", "job_class_pattern": r"\.jobs\.\w+Job$",
                        "note_key_pattern": r"(?i)host$|password$"}])
        self.assertEqual(len(m["jobs"]), 1, m["jobs"])
        job = m["jobs"][0]
        self.assertEqual((job["name"], job["schedule"]), ("LimpiezaJob", "0 */5 * * * ?"), "el cron del trigger se resuelve desde Config")
        self.assertIn("enabled=true", job["detail"])
        notes = [n for n in m["notes"] if n.startswith("config")]
        self.assertTrue(any("mail.host = smtp.correo.example" in n for n in notes), notes)
        self.assertFalse(any("secreta" in n for n in notes), "una clave de configuración con password no viaja")

    def test_controller_actions_son_rutas_por_convencion(self):
        m = self._map([{"mechanism": "groovy_controller_actions", "package_prefixes": ["app/"], "class_pattern": "Controller$"}])
        routes = {(r["method"], r["path"]): r["handler"] for r in m["entrypoints"]}
        self.assertEqual(routes[("", "/producto/list")], "ProductoController.list")
        self.assertEqual(routes[("POST", "/producto/save")], "ProductoController.save", "allowedMethods da el verbo")

    def test_url_mappings_con_plantillas_bloques_y_verbos(self):
        m = self._map([{"mechanism": "groovy_url_mappings", "package_prefixes": ["UrlMappings"]}])
        routes = {(r["method"], r["path"]): r["handler"] for r in m["entrypoints"]}
        self.assertIn(("", "/{controller}/{action}?/{id}?"), routes)
        self.assertEqual(routes[("GET", "/api/productos/{id}")], "productoApi.read")
        self.assertEqual(routes[("", "HTTP 500")], "vista /error")
        self.assertFalse(any("groovy_url_mappings" in g for g in m["coverage_gaps"]), m["coverage_gaps"])

    def test_la_cobertura_casa_rutas_con_plantilla(self):
        m = self._map([{"mechanism": "groovy_url_mappings", "package_prefixes": ["UrlMappings"]}])
        cov = coverage(m, ["/producto/list", "/api/productos/7?x=1"])
        self.assertEqual(cov["routes_observed"], 2, cov)

    def test_route_pattern(self):
        self.assertTrue(route_pattern("/{controller}/{action}?/{id}?").match("/producto"))
        self.assertTrue(route_pattern("/stockMovement/{action}/{id}**?").match("/stockMovement/edit/9/extra"))
        self.assertFalse(route_pattern("/api/productos/{id}").match("/api/productos"))


if __name__ == "__main__":
    unittest.main()
