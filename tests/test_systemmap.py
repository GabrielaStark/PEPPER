"""`pepper map`: todo lo que un sistema ES, sacado del artefacto y del respaldo.

Hermético: un WAR sintético (zip) con vistas, bundle y clases falsas; un javap de
mentira; y un respaldo en formato custom de pg_dump ESCRITO por el propio test
(cabecera, TOC y bloques zlib), para probar el lector sin PostgreSQL.
"""

import json
import stat
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pepper.inspect import pgdump  # noqa: E402
from pepper.inspect.systemmap import build_map, coverage, render_map  # noqa: E402
from pepper.validate import validate_instance  # noqa: E402

# javap de mentira: -v devuelve rutas/jobs; -c -constants devuelve métodos, constantes y cadenas.
FAKE_JAVAP = r'''#!/usr/bin/env python3
import sys
args = sys.argv[1:]
classes = args[args.index("-classpath") + 2:]
verbose = "-v" in args
for fqn in classes:
    name = fqn.split(".")[-1]
    print(f"public class {fqn} {{")
    if verbose:
        if name.endswith("Controller"):
            print("  public mx.Resp obtenerGeneros();")
            print("        org.springframework.web.bind.annotation.GetMapping(")
            print('          value=["/obtenerGeneros"]')
            print("  public void guardar();")
            print("        org.springframework.web.bind.annotation.PostMapping(")
            print('          value=["/guardar"]')
            print("    org.springframework.web.bind.annotation.RequestMapping(")
            print('      value=["/api/rest"]')
        elif name.endswith("Schedule"):
            print("  public void run();")
            print("    org.springframework.scheduling.annotation.Scheduled(")
            print('          cron="0 0 0 * * *"')
            print('          cron="0 0 0 * * *"')
    else:
        if name.endswith("Controller"):
            print("  public void guardar(javax.faces.event.ActionEvent);")
            print("  public java.lang.String getNombre();")
            print("  public void setNombre(java.lang.String);")
            print("    Code:")
            print("       5: ldc           #12                 // String Se debe registrar al menos un trabajador")
            print("       9: ldc           #13                 // String PENDIENTE")
            print("      12: ldc           #14                 // String password=secreta")
            print("      15: ldc           #15                 // String correo@persona.example")
        elif name.endswith("Constantes"):
            print("  public static final java.lang.String ESTATUS_ACTIVO = \"ACTIVO\";")
            print("  public static final long ID_ROL_ADMIN = 1l;")
            print("  private static final java.lang.String PASSWORD = \"nope\";")
        elif name.endswith("Schedule"):
            print("  public void run();")
            print("       3: ldc           #2                  // String Cancela las citas pendientes")
    print("}")
'''


def _make_tool(dirpath, name, body):
    p = Path(dirpath) / name
    p.write_text(body, encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def _make_war(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("WEB-INF/classes/app/controller/CatalogosController.class", b"\xca\xfe\xba\xbe fake")
        z.writestr("WEB-INF/classes/app/cron/SyncSchedule.class", b"\xca\xfe\xba\xbe fake")
        z.writestr("WEB-INF/classes/app/utils/Constantes.class", b"\xca\xfe\xba\xbe fake")
        z.writestr("WEB-INF/classes/application-prod.yml",
                   "spring:\n  datasource:\n    url: jdbc:postgresql://10.0.0.9/db\n"
                   "sideco:\n  restUrl: http://10.0.0.5:8080/\n"
                   "bus:\n  url: https://bus.institucion.example/\n"
                   "  password: secreta123\n")
        z.writestr("WEB-INF/classes/app/messages_es.properties",
                   "lbl.guardar=Guardar\nlbl.recepcion=Recepci\\u00f3n\nlbl.debeElegir=Debe elegir un procurador\n")
        z.writestr("Vista/Recepcion.xhtml",
                   '<html><h2>#{lbl[\'lbl.recepcion\']}</h2>'
                   '<p:outputLabel value="CURP:"/><p:outputLabel value="Nombre:"/>'
                   '<p:commandButton value="#{lbl[\'lbl.guardar\']}" actionListener="#{citaController.registraCita()}"/>'
                   '<p:commandButton value="Cancelar" action="#{citaController.cancelar}"/>'
                   '<p:inputText requiredMessage="Nombre es obligatorio"/>'
                   '<p:selectOneMenu validatorMessage="#{lbl[\'lbl.debeElegir\']}"/>'
                   '<p:panel rendered="#{loginBean.rolUsuario eq \'ADMIN\'}"/>'
                   '<ui:include src="/Vista/tabs.xhtml"/>'
                   '<script src="https://cdn.jsdelivr.net/x.js"></script>'
                   '<a href="https://externo.institucion.example/api">x</a></html>')
        z.writestr("WEB-INF/plantillas/Template.xhtml", "<html><h1>plantilla</h1></html>")


# ---------------------------------------------------------------- escritor de pg_dump custom

def _wint(value):
    sign = 1 if value < 0 else 0
    return bytes([sign]) + struct.pack("<i", abs(value))


def _wstr(value):
    if value is None:
        return _wint(-1)
    raw = value.encode("utf-8")
    return _wint(len(raw)) + raw


def _toc_entry(dump_id, desc, tag, defn, has_data, pos):
    out = _wint(dump_id) + _wint(1 if has_data else 0) + _wstr("1259") + _wstr(str(1000 + dump_id))
    out += _wstr(tag) + _wstr(desc) + _wint(1) + _wstr(defn) + _wstr("") + _wstr("")
    out += _wstr("public") + _wstr("") + _wstr("") + _wint(0) + _wstr("owner") + _wstr("false") + _wstr(None)
    out += bytes([2]) + struct.pack("<q", pos)  # K_OFFSET_POS_SET
    return out


def write_custom_dump(path, tables, functions=(), triggers=(), views=()):
    """Escribe un respaldo custom (formato 1.16, zlib) con las tablas dadas: {nombre: (ddl, filas)}."""
    entries = []
    dump_id = 1
    for name, (ddl, _rows) in tables.items():
        entries.append((dump_id, "TABLE", name, ddl, False)); dump_id += 1
    data_entries = []
    for name, (_ddl, rows) in tables.items():
        data_entries.append((dump_id, "TABLE DATA", name, "", True, rows)); dump_id += 1
    for name, body in functions:
        entries.append((dump_id, "FUNCTION", name, body, False)); dump_id += 1
    for name, body in triggers:
        entries.append((dump_id, "TRIGGER", name, body, False)); dump_id += 1
    for name, body in views:
        entries.append((dump_id, "VIEW", name, body, False)); dump_id += 1

    header = b"PGDMP" + bytes([1, 16, 0, 4, 8, 1, 1])  # versión, intsize, offsize, formato custom, gzip
    header += b"".join(_wint(v) for v in (0, 0, 0, 1, 0, 126, 0))
    header += _wstr("base_origen") + _wstr("10.6") + _wstr("17.10")
    total = len(entries) + len(data_entries)

    # Primero se calcula el tamaño del TOC para conocer las posiciones de los datos.
    def toc_bytes(positions):
        out = _wint(total)
        for e in entries:
            out += _toc_entry(e[0], e[1], e[2], e[3], e[4], 0)
        for e in data_entries:
            out += _toc_entry(e[0], e[1], e[2], e[3], e[4], positions.get(e[0], 0))
        return out

    blocks = []
    for e in data_entries:
        copy = "".join("\t".join("\\N" if c is None else str(c) for c in row) + "\n" for row in e[5]) + "\\.\n"
        z = zlib.compressobj()
        payload = z.compress(copy.encode("utf-8")) + z.flush()
        block = bytes([1]) + _wint(e[0])
        for i in range(0, len(payload), 1024):
            chunk = payload[i:i + 1024]
            block += _wint(len(chunk)) + chunk
        block += _wint(0)
        blocks.append((e[0], block))
    base = len(header) + len(toc_bytes({}))
    positions, offset = {}, base
    for dump_id_, block in blocks:
        positions[dump_id_] = offset
        offset += len(block)
    path.write_bytes(header + toc_bytes(positions) + b"".join(b for _, b in blocks))


TABLES = {
    "ctroles": ("CREATE TABLE public.ctroles (\n    llrol bigint NOT NULL,\n    boactivo boolean,\n    dsrol character varying(50)\n);",
                [[1, "t", "ADMIN"], [2, "t", "RECEPCION"], [3, "f", "TITULAR"]]),
    "ctparametros": ("CREATE TABLE public.ctparametros (\n    llparametro bigint,\n    dsclave character varying,\n    dsvalor character varying\n);",
                     [[1, "tiempoEspera", "45"], [2, "password", "S3cr3t0"], [3, "mail.smtp.user", "usuario@correo.example"]]),
    "enusuarios": ("CREATE TABLE public.enusuarios (\n    llusuario bigint,\n    dsnombre character varying,\n    dscorreo character varying\n);",
                   [[1, "Persona Real", "persona@correo.example"]]),
    "ctcita": ("CREATE TABLE public.ctcita (\n    llcita bigint NOT NULL,\n    dsestatus character varying(20),\n    comentarios text,\n    CONSTRAINT pk PRIMARY KEY (llcita)\n);",
               [[i, "ASIGNADA" if i % 10 else "TERMINADA", None] for i in range(1, 401)]),
}
FUNCTIONS = [("siat_sector()", "CREATE FUNCTION public.siat_sector() RETURNS trigger\n    LANGUAGE plpgsql\n    AS $$BEGIN NEW.dssector := 'Privado'; RETURN NEW; END;$$;")]
TRIGGERS = [("enempresa trg_sector", "CREATE TRIGGER trg_sector BEFORE INSERT OR UPDATE ON public.enempresa FOR EACH ROW EXECUTE PROCEDURE public.siat_sector();")]
VIEWS = [("vw_reportes", "CREATE VIEW public.vw_reportes AS SELECT 1;")]

EXTRACTORS = [
    {"mechanism": "jvm_route_annotations", "class_root": "WEB-INF/classes",
     "package_prefixes": ["controller/", "cron/"]},
    {"mechanism": "jvm_class_inventory", "class_root": "WEB-INF/classes",
     "package_prefixes": ["controller/", "cron/", "utils/"],
     "class_kinds": {"pantalla": r"\.controller\.", "job": r"\.cron\.", "constantes": "Constantes"}},
    {"mechanism": "view_templates", "member_patterns": [r"\.xhtml$"], "exclude_patterns": ["plantillas/"],
     "bundle_patterns": [r"messages.*\.properties$"],
     "bundle_ref_pattern": r"#\{lbl\['(?P<key>[^']+)'\]\}"},
    {"mechanism": "config_hosts",
     "config_patterns": [r"application.*\.(yml|yaml)$"],
     "host_key_pattern": r"(?i)(url|host|bus|sideco)"},
    {"mechanism": "archive_url_scan",
     "member_patterns": [r"\.(yml|xhtml|class)$"],
     "exclude_host_patterns": [r"cdn\.", r"jsdelivr"],
     "kind_hints": {"bus": [r"bus\."], "rest": [r"\d+\.\d+\.\d+\.\d+"]}},
    {"mechanism": "pg_dump_custom", "catalog_max_rows": 300,
     "catalog_include_patterns": ["^ct"], "state_column_pattern": r"(?i)estatus"},
]


class PgDumpReaderTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dump = Path(self._tmp.name) / "backup.dump"
        write_custom_dump(self.dump, TABLES, FUNCTIONS, TRIGGERS, VIEWS)

    def tearDown(self):
        self._tmp.cleanup()

    def test_lee_cabecera_toc_y_filas(self):
        info = pgdump.read_toc(self.dump)
        self.assertEqual(info.dbname, "base_origen")
        self.assertEqual(info.server_version, "10.6")
        self.assertEqual(info.compression, "gzip")
        self.assertEqual({e.tag for e in info.by_desc("TABLE")}, set(TABLES))
        rows = list(pgdump.iter_rows(info, info.table_data("ctroles")))
        self.assertEqual(rows, [["1", "t", "ADMIN"], ["2", "t", "RECEPCION"], ["3", "f", "TITULAR"]])
        self.assertEqual(pgdump.count_rows(info, info.table_data("ctcita")), 400)
        self.assertIsNone(list(pgdump.iter_rows(info, info.table_data("ctcita"), limit=1))[0][2], "\\N → None")

    def test_columnas_y_trigger(self):
        info = pgdump.read_toc(self.dump)
        cita = next(e for e in info.by_desc("TABLE") if e.tag == "ctcita")
        self.assertEqual(pgdump.table_columns(cita.defn), ["llcita", "dsestatus", "comentarios"])
        trg = info.by_desc("TRIGGER")[0]
        self.assertEqual(pgdump.trigger_targets(trg.defn),
                         {"event": "BEFORE INSERT OR UPDATE", "table": "enempresa", "function": "siat_sector"})

    def test_no_es_custom(self):
        other = Path(self._tmp.name) / "plain.sql"
        other.write_bytes(b"-- PostgreSQL database dump\n")
        self.assertFalse(pgdump.is_custom_dump(other))
        with self.assertRaises(ValueError):
            pgdump.read_toc(other)


class SystemMapTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.war = self.dir / "app.war"
        _make_war(self.war)
        self.dump = self.dir / "backup.dump"
        write_custom_dump(self.dump, TABLES, FUNCTIONS, TRIGGERS, VIEWS)
        self.tools = {"javap": _make_tool(self.dir, "javap", FAKE_JAVAP)}

    def tearDown(self):
        self._tmp.cleanup()

    def _map(self, tools=None, extractors=None):
        return build_map(self.war, extractors or EXTRACTORS, "perfil-test", dump=self.dump,
                         tools=self.tools if tools is None else tools)

    def test_mapa_completo_y_valida_contra_el_contrato(self):
        m = self._map()
        self.assertTrue(m["complete"], m["coverage_gaps"])
        self.assertNotIn("created", m, "sin timestamp: mismo artefacto → mismos bytes")
        try:
            errors = validate_instance(m, "system-map")
        except ImportError:
            self.skipTest("jsonschema no instalado")
        self.assertEqual(errors, [], errors)

    def test_superficie_sin_extractor_es_hueco_declarado(self):
        m = self._map(extractors=EXTRACTORS[:1])
        self.assertFalse(m["complete"])
        self.assertTrue(any(g.startswith("screens") for g in m["coverage_gaps"]), m["coverage_gaps"])
        self.assertTrue(any(g.startswith("catalogs") for g in m["coverage_gaps"]))

    def test_rutas_del_bytecode_con_la_ruta_base_de_la_clase(self):
        m = self._map()
        paths = {(e["method"], e["path"]) for e in m["entrypoints"]}
        self.assertIn(("GET", "/api/rest/obtenerGeneros"), paths)
        self.assertIn(("POST", "/api/rest/guardar"), paths)
        self.assertNotIn(("", "/api/rest"), paths, "la ruta base no es un endpoint por sí sola")

    def test_jobs_deduplicados(self):
        m = self._map()
        syncs = [j for j in m["jobs"] if j["name"] == "SyncSchedule"]
        self.assertEqual(len(syncs), 1)
        self.assertEqual(syncs[0]["schedule"], "0 0 0 * * *")

    def test_inventario_de_clases_sin_getters_ni_secretos(self):
        m = self._map()
        by_name = {c["name"].rsplit(".", 1)[-1]: c for c in m["classes"]}
        ctl = by_name["CatalogosController"]
        self.assertEqual(ctl["kind"], "pantalla")
        self.assertEqual(ctl["methods"], ["guardar(ActionEvent)"])
        self.assertIn("Se debe registrar al menos un trabajador", ctl["strings"])
        self.assertIn("PENDIENTE", ctl["strings"])
        self.assertFalse(any("secreta" in s for s in ctl["strings"]), "cadenas con pinta de credencial no viajan")
        self.assertFalse(any("@" in s for s in ctl["strings"]), "correos no viajan")
        const = by_name["Constantes"]
        self.assertEqual(const["constants"].get("ESTATUS_ACTIVO"), "ACTIVO")
        self.assertEqual(const["constants"].get("ID_ROL_ADMIN"), "1l")
        self.assertNotIn("PASSWORD", const["constants"])

    def test_pantallas_con_etiquetas_resueltas(self):
        m = self._map()
        [screen] = [s for s in m["screens"] if s["path"].endswith("Recepcion.xhtml")]
        self.assertEqual(screen["headings"], ["Recepción"])
        self.assertEqual(screen["fields"], ["CURP:", "Nombre:"])
        self.assertIn({"label": "Guardar", "action": "registraCita"}, screen["buttons"])
        self.assertIn({"label": "Cancelar", "action": "cancelar"}, screen["buttons"])
        self.assertEqual(screen["messages"], ["Nombre es obligatorio", "Debe elegir un procurador"])
        self.assertEqual(screen["conditions"], ["loginBean.rolUsuario eq 'ADMIN'"])
        self.assertEqual(screen["includes"], ["/Vista/tabs.xhtml"])
        self.assertFalse(any("Template" in s["path"] for s in m["screens"]), "las plantillas se excluyen")
        self.assertEqual(m["labels"], 3)

    def test_respaldo_tablas_catalogos_y_reglas_en_la_base(self):
        m = self._map()
        tables = {d["name"]: d for d in m["data_stores"] if d["kind"] == "table"}
        self.assertEqual(tables["ctcita"]["count"], 400)
        self.assertEqual(tables["ctcita"]["columns"], ["llcita", "dsestatus", "comentarios"])
        kinds = {(d["kind"], d["name"]) for d in m["data_stores"]}
        self.assertIn(("trigger", "enempresa trg_sector"), kinds)
        self.assertIn(("function", "siat_sector()"), kinds)
        self.assertIn(("view", "vw_reportes"), kinds)
        trg = next(d for d in m["data_stores"] if d["kind"] == "trigger")
        self.assertIn("BEFORE INSERT OR UPDATE en enempresa → siat_sector()", trg["detail"])
        self.assertIn("RETURN NEW", trg["definition"] + next(d for d in m["data_stores"] if d["kind"] == "function")["definition"])
        catalogs = {c["table"]: c for c in m["catalogs"]}
        self.assertIn("ctroles", catalogs)
        self.assertEqual(catalogs["ctroles"]["rows"][0], ["1", "t", "ADMIN"])
        self.assertNotIn("enusuarios", catalogs, "las tablas de personas no se vuelcan")
        self.assertNotIn("ctcita", catalogs, "una tabla grande no es catálogo")
        notas = " ".join(m["notes"])
        self.assertIn("base de origen: base_origen", notas)
        self.assertIn("versión del servidor de origen: 10.6", notas)

    def test_catalogos_redactan_secretos_y_datos_de_personas(self):
        m = self._map()
        params = next(c for c in m["catalogs"] if c["table"] == "ctparametros")
        rows = {r[1]: r[2] for r in params["rows"]}
        self.assertEqual(rows["tiempoEspera"], "45")
        self.assertEqual(rows["password"], "[REDACTADO]")
        self.assertEqual(rows["mail.smtp.user"], "[REDACTADO]")
        blob = json.dumps(m, ensure_ascii=False)
        for leak in ("S3cr3t0", "usuario@correo.example", "persona@correo.example", "Persona Real", "secreta123"):
            self.assertNotIn(leak, blob, leak)

    def test_distribuciones_de_columnas_de_estado(self):
        m = self._map()
        [dist] = [d for d in m["distributions"] if d["table"] == "ctcita"]
        self.assertEqual(dist["column"], "dsestatus")
        self.assertEqual(dist["total"], 400)
        self.assertEqual(dist["values"][0], {"value": "ASIGNADA", "count": 360})
        self.assertEqual(dist["values"][1], {"value": "TERMINADA", "count": 40})

    def test_dependencias_externas_sin_cdn_ni_secretos(self):
        m = self._map()
        hosts = {d["target"] for d in m["external_dependencies"]}
        self.assertIn("bus.institucion.example", hosts)
        self.assertIn("externo.institucion.example", hosts)
        self.assertNotIn("cdn.jsdelivr.net", hosts)
        self.assertNotIn("secreta123", json.dumps(m, ensure_ascii=False))

    def test_fail_honest_sin_herramientas_ni_respaldo(self):
        m = build_map(self.war, EXTRACTORS, "perfil-test", dump=None, tools={})
        self.assertFalse(m["complete"])
        self.assertTrue(any("javap" in g for g in m["coverage_gaps"]))
        self.assertTrue(any("pg_dump_custom" in g for g in m["coverage_gaps"]))

    def test_render_legible(self):
        m = self._map()
        files = render_map(m)
        self.assertEqual(set(files), {"surface.md", "db.md", "catalogs.md", "screens.md", "code.md"})
        self.assertIn("Guardar → `registraCita()`", files["screens.md"])
        self.assertIn("trg_sector", files["db.md"])
        self.assertIn("| 1 | t | ADMIN |", files["catalogs.md"])
        self.assertIn("ESTATUS_ACTIVO=ACTIVO", files["code.md"])

    def test_cobertura_observado_vs_total(self):
        m = self._map()
        cov = coverage(m, observed_paths=["/api/rest/guardar", "/otra?x=1"])
        self.assertEqual(cov["routes_observed"], 1)
        self.assertGreater(cov["routes_total"], 1)
        self.assertFalse(cov["jobs_measurable"])
        self.assertIsNone(cov["jobs_observed"], "sin firma se declara no medible, nunca 0 observados")


if __name__ == "__main__":
    unittest.main()
