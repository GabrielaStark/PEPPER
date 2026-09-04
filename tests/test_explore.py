"""El explorador sin navegador: valores plausibles, la nota de sesión y el parser de explore.jsonl."""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pepper.correlate.parsers import ExplorerParser  # noqa: E402
from pepper.explore import operator_note, plausible_value, write_session  # noqa: E402
from pepper.session import Session  # noqa: E402


class PlausibleValueTest(unittest.TestCase):
    def test_pistas_del_sistema_mandan(self):
        self.assertEqual(plausible_value("formCita:txtCurp", "", "text", {"curp": "XEXX010101HNEXXXA4"}), "XEXX010101HNEXXXA4")

    def test_heuristicas(self):
        self.assertEqual(plausible_value("txtCorreo", "", "text", {}), "prueba@pepper.invalid")
        self.assertEqual(plausible_value("txtNombre", "", "text", {}), "Prueba")
        self.assertEqual(plausible_value("txtEdad", "", "text", {}), "10")
        self.assertIsNone(plausible_value("txtPassword", "", "password", {}))
        self.assertIsNone(plausible_value("chk", "", "checkbox", {}))
        self.assertEqual(plausible_value("", "Edad", "number", {}), "10")


class SessionNoteTest(unittest.TestCase):
    def test_nota_desde_las_acciones(self):
        actions = [
            {"kind": "screen", "role": "ADMIN", "route": "/cita", "result": "ok"},
            {"kind": "empty_submit", "role": "ADMIN", "route": "/cita", "result": "rejected", "messages": ["Nombre es obligatorio"]},
            {"kind": "screen", "role": "RECEPCION", "route": "/reportes", "result": "redirected"},
        ]
        note = operator_note(actions, {"roles": {"ADMIN": "3/3 pantallas, 1 rechazos provocados"}}, "recorrido")
        self.assertIn("POR EL AGENTE", note)
        self.assertIn("1 rechazos provocados", note)
        self.assertIn("Nombre es obligatorio", note)
        self.assertIn("1 pantallas que mandaron a login", note)

    def test_session_json_valida(self):
        try:
            from pepper.validate import validate_instance
        except ImportError:
            self.skipTest("jsonschema no instalado")
        with tempfile.TemporaryDirectory() as tmp:
            start = datetime(2026, 9, 4, 16, 0, tzinfo=timezone.utc)
            path = write_session(Path(tmp), "explore-001", "recorrido", start, start, "perfil-x", "nota",
                                 [{"source": "explorer", "kind": "generic", "file": "explore.jsonl", "note": "x"}])
            session = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(session["timezone"], "+00:00")
            self.assertEqual(validate_instance(session, "session"), [])


class ExplorerParserTest(unittest.TestCase):
    def test_acciones_a_eventos(self):
        session = Session(session_id="explore-001", flow_name="x", observed_start=None, observed_end=None,
                          tz=timezone.utc, collectors=[])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "explore.jsonl"
            path.write_text("\n".join(json.dumps(a) for a in [
                {"ts": "2026-09-04T16:00:00.000+00:00", "role": "ADMIN", "route": "/cita", "kind": "screen", "label": "abrir /cita", "result": "ok", "status": 200},
                {"ts": "2026-09-04T16:00:05.000+00:00", "role": "ADMIN", "route": "/cita", "kind": "filled_submit", "label": "Guardar",
                 "result": "rejected", "messages": ["Sexo: el valor no es válido"], "detail": {"filled": {"txtCurp": "X"}}},
            ]) + "\n", encoding="utf-8")
            events, unparsed = ExplorerParser().parse_file(path, "explore.jsonl", session)
        self.assertEqual(unparsed, [])
        self.assertEqual([e.severity for e in events], ["info", "warn"], "un rechazo es evidencia protegida")
        self.assertEqual(events[0].component, "explorador")
        self.assertIn("Guardar → rejected · Sexo", events[1].message)
        self.assertEqual(events[1].metadata["detail_filled"], {"txtCurp": "X"})
        self.assertIsNone(events[1].correlation_id)


if __name__ == "__main__":
    unittest.main()
