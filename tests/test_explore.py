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


class CredencialesTest(unittest.TestCase):
    """Sin credencial no hay nada que explorar: el fallo es fatal, no una nota al pie.

    En la primera corrida en frío la base recién restaurada no tenía la extensión que da
    crypt()/gen_salt(); los seis roles quedaron "sin credencial" y el explorador siguió y
    dijo "Siguiente: correlate" con 6 acciones. Aquí se fija: setup_sql corre una vez antes,
    y si ningún rol queda listo se levanta un error con el stderr real.
    """

    def _explorer(self, creds):
        import io
        from pepper.explore import Explorer
        ex = Explorer.__new__(Explorer)          # sin navegador: solo lo que grant_credentials usa
        ex.config = {"credentials": creds, "roles": [{"name": "ADMIN", "user": "u1", "password": "p"},
                                                     {"name": "CONSULTAS", "user": "u2", "password": "p"}]}
        ex.actions, ex._log = [], io.StringIO()
        return ex

    def _run(self, ex, responder):
        from unittest import mock
        calls = []
        def fake(command, capture_output, text):
            sql = command[-1]; calls.append(sql)
            rc, err = responder(sql)
            return mock.Mock(returncode=rc, stderr=err, stdout="")
        with mock.patch("pepper.explore.subprocess.run", side_effect=fake):
            ready = ex.grant_credentials(Path("/x/docker-compose.yml"))
        return ready, calls

    def test_setup_sql_corre_una_vez_antes_de_los_roles(self):
        ex = self._explorer({"db_name": "d", "setup_sql": "CREATE EXTENSION IF NOT EXISTS pgcrypto",
                             "sql": "UPDATE u SET p = crypt('{password}') WHERE k = '{user}'"})
        ready, calls = self._run(ex, lambda sql: (0, ""))
        self.assertEqual(ready, ["ADMIN", "CONSULTAS"])
        self.assertEqual(calls[0], "CREATE EXTENSION IF NOT EXISTS pgcrypto")
        self.assertEqual(len(calls), 3)
        self.assertIn("k = 'u1'", calls[1])

    def test_sin_ningun_rol_listo_es_fatal_y_dice_por_que(self):
        ex = self._explorer({"db_name": "d", "sql": "UPDATE u SET p = gen_salt('bf') WHERE k = '{user}'"})
        with self.assertRaises(RuntimeError) as raised:
            self._run(ex, lambda sql: (1, "ERROR:  function gen_salt(unknown) does not exist"))
        self.assertIn("gen_salt", str(raised.exception))
        self.assertEqual([a.kind for a in ex.actions], ["credentials", "credentials"])

    def test_si_falla_el_setup_se_para_antes_de_los_roles(self):
        ex = self._explorer({"db_name": "d", "setup_sql": "CREATE EXTENSION nada", "sql": "UPDATE u SET p = 1 WHERE k = '{user}'"})
        with self.assertRaises(RuntimeError) as raised:
            self._run(ex, lambda sql: (1, "ERROR:  extension nada") if sql.startswith("CREATE") else (0, ""))
        self.assertIn("setup_sql", str(raised.exception))
        self.assertEqual(len(ex.actions), 1)

    def test_con_un_rol_listo_se_sigue_y_el_otro_queda_registrado(self):
        ex = self._explorer({"db_name": "d", "sql": "UPDATE u SET p = 1 WHERE k = '{user}'"})
        ready, _ = self._run(ex, lambda sql: (0, "") if "u1" in sql else (1, "ERROR: usuario inexistente"))
        self.assertEqual(ready, ["ADMIN"])
        self.assertEqual(ex.actions[0].role, "CONSULTAS")


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
