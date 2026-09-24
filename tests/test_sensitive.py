"""Gate de datos: un paquete remoto exige una decisión humana explícita."""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from pepper.correlate import run as correlate_run  # noqa: E402
from pepper.package import assemble  # noqa: E402
from autorizar import assemble_authorized  # noqa: E402
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
        with self.assertRaisesRegex(ValueError, r"sin autorizar|sistema\.war \(binary\)"):
            assemble(self.correlated, self.root / "package-binary", legacy, data_mode="remote")

    def test_la_autorizacion_queda_registrada_en_el_manifest(self):
        legacy = self.root / "legacy-approved"
        legacy.mkdir()
        (legacy / "config.properties").write_text("token=TokenAprobadoPorHumano\n", encoding="utf-8")
        (legacy / "sistema.war").write_bytes(b"PK\x03\x04\x00\x00contenido")
        package = self.root / "package-approved"
        summary = assemble_authorized(self.correlated, package, legacy, by="Ana Responsable", data_mode="remote")
        manifest = json.loads(Path(summary["external_manifest"]).read_text(encoding="utf-8"))
        policy = manifest["data_policy"]
        self.assertEqual(policy["authorization"]["decided_by"], "Ana Responsable")
        self.assertIn("credential", policy["categories"])
        self.assertGreaterEqual(policy["sensitive_findings"], 1)
        self.assertGreaterEqual(policy["unscanned_files"], 1)
        self.assertIn("legacy/config.properties", policy["substitutions"])
        self.assertNotIn("TokenAprobadoPorHumano", (package / "legacy" / "config.properties").read_text(encoding="utf-8"))

    def test_remoto_bloquea_rfc(self):
        legacy = self.root / "legacy-rfc"
        legacy.mkdir()
        (legacy / "empresa.sql").write_text("INSERT INTO enempresa VALUES ('GOCG950101AB1');\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "rfc"):
            assemble(self.correlated, self.root / "package-rfc", legacy, data_mode="remote")

    def test_variantes_de_credencial_del_mundo_real(self):
        # `psw:` apareció en un artefacto real y el gate lo dejó pasar (E2E 2026-09-03)
        legacy = self.root / "legacy-variantes"
        legacy.mkdir()
        (legacy / "sei.yml").write_text("sei:\n  usr: servicio\n  psw: ValorFalso123\n", encoding="utf-8")
        (legacy / "mail.properties").write_text("mail.contrasenia=ValorFalso456\n", encoding="utf-8")
        (legacy / "ws.xml").write_text("<credencial>ValorFalso789</credencial>\n", encoding="utf-8")
        report = scan([("legacy", legacy, None)])
        hits = sorted(f.path.rsplit("/", 1)[-1] for f in report.sensitive if f.kind == "credential")
        self.assertEqual(hits, ["mail.properties", "sei.yml", "ws.xml"])

    def test_la_bandera_synthetic_no_exime_del_gate(self):
        # session.json lo escribe el agente en Observe: una bandera suya no abre la frontera
        raw = self.root / "raw-synthetic"
        shutil.copytree(FIXTURE / "raw-evidence", raw)  # trae synthetic: true
        correlated = self.root / "correlated-synthetic"
        correlate_run(raw, correlated)
        legacy = self.root / "legacy-real"
        legacy.mkdir()
        (legacy / "sistema.war").write_bytes(b"PK\x03\x04\x00\x00contenido")
        with self.assertRaisesRegex(ValueError, r"sin autorizar|sistema\.war \(binary\)"):
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


class EscanerCompletoTest(unittest.TestCase):
    """Lo que decide es completo; solo lo que se muestra tiene tope."""

    def test_todos_los_no_inspeccionables_y_todas_las_categorias(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i in range(300):
                (root / f"bin-{i:03d}.dat").write_bytes(b"\x00%d" % i)
            lines = [f"'GOCG95{(i % 12) + 1:02d}{(i % 28) + 1:02d}MDFRRB{i % 10}9'" for i in range(400)]
            lines.append("contacto: alguien@example.com")
            (root / "dump.sql").write_text("\n".join(lines) + "\n", encoding="utf-8")
            report = scan([("legacy", root, None)])
        self.assertEqual(len(report.unscanned), 300)
        self.assertEqual(report.categories, {"curp", "email"})
        self.assertEqual(report.sensitive_total, 401)
        self.assertLessEqual(len(report.sensitive), 201)
        self.assertIn("email", {f.kind for f in report.sensitive})  # la primera de cada categoría se muestra

    def test_dos_credenciales_en_una_linea_son_una_ubicacion(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "app.properties").write_text("db.password=Uno12345 api_key=Dos12345\n", encoding="utf-8")
            report = scan([("legacy", Path(tmp), None)])
        self.assertEqual(report.sensitive_total, 1)
        self.assertEqual([f.location for f in report.sensitive], ["legacy/app.properties:1"])


if __name__ == "__main__":
    unittest.main()


class GateOnWhatTravelsTest(SensitiveDataGateTest):
    """El gate escanea la COPIA que viaja, archivo por archivo — no las fuentes ni una representación.

    Antes se escaneaban evidencia, legacy y el mapa rendido, y después se copiaban sin
    mirarse `previous/funcional.json` y `map/system-map.json`: un paquete remoto salía con
    `sensitive_findings: 0` llevando una credencial (revisión 2026-09-21).
    """

    def _legacy_limpio(self):
        legacy = self.root / "legacy-limpio"
        legacy.mkdir()
        (legacy / "LEEME.txt").write_text("nada sensible aquí\n", encoding="utf-8")
        return legacy

    def test_remoto_bloquea_credencial_en_el_discovery_anterior(self):
        previous = self.root / "funcional-anterior.json"
        previous.write_text(json.dumps({"summary": {"context": "db.password=ValorQueNoDebeSalir123"}}), encoding="utf-8")
        package = self.root / "package-previous"
        with self.assertRaises(ValueError) as raised:
            assemble(self.correlated, package, self._legacy_limpio(), data_mode="remote", previous=previous)
        message = str(raised.exception)
        self.assertIn("previous/funcional.json:1", message)
        self.assertNotIn("ValorQueNoDebeSalir123", message)
        self.assertFalse(package.exists())
        self.assertEqual([p.name for p in self.root.iterdir() if "staging" in p.name], [], "el staging no sobrevive al gate")

    def test_remoto_bloquea_credencial_en_el_mapa_estructurado(self):
        # El valor va en el JSON del mapa, no en su versión legible: es el archivo que se copiaba sin escanear.
        system_map = {"schema_version": "0.2.0", "profile_id": None, "artifact": {"name": "x.war"},
                      "complete": True, "coverage_gaps": [], "entrypoints": [], "jobs": [],
                      "external_dependencies": [], "data_stores": [], "catalogs": [], "distributions": [],
                      "classes": [], "screens": [], "labels": 0,
                      "notes": ["datasource: password=ValorQueNoDebeSalir456"]}
        docs = self.root / "docs"
        docs.mkdir()
        (docs / "system-map.json").write_text(json.dumps(system_map, indent=2), encoding="utf-8")
        package = self.root / "package-map"
        with self.assertRaises(ValueError) as raised:
            assemble(self.correlated, package, self._legacy_limpio(), data_mode="remote",
                     system_map=docs / "system-map.json")
        message = str(raised.exception)
        self.assertIn("map/system-map.json:", message)
        self.assertNotIn("ValorQueNoDebeSalir456", message)
        self.assertFalse(package.exists())

    def test_el_conteo_del_manifest_es_el_de_la_copia(self):
        previous = self.root / "funcional-anterior.json"
        previous.write_text(json.dumps({"summary": {"context": "db.password=ValorQueNoDebeSalir123"}}), encoding="utf-8")
        package = self.root / "package-contado"
        summary = assemble_authorized(self.correlated, package, self._legacy_limpio(), data_mode="remote",
                                      previous=previous)
        manifest = json.loads(Path(summary["external_manifest"]).read_text(encoding="utf-8"))
        self.assertGreaterEqual(manifest["data_policy"]["sensitive_findings"], 1)
        self.assertIn("previous/funcional.json", manifest["files"])
        self.assertTrue((package / "previous" / "funcional.json").is_file())
        self.assertFalse(any("staging" in p.name for p in self.root.iterdir()))

    def test_las_notas_se_escanean_ya_redactadas(self):
        # La redacción de notas es una mitigación: lo que se escanea (y cuenta) es la copia redactada.
        legacy = self._legacy_limpio()
        (legacy / "NOTAS.md").write_text("Contraseña de la base: ValorQueNoDebeSalir789\n", encoding="utf-8")
        package = self.root / "package-notas"
        summary = assemble(self.correlated, package, legacy, data_mode="remote")
        self.assertEqual(summary["redacted_notes"], ["NOTAS.md"])
        self.assertEqual(summary["sensitive_findings"], 0)
        self.assertNotIn("ValorQueNoDebeSalir789", (package / "legacy" / "NOTAS.md").read_text(encoding="utf-8"))
