"""`schemas/extractors.schema.json`: el contrato de extractors.json, y `pepper map` lo exige antes de correr.

Hasta la auditoría 2026-09-29 extractors.json no tenía schema: una clave mal escrita
(`member_pattern`, `patterns`) caía en un default en silencio y el mapa salía "completo"
con lo que el default alcanzara. El perfil fatjar traía justo una clave así.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pepper.inspect import readers  # noqa: E402
from pepper.validate import guess_schema, load_schema, validate_file, validate_instance  # noqa: E402

try:
    import jsonschema  # noqa: F401
except ImportError:  # pragma: no cover
    jsonschema = None


@unittest.skipIf(jsonschema is None, "jsonschema no instalado")
class ContratoDeExtractoresTest(unittest.TestCase):
    def test_se_reconoce_por_nombre_de_archivo(self):
        self.assertEqual(guess_schema(Path("profiles/x/extractors.json")), "extractors")

    def test_los_extractors_json_del_repo_validan(self):
        archivos = sorted((ROOT / "profiles").glob("*/extractors.json"))
        self.assertTrue(archivos)
        for path in archivos:
            self.assertEqual(validate_file(path), [], path)

    def test_el_contrato_cubre_exactamente_los_mecanismos_registrados(self):
        schema = load_schema("extractors")
        enum = schema["properties"]["extractors"]["items"]["properties"]["mechanism"]["enum"]
        self.assertEqual(set(enum), set(readers.MECHANISMS), "un mecanismo registrado sin contrato, o al revés")
        self.assertEqual(set(schema["$defs"]), set(readers.MECHANISMS))

    def test_una_clave_mal_escrita_se_nombra(self):
        errores = validate_instance({"extractors": [
            {"mechanism": "view_templates", "member_pattern": [r"\.gsp$"]},
            {"mechanism": "config_hosts", "config_patterns": ["x"], "patterns": ["y"]},
        ]}, "extractors")
        self.assertEqual(len(errores), 2, errores)
        self.assertIn("extractors/0", errores[0])
        self.assertIn("'member_pattern'", errores[0])
        self.assertIn("extractors/1", errores[1])
        self.assertIn("'patterns'", errores[1])

    def test_un_mecanismo_desconocido_y_un_tipo_equivocado_no_validan(self):
        errores = validate_instance({"extractors": [{"mechanism": "lector_inventado"}]}, "extractors")
        self.assertTrue(any("lector_inventado" in e for e in errores), errores)
        errores = validate_instance({"extractors": [{"mechanism": "pg_dump_custom", "catalog_max_rows": "300"}]}, "extractors")
        self.assertTrue(any("catalog_max_rows" in e for e in errores), errores)
        errores = validate_instance({"extractors": [{"mechanism": "regex_extractor", "surface": "screens",
                                                     "member_patterns": ["x"], "pattern": "(?P<path>x)"}]}, "extractors")
        self.assertTrue(any("surface" in e for e in errores), errores)
        errores = validate_instance({"extractors": [{"mechanism": "regex_extractor", "surface": "jobs", "pattern": "(?P<name>x)"}]}, "extractors")
        self.assertTrue(any("member_patterns" in e for e in errores), "member_patterns es obligatorio en regex_extractor")
        self.assertEqual(validate_instance({"extractors": []}, "extractors") != [], True, "una lista vacía no es un perfil con extractores")

    def test_pepper_map_valida_antes_de_correr_y_dice_que_clave_esta_mal(self):
        origen = ROOT / "profiles" / "java-springboot-jsf-postgres"
        with tempfile.TemporaryDirectory() as tmp:
            perfil = Path(tmp) / "perfil-roto"
            perfil.mkdir()
            shutil.copy2(origen / "profile.json", perfil / "profile.json")
            (perfil / "extractors.json").write_text(json.dumps({"extractors": [
                {"mechanism": "view_templates", "member_pattern": [r"\.xhtml$"]}]}), encoding="utf-8")
            war = Path(tmp) / "app.war"
            with zipfile.ZipFile(war, "w") as z:
                z.writestr("WEB-INF/classes/x.txt", "x")
            out = subprocess.run([sys.executable, "-m", "pepper", "map", str(war), "--profile", str(perfil),
                                  "--out", str(Path(tmp) / "docs" / "system-map.json")],
                                 capture_output=True, text=True, cwd=str(ROOT))
            self.assertEqual(out.returncode, 2, out.stderr)
            self.assertIn("extractors.schema.json", out.stderr)
            self.assertIn("'member_pattern'", out.stderr)
            self.assertFalse((Path(tmp) / "docs" / "system-map.json").exists(), "con el contrato roto no se escribe mapa")


if __name__ == "__main__":
    unittest.main()
