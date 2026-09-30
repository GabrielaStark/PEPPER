"""El registro de lectores de `pepper map` (pepper/inspect/readers): UNA tabla, no tres.

Hasta la auditoría 2026-09-29 el despacho vivía en tres tablas hermanas de systemmap.py
(mecanismo → función, mecanismo → superficies, mecanismos del respaldo) y una función
`_groovy()` cableada; un mecanismo agregado a una y no a otra no contaba para la cobertura.
Estas pruebas fijan que el registro es la única fuente y que sigue siendo fail-honest.
"""

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pepper.inspect import readers  # noqa: E402
from pepper.inspect.systemmap import build_map, extractors_without_dump  # noqa: E402


class RegistroTest(unittest.TestCase):
    def test_cada_mecanismo_declara_como_corre_y_que_alimenta(self):
        self.assertTrue(readers.MECHANISMS, "el registro no puede estar vacío")
        for name, mechanism in readers.MECHANISMS.items():
            self.assertEqual(name, mechanism.name, "la llave de la tabla es el nombre del mecanismo")
            self.assertTrue(callable(mechanism.run), name)
            self.assertTrue(mechanism.description, f"{name} sin descripción")
            for surface in mechanism.surfaces:
                self.assertIn(surface, readers.SURFACES, f"{name} alimenta una superficie que el mapa no tiene")
            self.assertTrue(mechanism.surfaces or mechanism.surface_from_spec,
                            f"{name} no alimenta ninguna superficie: no contaría para la cobertura")

    def test_los_diez_mecanismos_del_nucleo_siguen_registrados(self):
        esperados = {"archive_url_scan", "config_hosts", "pg_dump_custom", "sql_dump", "jvm_route_annotations",
                     "jvm_class_inventory", "view_templates", "groovy_config_values", "groovy_controller_actions",
                     "groovy_url_mappings"}
        self.assertTrue(esperados <= set(readers.MECHANISMS), esperados - set(readers.MECHANISMS))

    def test_los_del_respaldo_y_los_de_javap_se_derivan_del_registro(self):
        self.assertEqual(set(readers.DUMP_MECHANISMS), {"pg_dump_custom", "sql_dump"})
        self.assertEqual({n for n, m in readers.MECHANISMS.items() if m.needs_dump}, set(readers.DUMP_MECHANISMS))
        con_javap = {n for n, m in readers.MECHANISMS.items() if m.needs_javap}
        self.assertEqual(con_javap, {"jvm_route_annotations", "jvm_class_inventory", "groovy_config_values",
                                     "groovy_controller_actions", "groovy_url_mappings"})
        extractores = [{"mechanism": "pg_dump_custom"}, {"mechanism": "view_templates"},
                       {"mechanism": "sql_dump"}, {"mechanism": "archive_url_scan"}]
        self.assertEqual([e["mechanism"] for e in extractors_without_dump(extractores)],
                         ["view_templates", "archive_url_scan"])

    def test_covers_devuelve_las_superficies_fijas(self):
        self.assertEqual(readers.MECHANISMS["jvm_route_annotations"].covers({}), ("entrypoints", "jobs"))
        self.assertEqual(readers.MECHANISMS["sql_dump"].covers({"cualquier": "cosa"}), ("data_stores", "catalogs"))

    def test_el_registro_se_importa_solo_sin_ciclo(self):
        # readers no importa systemmap al cargar (systemmap sí importa readers): en cualquier orden funciona
        import importlib
        import subprocess
        code = ("import pepper.inspect.readers as r; import pepper.inspect.systemmap as s; "
                "assert s.MECHANISMS is r.MECHANISMS; print(len(r.MECHANISMS))")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(int(out.stdout.strip()), len(readers.MECHANISMS))
        importlib.reload(readers)


class DespachoTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.war = Path(self._tmp.name) / "app.war"
        with zipfile.ZipFile(self.war, "w") as z:
            z.writestr("WEB-INF/classes/x.txt", "x")

    def tearDown(self):
        self._tmp.cleanup()

    def test_un_mecanismo_desconocido_es_hueco_declarado_y_nombra_los_registrados(self):
        mapa = build_map(self.war, [{"mechanism": "lector_inventado"}, {"mechanism": "archive_url_scan"}], "p", tools={})
        self.assertFalse(mapa["complete"])
        hueco = next(g for g in mapa["coverage_gaps"] if "lector_inventado" in g)
        self.assertIn("mecanismo desconocido", hueco)
        self.assertIn("archive_url_scan", hueco, "el hueco dice cuáles sí existen")
        # y no cubre ninguna superficie: las demás siguen declaradas como huecos
        self.assertTrue(any(g.startswith("screens") for g in mapa["coverage_gaps"]))

    def test_cada_mecanismo_registrado_corre_por_el_registro_sin_reventar(self):
        # sin javap ni respaldo: todos declaran su hueco por el camino normal, ninguno tira el mapa
        extractores = [{"mechanism": name} for name in readers.MECHANISMS]
        mapa = build_map(self.war, extractores, "p", dump=None, tools={})
        self.assertFalse(mapa["complete"])
        self.assertFalse(any("el extractor del perfil falló" in g for g in mapa["coverage_gaps"]), mapa["coverage_gaps"])


if __name__ == "__main__":
    unittest.main()
