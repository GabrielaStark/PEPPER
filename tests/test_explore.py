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
from pepper.explore import (config_problems, operator_note, outcome, plan_problems, plan_verdict,  # noqa: E402
                            plausible_value, write_session)
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
                                 [{"source": "explorer", "kind": "generic", "file": "explore.jsonl", "note": "x"}],
                                 {"status": "PARCIAL", "reason": "1/2 comprobaciones", "counts": {"pasos": 4}, "code": 3})
            session = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(session["timezone"], "+00:00")
            self.assertEqual(session["outcome"]["status"], "PARCIAL")
            self.assertNotIn("code", session["outcome"])
            self.assertEqual(validate_instance(session, "session"), [])


class SalidaHonestaTest(unittest.TestCase):
    """explore salía con 0 con cero trabajo (logins rechazados, 0 pantallas, plan sin un ok, sin http.jsonl)."""

    CONFIG = {"base_url": "http://127.0.0.1:18080", "login": {"route": "/login", "user_field": "#u", "password_field": "#p",
                                                               "submit": "#b", "identity_text": "{user}"},
              "roles": [{"name": "A", "user": "u", "password": "p"}]}

    def test_config_incompleto_se_dice_antes_de_arrancar(self):
        self.assertEqual(config_problems(self.CONFIG), [])
        problems = config_problems({"login": {"route": "/login"}, "roles": [{"name": "A"}], "credentials": {"sql": "x"}})
        self.assertTrue(any("base_url" in p for p in problems))
        self.assertTrue(any("login.submit" in p for p in problems))
        self.assertTrue(any("rol incompleto" in p for p in problems))
        self.assertTrue(any("db_name" in p for p in problems))

    def test_sin_texto_de_identidad_no_se_explora(self):
        config = json.loads(json.dumps(self.CONFIG))
        del config["login"]["identity_text"]
        self.assertTrue(any("identity_text" in p for p in config_problems(config)))
        config["roles"][0]["identity_text"] = "Bienvenido {user}"
        self.assertEqual(config_problems(config), [])

    def test_sin_ningun_rol_dentro_no_es_exito(self):
        verdict = outcome({"roles": {"A": "login rechazado", "B": "sin credencial"}, "routes": 19}, [], ["http.jsonl"])
        self.assertEqual((verdict["status"], verdict["code"]), ("FALLIDO", 1)); self.assertIn("ningún rol entró", verdict["reason"])

    def test_recorrido_real_es_completo_solo_con_http_jsonl(self):
        summary = {"roles": {"A": "19/19 pantallas, 6 rechazos provocados"}, "routes": 19}
        actions = [{"kind": "screen", "result": "ok"}]
        verdict = outcome(summary, actions, ["explore.jsonl", "http.jsonl"])
        self.assertEqual((verdict["status"], verdict["code"]), ("COMPLETO", 0))
        verdict = outcome(summary, actions, ["explore.jsonl"])
        self.assertEqual(verdict["code"], 1); self.assertIn("http.jsonl", verdict["reason"])

    def test_un_rol_fuera_o_un_tropiezo_del_explorador_es_parcial(self):
        actions = [{"kind": "screen", "result": "ok"}]
        verdict = outcome({"roles": {"A": "19/19 pantallas", "B": "identidad no confirmada"}}, actions, ["http.jsonl"])
        self.assertEqual((verdict["status"], verdict["code"]), ("PARCIAL", 3)); self.assertIn("B: identidad", verdict["reason"])
        actions.append({"kind": "empty_submit", "result": "error", "detail": {"falla": "explorador"}})
        self.assertEqual(outcome({"roles": {"A": "19/19 pantallas"}}, actions, ["http.jsonl"])["status"], "PARCIAL")
        # un 500 del sistema es lo que el sistema respondió, no un tropiezo del explorador
        actions[-1] = {"kind": "screen", "result": "error", "detail": {"falla": "sistema"}}
        self.assertEqual(outcome({"roles": {"A": "19/19 pantallas"}}, actions, ["http.jsonl"])["status"], "COMPLETO")

    def test_error_o_interrupcion_es_interrumpido(self):
        self.assertEqual(outcome({"error": "TimeoutError: x"}, [], ["http.jsonl"])["status"], "INTERRUMPIDO")
        verdict = outcome({"roles": {"A": "19/19 pantallas"}, "interrupted": "sí"}, [{"kind": "screen", "result": "ok"}], ["http.jsonl"])
        self.assertEqual((verdict["status"], verdict["code"]), ("INTERRUMPIDO", 4))


def _paso(n, tipo, result, falla=None, **declarado):
    detail = {"paso": n, "tipo": tipo, **declarado}
    if falla:
        detail["falla"] = falla
    return {"kind": "plan", "result": result, "detail": detail}


class VeredictoDelPlanTest(unittest.TestCase):
    """Un plan con 1 paso correcto y 9 fallidos salía con 0 (revisión 2026-09-24)."""

    def test_uno_bien_y_nueve_mal_no_es_exito(self):
        records = [_paso(1, "login", "ok")] + [_paso(i, "click", "error", "explorador", efecto="consulta") for i in range(2, 11)]
        verdict = outcome({"mode": "plan", "steps": 10}, records, ["http.jsonl"], plan_steps=10)
        self.assertEqual((verdict["status"], verdict["code"]), ("FALLIDO", 1))
        self.assertIn("ninguna comprobación", verdict["reason"])

    def test_guardado_comprobado_por_su_propia_comprobacion_es_completo(self):
        records = [_paso(1, "login", "ok"), _paso(2, "click_at", "ok", efecto="modifica", id="registrar"),
                   _paso(3, "expect_text", "ok", comprueba="registrar")]
        verdict = plan_verdict(records, 3)
        self.assertEqual(verdict["status"], "COMPLETO")
        self.assertEqual((verdict["counts"]["modifican"], verdict["counts"]["modifican_comprobadas"]), (1, 1))

    def test_comprobaciones_de_otras_pantallas_no_cuentan_por_el_guardado(self):
        # el caso de la revisión de main: click_at "#j_idt42" guarda; las comprobaciones que pasan son de otra cosa
        records = [_paso(1, "login", "ok"), _paso(2, "expect_route", "ok"),
                   _paso(3, "click_at", "ok", efecto="modifica", id="registrar"),
                   _paso(4, "goto", "ok"), _paso(5, "expect_text", "ok")]
        verdict = plan_verdict(records, 5)
        self.assertEqual(verdict["status"], "FALLIDO")
        self.assertEqual(verdict["counts"]["fallas"], {"sin_comprobar": 1})
        self.assertIn("0/1 acciones que modifican datos comprobadas", verdict["reason"])

    def test_su_comprobacion_falla_y_otras_pasan_es_fallido(self):
        records = [_paso(1, "login", "ok"), _paso(2, "click", "ok", efecto="modifica", id="guardar"),
                   _paso(3, "expect_text", "error", "verificacion", comprueba="guardar"), _paso(4, "expect_route", "ok")]
        self.assertEqual(plan_verdict(records, 4)["status"], "FALLIDO")

    def test_un_guardado_comprobado_y_otro_no_es_parcial(self):
        records = [_paso(1, "login", "ok"), _paso(2, "click", "ok", efecto="modifica", id="a"),
                   _paso(3, "expect_text", "ok", comprueba="a"), _paso(4, "click", "ok", efecto="modifica", id="b"),
                   _paso(5, "expect_text", "error", "verificacion", comprueba="b")]
        verdict = outcome({"mode": "plan", "steps": 5}, records, ["http.jsonl"], plan_steps=5)
        self.assertEqual((verdict["status"], verdict["code"]), ("PARCIAL", 3))
        self.assertEqual(verdict["counts"]["fallas"], {"verificacion": 1})

    def test_rechazo_declarado_es_resultado_de_negocio_no_falla(self):
        records = [_paso(1, "login", "ok"), _paso(2, "click", "rejected", "negocio", efecto="modifica", id="vacio"),
                   _paso(3, "expect_rejected", "ok", comprueba="vacio")]
        verdict = plan_verdict(records, 3)
        self.assertEqual(verdict["status"], "COMPLETO")
        self.assertEqual(verdict["counts"]["rechazos_esperados"], 1)

    def test_rechazo_no_declarado_es_falla_de_negocio(self):
        records = [_paso(1, "login", "ok"), _paso(2, "click", "rejected", "negocio", efecto="consulta"), _paso(3, "expect_text", "ok")]
        verdict = plan_verdict(records, 3)
        self.assertEqual(verdict["status"], "PARCIAL")
        self.assertEqual(verdict["counts"]["fallas"], {"negocio": 1})

    def test_consulta_tras_la_cual_el_sistema_dice_que_guardo_no_es_completo(self):
        records = [_paso(1, "login", "ok"), _paso(2, "click_at", "ok", "declaracion", efecto="consulta"), _paso(3, "expect_text", "ok")]
        verdict = plan_verdict(records, 3)
        self.assertEqual(verdict["status"], "PARCIAL")
        self.assertEqual(verdict["counts"]["fallas"], {"declaracion": 1})

    def test_pasos_sin_correr_es_interrumpido(self):
        records = [_paso(1, "login", "ok"), _paso(2, "click", "ok", efecto="consulta"), _paso(3, "expect_text", "ok")]
        verdict = outcome({"mode": "plan", "steps": 8, "interrupted": "presupuesto"}, records, ["http.jsonl"], plan_steps=8)
        self.assertEqual((verdict["status"], verdict["code"]), ("INTERRUMPIDO", 4))
        self.assertEqual(verdict["counts"]["sin_correr"], 5)


class ProblemasDelPlanTest(unittest.TestCase):
    def test_un_clic_sin_efecto_declarado_no_se_corre(self):
        # un selector no dice nada: "#j_idt42" puede guardar; el plan lo tiene que declarar
        problems = plan_problems([{"login": "A"}, {"click_at": "#j_idt42"}, {"expect_text": "x"}])
        self.assertEqual(len(problems), 1); self.assertIn("no declara su efecto", problems[0])

    def test_lo_que_modifica_necesita_su_propia_comprobacion(self):
        plan = [{"login": "A"}, {"click_at": "#j_idt42", "efecto": "modifica", "id": "registrar"},
                {"goto": "/otra"}, {"expect_text": "Bienvenido"}]
        problems = plan_problems(plan)
        self.assertEqual(len(problems), 1); self.assertIn('"comprueba": "registrar"', problems[0])
        self.assertTrue(any("sin" in p or "id" in p for p in plan_problems([{"login": "A"}, {"click": "Guardar", "efecto": "modifica"},
                                                                        {"expect_text": "x"}])))

    def test_la_comprobacion_puede_llegar_despues_y_con_otro_rol(self):
        plan = [{"login": "RECEPCION"}, {"goto": "/cita"}, {"click": "Guardar", "efecto": "modifica", "id": "cita"},
                {"logout": True}, {"login": "SUPERVISOR"}, {"goto": "/citas"}, {"expect_text": "Prueba PEPPER", "comprueba": "cita"},
                {"click": "Buscar", "efecto": "consulta"}]
        self.assertEqual(plan_problems(plan), [])

    def test_consulta_con_texto_de_guardar_dice_por_que(self):
        base = [{"login": "A"}, {"expect_route": "/home"}]
        problems = plan_problems(base + [{"click": "Generar reporte", "efecto": "consulta"}])
        self.assertTrue(any("porque" in p for p in problems))
        self.assertEqual(plan_problems(base + [{"click": "Generar reporte", "efecto": "consulta",
                                                "porque": "descarga un PDF; no guarda nada"}]), [])

    def test_comprueba_nombra_un_paso_anterior_y_los_ids_no_se_repiten(self):
        problems = plan_problems([{"login": "A"}, {"expect_text": "x", "comprueba": "despues"},
                                  {"click": "Guardar", "efecto": "modifica", "id": "despues"},
                                  {"click": "Enviar", "efecto": "modifica", "id": "despues"}])
        self.assertTrue(any("paso ANTERIOR" in p for p in problems))
        self.assertTrue(any("repetido" in p for p in problems))

    def test_expect_rejected_dice_que_rechazo_esperaba(self):
        problems = plan_problems([{"login": "A"}, {"click": "Buscar", "efecto": "consulta", "id": "b"}, {"expect_rejected": True}])
        self.assertTrue(any("expect_rejected" in p for p in problems))
        self.assertEqual(plan_problems([{"login": "A"}, {"click": "Buscar", "efecto": "consulta", "id": "b"},
                                        {"expect_rejected": True, "comprueba": "b"}]), [])

    def test_plan_sin_ninguna_comprobacion_ni_claves_raras(self):
        problems = plan_problems([{"login": "A"}, {"click": "Buscar", "efecto": "consulta"}, {"teletransportar": 1},
                                  {"goto": "/x", "efecto": "quizas"}, {"goto": "/y", "prisa": True}])
        self.assertTrue(any("no comprueba nada" in p for p in problems))
        self.assertTrue(any("exactamente UNA acción" in p for p in problems))
        self.assertTrue(any("quizas" in p for p in problems))
        self.assertTrue(any("prisa" in p for p in problems))
        self.assertTrue(plan_problems([]))


class _Page:
    """Una página falsa: suficiente para run_plan sin navegador."""

    def __init__(self, texts=()):
        self.url = "http://127.0.0.1:18080/inicio"
        self.texts = list(texts)

    def goto(self, url):
        self.url = url
        return None

    def get_by_text(self, text):
        from unittest import mock
        return mock.Mock(count=lambda: sum(1 for t in self.texts if text in t))

    def wait_for_timeout(self, ms):
        pass

    def locator(self, selector):
        from unittest import mock
        return mock.Mock()  # .first.click() funciona: el clic "ocurre"


def _fake_explorer(page, messages=(), clicks_ok=True):
    import io
    from pepper.explore import Explorer
    ex = Explorer.__new__(Explorer)
    ex.config = {"login": {"route": "/login", "identity_text": "{user}"}, "roles": [{"name": "A", "user": "u", "password": "p"}]}
    ex.actions, ex._log, ex.base, ex.timeout_ms = [], io.StringIO(), "http://127.0.0.1:18080", 1000
    ex.ready, ex.deadline, ex._page = ["A"], None, page
    ex._settle = lambda ms=0: None
    ex._shot = lambda name: ""
    ex._messages = lambda: list(messages)
    ex._click_button = lambda name: clicks_ok
    ex.login = lambda role: True
    return ex


class RunPlanTest(unittest.TestCase):
    GUARDAR = [{"login": "A"}, {"click": "Guardar", "efecto": "modifica", "id": "g"}]

    def test_con_el_presupuesto_vencido_no_corre_ni_un_paso_mas(self):
        ex = _fake_explorer(_Page())
        ex.deadline = 1.0  # 1970: ya venció
        summary = ex.run_plan(self.GUARDAR + [{"expect_text": "ok", "comprueba": "g"}])
        self.assertEqual(summary["executed"], 0)
        self.assertIn("presupuesto", summary["interrupted"])
        verdict = outcome(summary, [a.record() for a in ex.actions], ["http.jsonl"], plan_steps=3)
        self.assertEqual(verdict["status"], "INTERRUMPIDO")

    def test_clic_que_el_sistema_rechaza_y_el_plan_lo_esperaba(self):
        ex = _fake_explorer(_Page(), messages=["La CURP es obligatoria"])
        summary = ex.run_plan(self.GUARDAR + [{"expect_rejected": "CURP", "comprueba": "g"}])
        records = [a.record() for a in ex.actions]
        self.assertEqual([r["result"] for r in records], ["ok", "rejected", "ok"])
        self.assertEqual(records[1]["detail"]["falla"], "negocio")
        self.assertEqual((records[1]["detail"]["efecto"], records[2]["detail"]["comprueba"]), ("modifica", "g"))
        self.assertEqual(outcome(summary, records, ["http.jsonl"], plan_steps=3)["status"], "COMPLETO")

    def test_boton_que_no_se_encuentra_es_falla_del_explorador(self):
        ex = _fake_explorer(_Page(texts=["Guardado"]), clicks_ok=False)
        summary = ex.run_plan(self.GUARDAR + [{"expect_text": "Guardado", "comprueba": "g"}, {"expect_route": "/inicio"}])
        records = [a.record() for a in ex.actions]
        self.assertEqual(records[1]["detail"]["falla"], "explorador")
        self.assertEqual(outcome(summary, records, ["http.jsonl"], plan_steps=4)["status"], "PARCIAL")

    def test_comprobacion_que_no_se_cumple_es_falla_de_verificacion(self):
        ex = _fake_explorer(_Page(texts=[]))
        summary = ex.run_plan(self.GUARDAR + [{"expect_text": "Solicitud registrada", "comprueba": "g"}])
        records = [a.record() for a in ex.actions]
        self.assertEqual(records[2]["detail"]["falla"], "verificacion")
        self.assertEqual(outcome(summary, records, ["http.jsonl"], plan_steps=3)["status"], "FALLIDO")

    PLAN_CONSULTA = [{"login": "A"}, {"click_at": "#j_idt42", "efecto": "consulta"}, {"expect_text": "Bienvenido"}]

    def test_consulta_que_termina_en_guardado_contradice_la_declaracion(self):
        ex = _fake_explorer(_Page(texts=["Bienvenido"]))
        shown = iter([[], ["Se guardó el registro"]])            # antes del clic, nada; después, el sistema dice que guardó
        ex._messages = lambda: next(shown, ["Se guardó el registro"])
        summary = ex.run_plan(self.PLAN_CONSULTA)
        records = [a.record() for a in ex.actions]
        self.assertEqual(records[1]["detail"]["falla"], "declaracion")
        self.assertEqual(outcome(summary, records, ["http.jsonl"], plan_steps=3)["status"], "PARCIAL")

    def test_un_aviso_que_ya_estaba_en_pantalla_no_cuenta(self):
        ex = _fake_explorer(_Page(texts=["Bienvenido"]), messages=["Se guardó el registro"])  # estaba antes del clic
        summary = ex.run_plan(self.PLAN_CONSULTA)
        records = [a.record() for a in ex.actions]
        self.assertNotIn("falla", records[1]["detail"])
        self.assertEqual(outcome(summary, records, ["http.jsonl"], plan_steps=3)["status"], "COMPLETO")


class ContextoPorRolTest(unittest.TestCase):
    """Un contexto de navegador por rol: cookies, localStorage y sessionStorage no cruzan de un rol a otro."""

    def _explorer(self, body_text):
        import io
        from unittest import mock
        from pepper.explore import Explorer
        ex = Explorer.__new__(Explorer)
        ex.config = {"login": {"route": "/login", "user_field": "#u", "password_field": "#p", "submit": "#b",
                               "identity_text": "Usuario: {user}"}, "logout_route": "/salir",
                     "roles": [{"name": "A", "user": "ana", "password": "p"}]}
        ex.actions, ex._log, ex.base, ex.timeout_ms = [], io.StringIO(), "http://127.0.0.1:18080", 1000
        ex._settle = lambda ms=0: None
        ex._shot = lambda name: ""
        ex._messages = lambda: []
        contexts = []

        def new_context(**kwargs):
            page = mock.Mock(url="http://127.0.0.1:18080/inicio")
            page.locator.return_value.inner_text.return_value = body_text
            context = mock.Mock()
            context.new_page.return_value = page
            contexts.append(context)
            return context

        ex._browser = mock.Mock(new_context=new_context)
        ex._context, ex._page = None, None
        ex._fresh_context()
        return ex, contexts

    def test_cada_login_estrena_contexto_y_salir_lo_descarta(self):
        ex, contexts = self._explorer("Usuario: ana")
        self.assertTrue(ex.login(ex.config["roles"][0]))
        self.assertEqual(len(contexts), 2)
        contexts[0].close.assert_called_once()
        ex.logout("A")
        self.assertEqual(len(contexts), 3)
        contexts[1].close.assert_called_once()
        self.assertEqual(ex.actions[0].detail["identidad"], "confirmada")

    def test_sin_la_identidad_en_pantalla_no_se_explora_con_ese_rol(self):
        ex, _ = self._explorer("Usuario: otro")
        self.assertFalse(ex.login(ex.config["roles"][0]))
        self.assertEqual(ex.actions[0].result, "error")
        self.assertEqual(ex.actions[0].detail["falla"], "identidad")


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

    def test_la_clave_no_queda_en_el_log_de_la_base_ni_en_el_error(self):
        ex = self._explorer({"db_name": "d", "sql": "UPDATE u SET p = crypt('{password}') WHERE k = '{user}'"})
        ex.config["roles"] = [{"name": "ADMIN", "user": "u1", "password": "Clave-XYZ"}]
        def responder(sql):
            return (1, "ERROR:  syntax error\nLINE 1: UPDATE u SET p = crypt('Clave-XYZ') WHERE k = 'u1'\n        ^") if "u1" in sql else (0, "")
        with self.assertRaises(RuntimeError) as raised:
            _, calls = self._run(ex, responder)
        self.assertNotIn("Clave-XYZ", str(raised.exception))
        self.assertNotIn("Clave-XYZ", ex.actions[0].detail.get("stderr", ""))
        self.assertNotIn("LINE 1", ex.actions[0].detail.get("stderr", ""))

    def test_cada_sql_apaga_el_log_de_sentencias_en_su_sesion(self):
        ex = self._explorer({"db_name": "d", "sql": "UPDATE u SET p = 1 WHERE k = '{user}'"})
        _, calls = self._run(ex, lambda sql: (0, ""))
        self.assertTrue(all(c.startswith("SET log_statement = 'none'; ") for c in calls), calls)

    def test_setup_sql_corre_una_vez_antes_de_los_roles(self):
        ex = self._explorer({"db_name": "d", "setup_sql": "CREATE EXTENSION IF NOT EXISTS pgcrypto",
                             "sql": "UPDATE u SET p = crypt('{password}') WHERE k = '{user}'"})
        ready, calls = self._run(ex, lambda sql: (0, ""))
        self.assertEqual(ready, ["ADMIN", "CONSULTAS"])
        self.assertEqual(calls[0], "SET log_statement = 'none'; CREATE EXTENSION IF NOT EXISTS pgcrypto")
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
            self._run(ex, lambda sql: (1, "ERROR:  extension nada") if "CREATE EXTENSION" in sql else (0, ""))
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
