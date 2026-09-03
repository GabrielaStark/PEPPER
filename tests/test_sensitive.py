"""Gate de datos: un paquete remoto exige una decisión humana explícita."""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pepper.correlate import run as correlate_run  # noqa: E402
from pepper.package import assemble  # noqa: E402
from pepper.sensitive import scan  # noqa: E402

FIXTURE = ROOT / "examples" / "legacy-demo"


class SensitiveDataGateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        raw = self.root / "raw"
        shutil.copytree(FIXTURE / "raw-evidence", raw)
        session_path = raw / "session.json"
        session = json.loads(session_path.read_text(encoding="utf-8"))
        session["synthetic"] = False
        session.pop("synthetic_note", None)
        session_path.write_text(json.dumps(session), encoding="utf-8")
        self.correlated = self.root / "correlated"
        correlate_run(raw, self.correlated)

    def tearDown(self):
        self._tmp.cleanup()

    def test_remoto_bloquea_credencial_y_no_expone_el_valor(self):
        legacy = self.root / "legacy-secret"
        legacy.mkdir()
        (legacy / "application.properties").write_text("db.password=ValorQueNoDebeSalir123\n", encoding="utf-8")
        package = self.root / "package-secret"

        with self.assertRaises(ValueError) as raised:
            assemble(self.correlated, package, legacy, data_mode="remote")

        message = str(raised.exception)
        self.assertIn("legacy/application.properties:1", message)
        self.assertNotIn("ValorQueNoDebeSalir123", message)
        self.assertFalse(package.exists())

    def test_remoto_bloquea_curp(self):
        legacy = self.root / "legacy-curp"
        legacy.mkdir()
        (legacy / "persona.sql").write_text("INSERT INTO persona VALUES ('GOCG950101MDFRRB09');\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "curp"):
            assemble(self.correlated, self.root / "package-curp", legacy, data_mode="remote")

    def test_remoto_exige_reconocer_binarios(self):
        legacy = self.root / "legacy-binary"
        legacy.mkdir()
        (legacy / "sistema.war").write_bytes(b"PK\x03\x04\x00\x00contenido")
        with self.assertRaisesRegex(ValueError, "no puede inspeccionar"):
            assemble(self.correlated, self.root / "package-binary", legacy, data_mode="remote")

    def test_excepciones_quedan_registradas_en_el_manifest(self):
        legacy = self.root / "legacy-approved"
        legacy.mkdir()
        (legacy / "config.txt").write_text("token=TokenAprobadoPorHumano\n", encoding="utf-8")
        (legacy / "sistema.war").write_bytes(b"PK\x03\x04\x00\x00contenido")
        package = self.root / "package-approved"
        summary = assemble(
            self.correlated,
            package,
            legacy,
            data_mode="remote",
            allow_sensitive=True,
            acknowledge_unscanned=True,
        )
        manifest = json.loads(Path(summary["external_manifest"]).read_text(encoding="utf-8"))
        self.assertTrue(manifest["data_policy"]["allow_sensitive"])
        self.assertTrue(manifest["data_policy"]["acknowledge_unscanned"])
        self.assertGreaterEqual(manifest["data_policy"]["sensitive_findings"], 1)
        self.assertGreaterEqual(manifest["data_policy"]["unscanned_files"], 1)

    def test_remoto_bloquea_rfc(self):
        legacy = self.root / "legacy-rfc"
        legacy.mkdir()
        (legacy / "empresa.sql").write_text("INSERT INTO enempresa VALUES ('GOCG950101AB1');\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "rfc"):
            assemble(self.correlated, self.root / "package-rfc", legacy, data_mode="remote")

    def test_la_bandera_synthetic_no_exime_del_gate(self):
        # session.json lo escribe el agente en Observe: una bandera suya no abre la frontera
        raw = self.root / "raw-synthetic"
        shutil.copytree(FIXTURE / "raw-evidence", raw)  # trae synthetic: true
        correlated = self.root / "correlated-synthetic"
        correlate_run(raw, correlated)
        legacy = self.root / "legacy-real"
        legacy.mkdir()
        (legacy / "sistema.war").write_bytes(b"PK\x03\x04\x00\x00contenido")
        with self.assertRaisesRegex(ValueError, "no puede inspeccionar"):
            assemble(correlated, self.root / "package-synthetic", legacy, data_mode="remote")
        self.assertFalse((self.root / "package-synthetic").exists())

    def test_codificacion_mixta_no_truena_y_queda_como_no_inspeccionado(self):
        # UTF-8 limpio en los primeros 8 KB (pasa el sniff) y un byte latin-1 después
        legacy = self.root / "legacy-latin1"
        legacy.mkdir()
        (legacy / "schema.sql").write_bytes(b"-- comentario\n" * 700 + b"-- a\xf1o fiscal\n")
        report = scan([("legacy", legacy, None)])
        self.assertEqual([(f.kind, f.location) for f in report.unscanned], [("undecodable", "legacy/schema.sql")])
        self.assertEqual(report.sensitive, [])

    def test_modo_local_prohibe_agentes_remotos_en_el_adaptador(self):
        legacy = self.root / "legacy-local"
        legacy.mkdir()
        (legacy / "config.txt").write_text("password=SecretoLocal123\n", encoding="utf-8")
        package = self.root / "package-local"
        summary = assemble(self.correlated, package, legacy, data_mode="local")
        instructions = (package / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("análisis LOCAL", instructions)
        self.assertIn("No lo abras con Claude Code", instructions)
        self.assertEqual(summary["data_mode"], "local")


if __name__ == "__main__":
    unittest.main()
