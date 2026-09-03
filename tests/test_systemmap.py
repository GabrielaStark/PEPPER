"""`pepper map`: extracción exhaustiva de la superficie de un artefacto.

Hermético: un WAR sintético (zip) y herramientas de mentira (javap, pg_restore,
docker) inyectadas, para probar sin JDK ni Postgres y sin salir de la máquina.
"""

import json
import os
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pepper.inspect.systemmap import build_map, coverage  # noqa: E402
from pepper.validate import validate_instance  # noqa: E402

# javap de mentira: devuelve rutas para *Controller y un job para *Schedule.
FAKE_JAVAP = r'''#!/usr/bin/env python3
import sys
fqn = sys.argv[-1]
name = fqn.split(".")[-1]
if name.endswith("Controller"):
    print("  public mx.Resp obtenerGeneros();")
    print("        org.springframework.web.bind.annotation.GetMapping(")
    print('          value=["/obtenerGeneros"]')
    print("  public void guardar();")
    print("        org.springframework.web.bind.annotation.PostMapping(")
    print('          value=["/guardar"]')
    # anotación de CLASE (indentación menor): la ruta base, no un endpoint
    print("    org.springframework.web.bind.annotation.RequestMapping(")
    print('      value=["/api/rest"]')
elif name.endswith("Schedule"):
    print("  public void run();")
    print("    org.springframework.scheduling.annotation.Scheduled(")
    print('          cron="0 0 0 * * *"')
    print('          cron="0 0 0 * * *"')
'''

# pg_restore -l de mentira: una TOC con servidor foráneo, trigger y conteos.
FAKE_PG_RESTORE = r'''#!/usr/bin/env python3
print(";  toc de mentira")
print(";     dbname: base_origen")
print(";     Dumped from database version: 10.6")
print(";     Dumped by pg_dump version: 17.10")
print("2580; 1417 128087 SERVER - svr_externo dueno")
print("10; 1259 100 TABLE public trabajador dueno")
print("11; 1259 101 TABLE public empresa dueno")
print("12; 1259 102 VIEW public vw_x dueno")
print("13; 1255 103 FUNCTION public fn_folio() dueno")
print("14; 1259 104 SEQUENCE public seq_x dueno")
print("20; 1260 200 TRIGGER public trabajador dueno")
'''


def _make_tool(dirpath, name, body):
    p = Path(dirpath) / name
    p.write_text(body, encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def _make_war(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("WEB-INF/classes/app/CatalogosController.class", b"\xca\xfe\xba\xbe fake")
        z.writestr("WEB-INF/classes/cron/SyncSchedule.class", b"\xca\xfe\xba\xbe fake")
        z.writestr("WEB-INF/classes/application-prod.yml",
                   "spring:\n  datasource:\n    url: jdbc:postgresql://10.0.0.9/db\n"
                   "sideco:\n  restUrl: http://10.0.0.5:8080/\n"
                   "bus:\n  url: https://bus.institucion.example/\n"
                   "  password: secreta123\n")
        z.writestr("WEB-INF/classes/vista.xhtml",
                   '<html><script src="https://cdn.jsdelivr.net/x.js"></script>'
                   '<a href="https://externo.institucion.example/api">x</a></html>')


EXTRACTORS = [
    {"mechanism": "jvm_route_annotations", "class_root": "WEB-INF/classes",
     "package_prefixes": ["app/", "cron/"]},
    {"mechanism": "config_hosts",
     "config_patterns": [r"application.*\.(yml|yaml)$"],
     "host_key_pattern": r"(?i)(url|host|bus|sideco)"},
    {"mechanism": "archive_url_scan",
     "member_patterns": [r"\.(yml|xhtml|class)$"],
     "exclude_host_patterns": [r"cdn\.", r"jsdelivr"],
     "kind_hints": {"bus": [r"bus\."], "rest": [r"\d+\.\d+\.\d+\.\d+"]}},
    {"mechanism": "db_dump_toc"},
]


class SystemMapTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.war = self.dir / "app.war"
        _make_war(self.war)
        self.dump = self.dir / "backup.dump"
        self.dump.write_bytes(b"PGDMP fake")
        self.tools = {
            "javap": _make_tool(self.dir, "javap", FAKE_JAVAP),
            "pg_restore": _make_tool(self.dir, "pg_restore", FAKE_PG_RESTORE),
        }

    def tearDown(self):
        self._tmp.cleanup()

    def _map(self, tools=None):
        return build_map(self.war, EXTRACTORS, "perfil-test", dump=self.dump,
                         tools=self.tools if tools is None else tools)

    def test_superficie_sin_extractor_es_hueco_declarado_y_valida_contra_el_contrato(self):
        # ningún mecanismo del perfil enumera roles: el mapa NO puede decirse completo (D23)
        m = self._map()
        self.assertFalse(m["complete"])
        self.assertEqual(len(m["coverage_gaps"]), 1, m["coverage_gaps"])
        self.assertIn("roles", m["coverage_gaps"][0])
        self.assertEqual(m["roles"], [])
        try:
            errors = validate_instance(m, "system-map")
        except ImportError:
            self.skipTest("jsonschema no instalado")
        self.assertEqual(errors, [], errors)

    def test_rutas_del_bytecode_con_la_ruta_base_de_la_clase(self):
        # Regresión: la @RequestMapping de CLASE es el prefijo, no un endpoint.
        # Antes salía un endpoint fantasma "/api/rest" y los demás sin prefijo.
        m = self._map()
        paths = {(e["method"], e["path"]) for e in m["entrypoints"]}
        self.assertIn(("GET", "/api/rest/obtenerGeneros"), paths)
        self.assertIn(("POST", "/api/rest/guardar"), paths)
        self.assertNotIn(("", "/api/rest"), paths, "la ruta base no es un endpoint por sí sola")

    def test_cobertura_de_jobs_con_firma_declarada(self):
        # Un job sin log propio solo se delata por sus consultas: el perfil declara
        # con qué regex reconocerlo (le pasó a RevisionCitasSchedule en la corrida real).
        extractors = [dict(e) for e in EXTRACTORS]
        extractors[0]["job_signatures"] = {"SyncSchedule": r"sincronizando catalogos"}
        m = build_map(self.war, extractors, "perfil-test", dump=self.dump, tools=self.tools)
        self.assertEqual(m["jobs"][0]["signature"], r"sincronizando catalogos")
        ev = self.dir / "ev"
        (ev / "containers").mkdir(parents=True)
        (ev / "containers" / "app.log").write_text("18:00 INFO sincronizando catalogos ok\n", encoding="utf-8")
        cov = coverage(m, [], evidence_dir=ev)
        self.assertTrue(cov["jobs_measurable"])
        self.assertEqual(cov["jobs_observed"], 1)

    def test_cobertura_de_jobs_sin_firma_no_miente(self):
        m = self._map()  # sin job_signatures
        cov = coverage(m, [])
        self.assertFalse(cov["jobs_measurable"])
        self.assertIsNone(cov["jobs_observed"], "sin firma se declara no medible, nunca 0 observados")

    def test_cobertura_de_dependencias_por_el_stub(self):
        m = self._map()
        ev = self.dir / "ev2"
        (ev / "containers").mkdir(parents=True)
        (ev / "containers" / "stub.log").write_text(
            '{"host": "bus.institucion.example:443", "path": "/x"}\n', encoding="utf-8")
        cov = coverage(m, [], evidence_dir=ev)
        self.assertIn("bus.institucion.example", cov["dependencies_confirmed"])
        self.assertEqual(cov["dependencies_observed"], 1)

    def test_jobs_deduplicados(self):
        m = self._map()
        # el cron aparece dos veces en el bytecode; debe quedar UNO
        syncs = [j for j in m["jobs"] if j["name"] == "SyncSchedule"]
        self.assertEqual(len(syncs), 1)
        self.assertEqual(syncs[0]["schedule"], "0 0 0 * * *")

    def test_dependencias_externas_sin_cdn(self):
        m = self._map()
        hosts = {d["target"] for d in m["external_dependencies"]}
        self.assertIn("bus.institucion.example", hosts)
        self.assertIn("externo.institucion.example", hosts)
        self.assertNotIn("cdn.jsdelivr.net", hosts)  # CDN de front excluido
        kinds = {d["target"]: d["kind"] for d in m["external_dependencies"]}
        self.assertEqual(kinds["bus.institucion.example"], "bus")

    def test_config_hosts_no_expone_secretos(self):
        m = self._map()
        blob = json.dumps(m, ensure_ascii=False)
        self.assertNotIn("secreta123", blob)  # la línea password no se registra
        self.assertTrue(any("restUrl" in n or "bus" in n for n in m["notes"]))

    def test_inventario_de_datos_y_servidor_foraneo(self):
        m = self._map()
        kinds = [(d["kind"], d.get("name")) for d in m["data_stores"]]
        self.assertIn(("foreign_server", "svr_externo"), kinds)
        summ = {d["name"]: d.get("count") for d in m["data_stores"] if d["kind"] == "summary"}
        self.assertEqual(summ.get("table"), 2)

    def test_cabecera_del_respaldo_llega_al_mapa(self):
        # base de origen y versión del servidor: lo que delata discrepancias con NOTAS.md
        m = self._map()
        notas = [n for n in m["notes"] if n.startswith("respaldo ")]
        self.assertTrue(any("base de origen: base_origen" in n for n in notas), notas)
        self.assertTrue(any("versión del servidor de origen: 10.6" in n for n in notas), notas)

    def test_fail_honest_sin_herramientas(self):
        m = self._map(tools={})  # sin javap ni pg_restore ni docker
        self.assertFalse(m["complete"])
        self.assertTrue(any("javap" in g for g in m["coverage_gaps"]))
        self.assertTrue(any("db_dump_toc" in g for g in m["coverage_gaps"]))

    def test_cobertura_observado_vs_total(self):
        m = self._map()
        cov = coverage(m, observed_paths=["/api/rest/guardar", "/otra?x=1"])
        self.assertEqual(cov["routes_observed"], 1)
        self.assertGreater(cov["routes_total"], 1)
        self.assertIn("POST /api/rest/guardar", cov["observed"])
        self.assertTrue(any("obtenerGeneros" in r for r in cov["not_observed"]))


if __name__ == "__main__":
    unittest.main()
