"""El contrato del perfil sin campos muertos, y el casado explícito colector ↔ archivo capturado.

Auditoría 2026-09-29: `collectors[].method|location|enable`, `validation[]` y `rehydrate.steps`
no los leía nadie como datos — salvo `location`, que `pepper explore` usaba como PROSA para
adivinar qué parser normaliza cada log capturado. Ahora el colector declara `file`.
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pepper.explore import collector_source  # noqa: E402
from pepper.profiles import iter_profiles  # noqa: E402
from pepper.validate import validate_instance  # noqa: E402

try:
    import jsonschema  # noqa: F401
except ImportError:  # pragma: no cover
    jsonschema = None

MINIMO = {
    "schema_version": "0.1.0", "id": "perfil-prueba", "name": "x", "status": "draft",
    "detection": {"signals": [{"type": "extension", "pattern": "*.war"}]},
    "rehydrate": {"required_inputs": ["un WAR"]},
    "collectors": [{"source": "app", "file": "containers/app.log", "parser": "parsers/app.json"}],
}


@unittest.skipIf(jsonschema is None, "jsonschema no instalado")
class CamposMuertosTest(unittest.TestCase):
    def test_el_minimo_valida_y_validation_ya_no_es_obligatorio(self):
        self.assertEqual(validate_instance(MINIMO, "profile"), [])

    def test_los_campos_que_nadie_leia_ya_no_se_admiten(self):
        casos = {
            "validation": dict(MINIMO, validation=[{"check": "algo"}]),
            "steps": dict(MINIMO, rehydrate={"required_inputs": ["x"], "steps": ["leer NOTAS.md"]}),
            "method": dict(MINIMO, collectors=[{"source": "app", "method": "container_stdout"}]),
            "location": dict(MINIMO, collectors=[{"source": "app", "location": "/var/log/x"}]),
            "enable": dict(MINIMO, collectors=[{"source": "app", "enable": "DEBUG"}]),
        }
        for campo, perfil in casos.items():
            errores = validate_instance(perfil, "profile")
            self.assertTrue(any(campo in e for e in errores), (campo, errores))

    def test_un_colector_solo_exige_source(self):
        self.assertEqual(validate_instance(dict(MINIMO, collectors=[{"source": "app"}]), "profile"), [])
        errores = validate_instance(dict(MINIMO, collectors=[{"parser": "parsers/x.json"}]), "profile")
        self.assertTrue(any("source" in e for e in errores), errores)


class ColectorDelArchivoCapturadoTest(unittest.TestCase):
    COLECTORES = [
        {"source": "postgresql", "file": "containers/db.err.log", "parser": "p.json"},
        {"source": "springboot", "file": "containers/*.log", "parser": "s.json"},
        {"source": "dblog", "parser": "d.json"},
    ]

    def test_exacto_luego_comodin_luego_por_servicio(self):
        self.assertEqual(collector_source(self.COLECTORES, "containers/db.err.log", "db"), "postgresql")
        self.assertEqual(collector_source(self.COLECTORES, "containers/recursos.log", "recursos"), "springboot")
        self.assertEqual(collector_source(self.COLECTORES, "containers/db.log", "db"), "springboot",
                         "el stdout de db casa el comodín: lo que el perfil declare, no lo que uno supondría")
        self.assertEqual(collector_source(self.COLECTORES, "containers/dblog.err.log", "dblog"), "springboot",
                         "un comodín que casa gana al colector que solo se llama como el servicio")
        sin_comodin = [c for c in self.COLECTORES if c["source"] != "springboot"]
        self.assertEqual(collector_source(sin_comodin, "containers/dblog.err.log", "dblog"), "dblog")
        self.assertIsNone(collector_source(sin_comodin, "containers/stub.err.log", "stub"))
        self.assertIsNone(collector_source([], "containers/app.log", "app"))

    def test_los_perfiles_del_repo_declaran_archivo_y_parser_coherentes(self):
        for profile in iter_profiles():
            for collector in profile.data.get("collectors", []):
                self.assertIn("parser", collector, f"{profile.id}: colector {collector['source']} sin parser")
                self.assertTrue((profile.dir / collector["parser"]).is_file(), f"{profile.id}: falta {collector['parser']}")
                spec = json.loads((profile.dir / collector["parser"]).read_text(encoding="utf-8"))
                self.assertEqual(spec["source"], collector["source"], f"{profile.id}: el parser y el colector nombran fuentes distintas")
                if "file" in collector:
                    self.assertTrue(collector["file"].startswith("containers/"), (profile.id, collector["file"]))
                for muerto in ("method", "location", "enable"):
                    self.assertNotIn(muerto, collector, f"{profile.id}: campo muerto {muerto}")
            self.assertNotIn("validation", profile.data, profile.id)
            self.assertNotIn("steps", profile.data.get("rehydrate", {}), profile.id)


if __name__ == "__main__":
    unittest.main()
