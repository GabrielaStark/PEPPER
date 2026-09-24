"""La frontera de datos con alcance (D24, revisión 2026-09-24).

Antes `{"remote": true}` en pepper-out/data-boundary.json bastaba para repetir `package` con
--allow-sensitive --acknowledge-unscanned: una decisión valía para cualquier dato posterior, de
ese sistema o de otro. Ahora la autorización dice qué cubre y lo que no cabe detiene el paquete.
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pepper.boundary import BoundaryError, authorize, key_path  # noqa: E402
from pepper.correlate import run as correlate_run  # noqa: E402
from pepper.package import assemble  # noqa: E402
from pepper.sensitive import pseudonym, pseudonymize_text  # noqa: E402

FIXTURE = ROOT / "examples" / "legacy-demo"
CURP = "GOCG950101MDFRRB09"


class _ConLegacy(unittest.TestCase):
    """Un legacy chico con una CURP en dos archivos y un binario; sin pruebas propias."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        raw = self.root / "raw"
        shutil.copytree(FIXTURE / "raw-evidence", raw)
        self.correlated = self.root / "correlated"
        correlate_run(raw, self.correlated)
        self.legacy = self.root / "legacy"
        self.legacy.mkdir()
        (self.legacy / "persona.sql").write_text(f"INSERT INTO persona VALUES ('{CURP}');\n", encoding="utf-8")
        (self.legacy / "otra.sql").write_text(f"-- la misma persona: {CURP}\n", encoding="utf-8")
        (self.legacy / "sistema.war").write_bytes(b"PK\x03\x04\x00\x00contenido")
        self.auth = self.root / "pepper-out" / "data-boundary.json"
        self.n = 0

    def tearDown(self):
        self._tmp.cleanup()

    def _package(self, legacy=None, previous=None):
        self.n += 1
        return assemble(self.correlated, self.root / f"package-{self.n}", legacy or self.legacy, data_mode="remote",
                        authorization=self.auth, previous=previous)

    def _authorized_package(self):
        with self.assertRaises(BoundaryError) as raised:
            self._package()
        authorize(raised.exception.proposal_path, "Ana Responsable", self.auth)
        return self._package()


class AlcanceTest(_ConLegacy):

    def test_sin_autorizacion_propone_el_alcance_y_no_arma_nada(self):
        with self.assertRaises(BoundaryError) as raised:
            self._package()
        proposal = json.loads(raised.exception.proposal_path.read_text(encoding="utf-8"))
        self.assertEqual(proposal["categories"], ["curp"])
        self.assertEqual(list(proposal["unscanned"]), ["legacy/sistema.war"])
        self.assertNotIn(CURP, raised.exception.proposal_path.read_text(encoding="utf-8"))
        self.assertFalse((self.root / "package-1").exists())

    def test_con_autorizacion_viaja_sustituido_y_con_el_mismo_seudonimo(self):
        summary = self._authorized_package()
        package = Path(summary["out_dir"])
        persona = (package / "legacy" / "persona.sql").read_text(encoding="utf-8")
        otra = (package / "legacy" / "otra.sql").read_text(encoding="utf-8")
        self.assertNotIn(CURP, persona + otra)
        token = pseudonym("curp", CURP, bytes.fromhex(key_path(self.auth).read_text().strip()))
        self.assertIn(token, persona)
        self.assertIn(token, otra)
        manifest = json.loads(Path(summary["external_manifest"]).read_text(encoding="utf-8"))
        self.assertEqual(manifest["data_policy"]["authorization"]["decided_by"], "Ana Responsable")
        self.assertIn("source_sha256", manifest["data_policy"]["substitutions"]["legacy/persona.sql"])

    def test_una_categoria_nueva_detiene_hasta_que_una_persona_la_autorice(self):
        # el sistema es el mismo; lo nuevo llega con la evidencia (aquí, el documento anterior)
        self._authorized_package()
        previous = self.root / "funcional-anterior.json"
        previous.write_text(json.dumps({"summary": {"context": "contacto: alguien@example.com"}}), encoding="utf-8")
        with self.assertRaisesRegex(BoundaryError, "categorías de datos no autorizadas: email") as raised:
            self._package(previous=previous)
        authorize(raised.exception.proposal_path, "Ana Responsable", self.auth)
        self._package(previous=previous)
        history = json.loads(self.auth.read_text(encoding="utf-8"))["history"]
        self.assertEqual(len(history), 2)

    def test_un_binario_que_cambio_no_viaja_con_la_autorizacion_vieja(self):
        self._authorized_package()
        (self.legacy / "sistema.war").write_bytes(b"PK\x03\x04\x00\x00otro contenido")
        with self.assertRaisesRegex(BoundaryError, "el legacy cambió desde que se autorizó"):
            self._package()

    def test_otro_legacy_no_hereda_la_autorizacion(self):
        self._authorized_package()
        other = self.root / "otro-legacy"
        other.mkdir()
        (other / "persona.sql").write_text(f"INSERT INTO persona VALUES ('{CURP}');\n", encoding="utf-8")
        with self.assertRaisesRegex(BoundaryError, "el legacy cambió desde que se autorizó") as raised:
            self._package(other)
        with self.assertRaisesRegex(ValueError, "otro sistema o de otra versión"):
            authorize(raised.exception.proposal_path, "Ana Responsable", self.auth)

    def test_las_notas_no_cuentan_para_la_huella_del_sistema(self):
        self._authorized_package()
        (self.legacy / "NOTAS.md").write_text("producción: servidor viejo\n", encoding="utf-8")
        self._package()

    def test_el_material_de_llave_nunca_viaja(self):
        (self.legacy / "server.jks").write_bytes(b"\xfe\xed\xfe\xed llave")
        summary = self._authorized_package()
        self.assertEqual(summary["excluded"], ["legacy/server.jks"])
        self.assertFalse((Path(summary["out_dir"]) / "legacy" / "server.jks").exists())

    def test_autorizar_exige_nombre(self):
        with self.assertRaises(BoundaryError) as raised:
            self._package()
        with self.assertRaisesRegex(ValueError, "--by"):
            authorize(raised.exception.proposal_path, "  ", self.auth)
        self.assertFalse(self.auth.exists())

    def test_sin_nada_sensible_no_hace_falta_autorizacion(self):
        clean = self.root / "limpio"
        clean.mkdir()
        (clean / "README.md").write_text("sistema de citas\n", encoding="utf-8")
        summary = assemble(self.correlated, self.root / "package-limpio", clean, data_mode="remote")
        self.assertEqual(summary["substituted"], 0)


class SinTopeParaDecidirTest(_ConLegacy):
    """El escáner guardaba como máximo 200 hallazgos y 200 archivos no inspeccionables, y con eso
    se decidía el alcance (revisión de main 2026-09-24): el archivo 201 quedaba fuera de la
    autorización, y una categoría que aparecía después de 200 hallazgos no llegaba a la propuesta."""

    def test_el_archivo_201_queda_en_la_autorizacion_y_si_cambia_se_detiene(self):
        # .txt: las notas no entran en la huella del legacy, así que SOLO la autorización lo vigila
        for i in range(201):
            (self.legacy / f"export-{i:03d}.txt").write_bytes(b"\x00binario %d" % i)
        with self.assertRaises(BoundaryError) as raised:
            self._package()
        proposal = json.loads(raised.exception.proposal_path.read_text(encoding="utf-8"))
        self.assertIn("legacy/export-200.txt", proposal["unscanned"])
        self.assertEqual(len(proposal["unscanned"]), 202)  # los 201 y sistema.war
        authorize(raised.exception.proposal_path, "Ana Responsable", self.auth)
        self._package()
        (self.legacy / "export-200.txt").write_bytes(b"\x00otro contenido")
        with self.assertRaisesRegex(BoundaryError, "cambió desde que se autorizó: legacy/export-200.txt"):
            self._package()

    def test_una_categoria_despues_de_200_hallazgos_llega_a_la_propuesta(self):
        lines = [f"INSERT INTO persona VALUES ('GOCG95{(i % 12) + 1:02d}{(i % 28) + 1:02d}MDFRRB{i % 10}9');" for i in range(250)]
        lines.append("INSERT INTO contacto VALUES ('alguien@example.com');")
        (self.legacy / "persona.sql").write_text("\n".join(lines) + "\n", encoding="utf-8")
        with self.assertRaises(BoundaryError) as raised:
            self._package()
        proposal = json.loads(raised.exception.proposal_path.read_text(encoding="utf-8"))
        self.assertEqual(proposal["categories"], ["curp", "email"])
        self.assertTrue(any("(email)" in location for location in proposal["locations"]))
        authorize(raised.exception.proposal_path, "Ana Responsable", self.auth)
        summary = self._package()
        manifest = json.loads(Path(summary["external_manifest"]).read_text(encoding="utf-8"))
        self.assertEqual(manifest["data_policy"]["sensitive_findings"], 252)  # 250 + el correo + otra.sql
        self.assertEqual(manifest["data_policy"]["categories"], ["curp", "email"])


class SustitucionTest(unittest.TestCase):
    KEY = b"k" * 32

    def test_conserva_lineas_y_json_valido(self):
        text = '{"msg": "db_password=abc123\\" correo a@b.com"}\n{"curp": "PEPR900101HMCPPR09"}'
        new, counts = pseudonymize_text(text, self.KEY)
        self.assertEqual(len(new.splitlines()), 2)
        for line in new.splitlines():
            json.loads(line)
        self.assertEqual(counts, {"credential": 1, "email": 1, "curp": 1})

    def test_el_seudonimo_no_depende_de_mayusculas_ni_del_archivo(self):
        a, _ = pseudonymize_text("Ana@Example.com", self.KEY)
        b, _ = pseudonymize_text("ana@example.com", self.KEY)
        self.assertEqual(a, b)
        self.assertTrue(a.startswith("[CORREO-"))

    def test_llave_privada_completa(self):
        new, counts = pseudonymize_text("-----BEGIN PRIVATE KEY-----\nMIIabc\n-----END PRIVATE KEY-----\nsigue", self.KEY)
        self.assertNotIn("MIIabc", new)
        self.assertTrue(new.endswith("sigue"))
        self.assertEqual(counts["private_key"], 3)


if __name__ == "__main__":
    unittest.main()
