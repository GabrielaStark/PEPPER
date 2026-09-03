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
elif name.endswith("Schedule"):
    print("  public void run();")
    print("    org.springframework.scheduling.annotation.Scheduled(")
    print('          cron="0 0 0 * * *"')
    print('          cron="0 0 0 * * *"')
'''

# pg_restore -l de mentira: una TOC con servidor foráneo, trigger y conteos.
FAKE_PG_RESTORE = r'''#!/usr/bin/env python3
print(";  toc de mentira")
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

    def test_mapa_completo_valida_contra_el_contrato(self):
        m = self._map()
        self.assertTrue(m["complete"], m["coverage_gaps"])
        try:
            errors = validate_instance(m, "system-map")
        except ImportError:
            self.skipTest("jsonschema no instalado")
        self.assertEqual(errors, [], errors)

    def test_rutas_del_bytecode(self):
        m = self._map()
        paths = {(e["method"], e["path"]) for e in m["entrypoints"]}
        self.assertIn(("GET", "/obtenerGeneros"), paths)
        self.assertIn(("POST", "/guardar"), paths)
        self.assertTrue(any(e["kind"] == "http_route" for e in m["entrypoints"]))

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

    def test_fail_honest_sin_herramientas(self):
        m = self._map(tools={})  # sin javap ni pg_restore ni docker
        self.assertFalse(m["complete"])
        self.assertTrue(any("javap" in g for g in m["coverage_gaps"]))
        self.assertTrue(any("db_dump_toc" in g for g in m["coverage_gaps"]))

    def test_cobertura_observado_vs_total(self):
        m = self._map()
        cov = coverage(m, observed_paths=["/guardar", "/otra?x=1"])
        self.assertEqual(cov["routes_observed"], 1)
        self.assertGreater(cov["routes_total"], 1)
        self.assertIn("POST /guardar", cov["observed"])
        self.assertTrue(any("obtenerGeneros" in r for r in cov["not_observed"]))


if __name__ == "__main__":
    unittest.main()
