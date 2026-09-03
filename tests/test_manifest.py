"""Integridad de evidencia (C-02): lo que Export publica es lo que Correlate produjo.

Reproduce el ataque de la auditoría: fabricar un evento dentro del paquete y
hacer que una conclusión lo cite. Antes Export lo publicaba; ahora muere.
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pepper import manifest as evidence_manifest  # noqa: E402
from pepper.correlate import run as correlate_run  # noqa: E402
from pepper.export import check as export_check  # noqa: E402
from pepper.package import assemble  # noqa: E402

FIXTURE = ROOT / "examples" / "legacy-demo"


class ManifestUnitTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "a.txt").write_text("hola", encoding="utf-8")
        (self.root / "sub").mkdir()
        (self.root / "sub" / "b.txt").write_text("mundo", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_roundtrip_limpio(self):
        manifest = evidence_manifest.build(self.root)
        evidence_manifest.write(self.root, manifest)
        loaded = evidence_manifest.load(self.root / evidence_manifest.MANIFEST_NAME)
        self.assertEqual(evidence_manifest.verify(self.root, loaded, scopes=["sub"]), [])

    def test_archivo_alterado_se_detecta(self):
        manifest = evidence_manifest.build(self.root)
        (self.root / "a.txt").write_text("alterado", encoding="utf-8")
        errors = evidence_manifest.verify(self.root, manifest)
        self.assertTrue(any("modificado" in e for e in errors))

    def test_archivo_faltante_se_detecta(self):
        manifest = evidence_manifest.build(self.root)
        (self.root / "a.txt").unlink()
        errors = evidence_manifest.verify(self.root, manifest)
        self.assertTrue(any("falta a.txt" in e for e in errors))

    def test_archivo_extra_en_ambito_se_detecta(self):
        manifest = evidence_manifest.build(self.root)
        (self.root / "sub" / "colado.txt").write_text("x", encoding="utf-8")
        errors = evidence_manifest.verify(self.root, manifest, scopes=["sub"])
        self.assertTrue(any("colado.txt" in e and "ajeno" in e for e in errors))


class ExportIntegrityTest(unittest.TestCase):
    """Pipeline real del fixture: correlate → package → export, y luego el ataque."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        correlated = base / "correlated"
        correlate_run(FIXTURE / "raw-evidence", correlated)
        # un legacy con artefactos SUELTOS en la raíz (auditoría H-03)
        legacy = base / "legacy"
        legacy.mkdir()
        (legacy / "sistema.war").write_bytes(b"PK\x03\x04 falso war")
        (legacy / "respaldo.dump").write_bytes(b"PGDMP falso")
        shutil.copytree(FIXTURE / "artifacts" / "configuration", legacy / "configuration")
        self.package = base / "package"
        # binarios falsos + credenciales de juguete: excepciones explícitas, como en un legacy real (D24)
        summary = assemble(correlated, self.package, legacy, allow_sensitive=True, acknowledge_unscanned=True)
        self.external_manifest = Path(summary["external_manifest"])
        shutil.copy2(FIXTURE / "expected" / "runtime-discovery.json",
                     self.package / "output" / "runtime-discovery.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_paquete_intacto_pasa(self):
        report = export_check(self.package, self.external_manifest)
        self.assertEqual(report.errors, [], report.errors)

    def test_artefactos_sueltos_entran_al_paquete(self):
        self.assertTrue((self.package / "legacy" / "sistema.war").is_file())
        self.assertTrue((self.package / "legacy" / "respaldo.dump").is_file())

    def test_evento_fabricado_se_rechaza(self):
        events = self.package / "evidence" / "events.jsonl"
        with events.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"event_id": "E-FABRICADA", "timestamp": "2026-08-25T13:21:00-06:00",
                                     "session_id": "flow-001", "source": "http-proxy",
                                     "event_type": "log", "message": "inventado"}) + "\n")
        report = export_check(self.package, self.external_manifest)
        self.assertTrue(any("modificado" in e for e in report.errors), report.errors)

    def test_archivo_de_evidencia_colado_se_rechaza(self):
        (self.package / "evidence" / "raw" / "colado.log").write_text("x", encoding="utf-8")
        report = export_check(self.package, self.external_manifest)
        self.assertTrue(any("ajeno" in e for e in report.errors), report.errors)

    def test_artefacto_del_legacy_alterado_se_rechaza(self):
        (self.package / "legacy" / "sistema.war").write_bytes(b"otro contenido")
        report = export_check(self.package, self.external_manifest)
        self.assertTrue(any("legacy/sistema.war" in e for e in report.errors), report.errors)

    def test_credenciales_en_notas_se_redactan_en_el_paquete(self):
        # NOTAS.md con una contraseña en claro no puede viajar al agente (C-03)
        notas = Path(self._tmp.name) / "legacy" / "NOTAS.md"
        # regenerar un paquete con NOTAS que trae credencial
        import shutil as _sh
        pkg2 = Path(self._tmp.name) / "package2"
        legacy2 = Path(self._tmp.name) / "legacy2"
        legacy2.mkdir()
        (legacy2 / "NOTAS.md").write_text("PASSWORD: SuperSecreta123\nusuario: ana\n", encoding="utf-8")
        from pepper.correlate import run as _run
        from pepper.package import assemble as _assemble
        corr = Path(self._tmp.name) / "corr2"
        _run(FIXTURE / "raw-evidence", corr)
        summary = _assemble(corr, pkg2, legacy2, allow_sensitive=True)
        self.assertIn("NOTAS.md", summary["redacted_notes"])
        content = (pkg2 / "legacy" / "NOTAS.md").read_text(encoding="utf-8")
        self.assertNotIn("SuperSecreta123", content)
        self.assertIn("[REDACTADO POR PEPPER]", content)
        # el original intacto
        self.assertIn("SuperSecreta123", (legacy2 / "NOTAS.md").read_text(encoding="utf-8"))

    def test_sin_manifest_no_hay_export(self):
        (self.package / evidence_manifest.MANIFEST_NAME).unlink()
        report = export_check(self.package, self.external_manifest)
        self.assertTrue(any("manifest" in e for e in report.errors), report.errors)

    def test_manifest_externo_detecta_manifest_interno_editado(self):
        external = self.external_manifest
        # el atacante edita evidencia Y el manifest interno para taparlo
        events = self.package / "evidence" / "events.jsonl"
        events.write_text(events.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        internal = json.loads((self.package / evidence_manifest.MANIFEST_NAME).read_text())
        internal["files"]["evidence/events.jsonl"] = evidence_manifest.sha256_file(events)
        (self.package / evidence_manifest.MANIFEST_NAME).write_text(json.dumps(internal), encoding="utf-8")
        self.assertTrue(export_check(self.package).errors, "el manifest externo ahora es obligatorio")
        report = export_check(self.package, external_manifest=external)
        self.assertTrue(any("manifest externo" in e for e in report.errors), report.errors)

    def test_symlink_anidado_se_rechaza_sin_tocar_el_destino(self):
        outside = Path(self._tmp.name) / "fuera.md"
        outside.write_text("PASSWORD: no-debe-cambiar\n", encoding="utf-8")
        legacy = Path(self._tmp.name) / "legacy-symlink"
        (legacy / "docs").mkdir(parents=True)
        (legacy / "docs" / "nota.md").symlink_to(outside)
        correlated = Path(self._tmp.name) / "corr-symlink"
        correlate_run(FIXTURE / "raw-evidence", correlated)
        package = Path(self._tmp.name) / "package-symlink"

        with self.assertRaisesRegex(ValueError, "symlink"):
            assemble(correlated, package, legacy)

        self.assertEqual(outside.read_text(encoding="utf-8"), "PASSWORD: no-debe-cambiar\n")
        self.assertFalse(package.exists())


class TraversalTest(unittest.TestCase):
    def test_collector_con_ruta_fuera_de_la_evidencia_falla(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            evidence = base / "evidence"
            evidence.mkdir()
            (base / "secreto.txt").write_text("fuera", encoding="utf-8")
            session = {
                "session_id": "flow-001", "observed_start": "2026-08-25T13:20:30-06:00",
                "observed_end": "2026-08-25T13:22:14-06:00", "timezone": "-06:00",
                "collectors": [{"source": "http-proxy", "file": "../secreto.txt"}],
            }
            (evidence / "session.json").write_text(json.dumps(session), encoding="utf-8")
            with self.assertRaises(ValueError):
                correlate_run(evidence, base / "out")


if __name__ == "__main__":
    unittest.main()
