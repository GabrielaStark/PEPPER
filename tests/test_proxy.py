"""Proxy HTTP de PEPPER: reenvía intacto, inyecta correlation_id, registra sin secretos.

Todo corre en 127.0.0.1 con puertos efímeros: un upstream de mentira, el proxy
delante, y peticiones reales de http.client. Nada sale de la máquina.
"""

import json
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pepper.correlate.parsers import HttpProxyParser  # noqa: E402
from pepper.proxy import CORRELATION_HEADER, ProxyServer, Recorder  # noqa: E402
from pepper.session import Session  # noqa: E402


class _UpstreamHandler(BaseHTTPRequestHandler):
    """App de mentira: refleja lo que recibió para poder afirmar sobre ello."""

    def log_message(self, *args):
        pass

    def _reply(self, status, payload, content_type="application/json", extra_headers=()):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for name, value in extra_headers:
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/echo-headers"):
            self._reply(200, {"correlation": self.headers.get(CORRELATION_HEADER),
                              "host": self.headers.get("Host"),
                              "accept_encoding": self.headers.get("Accept-Encoding")})
        elif self.path.startswith("/con-csp"):
            # un app que trae su propia política laxa y precarga de fuera
            self._reply(200, b"<html><head><title>x</title></head><body>hola</body></html>", content_type="text/html",
                        extra_headers=[("Content-Security-Policy", "default-src *"),
                                       ("Link", "<http://externo.example/a.js>; rel=preload; as=script")])
        elif self.path.startswith("/redirect"):
            self._reply(303, b"", content_type="text/plain", extra_headers=[("Location", "/destino")])
        elif self.path.startswith("/pagina"):
            self._reply(200, b"<html><body>hola</body></html>", content_type="text/html")
        else:
            self._reply(200, {"ok": True})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        if self.path.startswith("/rechazo"):
            self._reply(409, {"error": "El ciudadano no se encuentra activo"})
        else:
            self._reply(201, {"recibido": len(body),
                              "correlation": self.headers.get(CORRELATION_HEADER)})

    do_HEAD = do_GET


class _MemoryRecorder(Recorder):
    def __init__(self):
        super().__init__(out_path=None, stdout=False)
        self.entries = []

    def record(self, entry):
        with self._lock:
            self.entries.append(entry)


def _serve(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


class ProxyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = ThreadingHTTPServer(("127.0.0.1", 0), _UpstreamHandler)
        cls.upstream.daemon_threads = True
        _serve(cls.upstream)
        cls.recorder = _MemoryRecorder()
        cls.proxy = ProxyServer(("127.0.0.1", 0), cls.upstream.server_address,
                                cls.recorder, upstream_timeout=5.0)
        _serve(cls.proxy)

    @classmethod
    def tearDownClass(cls):
        cls.proxy.shutdown()
        cls.proxy.server_close()
        cls.upstream.shutdown()
        cls.upstream.server_close()

    def setUp(self):
        self.recorder.entries.clear()

    def _request(self, method, path, body=None, headers=None):
        connection = HTTPConnection(*self.proxy.server_address, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def _pair(self):
        """(petición, respuesta) del último intercambio registrado."""
        self.assertGreaterEqual(len(self.recorder.entries), 2)
        return self.recorder.entries[-2], self.recorder.entries[-1]

    def test_reenvia_intacto_e_inyecta_correlation_id(self):
        status, _, payload = self._request("GET", "/echo-headers")
        self.assertEqual(status, 200)
        echoed = json.loads(payload)
        request, response = self._pair()
        # el app recibió el header con el MISMO id que quedó en las dos líneas
        self.assertEqual(echoed["correlation"], request["correlation_id"])
        self.assertEqual(request["correlation_id"], response["correlation_id"])
        self.assertEqual(request["direction"], "request")
        self.assertEqual(response["direction"], "response")
        self.assertEqual(response["status"], 200)
        self.assertIn("duration_ms", response)

    def test_cuerpo_ilegible_responde_400_y_no_reenvia(self):
        import socket
        with socket.create_connection(self.proxy.server_address, timeout=5) as sock:
            sock.sendall(b"POST /login HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n"
                         b"zz\r\nhola\r\n0\r\n\r\n")
            raw = sock.recv(4096)
        self.assertTrue(raw.startswith(b"HTTP/1.1 400"), raw[:40])
        request, response = self._pair()
        self.assertEqual(response["status"], 400)
        self.assertIn("ilegible", response["note"])
        self.assertEqual(request["correlation_id"], response["correlation_id"])

    def test_preserva_host_del_cliente(self):
        _, _, payload = self._request("GET", "/echo-headers", headers={"Host": "legado.local:18080"})
        self.assertEqual(json.loads(payload)["host"], "legado.local:18080")

    def test_correlation_ids_unicos(self):
        for _ in range(3):
            self._request("GET", "/echo-headers")
        ids = {e["correlation_id"] for e in self.recorder.entries}
        self.assertEqual(len(ids), 3)

    def test_post_json_captura_cuerpo_y_respuesta_de_error(self):
        body = json.dumps({"citizenId": 1003, "tipoTramite": "LICENCIA"})
        status, _, _ = self._request("POST", "/rechazo", body=body,
                                     headers={"Content-Type": "application/json"})
        self.assertEqual(status, 409)
        request, response = self._pair()
        self.assertEqual(request["body"], {"citizenId": 1003, "tipoTramite": "LICENCIA"})
        self.assertEqual(response["body"], {"error": "El ciudadano no se encuentra activo"})

    def test_redacta_credenciales_en_formularios_y_json(self):
        form = "usuario=operadora1&password=SuperSecreta1&recordar=on"
        self._request("POST", "/login", body=form,
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
        request, _ = self._pair()
        self.assertEqual(request["body"]["usuario"], "operadora1")
        self.assertEqual(request["body"]["password"], "[REDACTADO]")
        self.assertEqual(request["body"]["recordar"], "on")

        payload = json.dumps({"user": "x", "credenciales": {"clave": "abc", "token": "t"}, "dato": 1})
        self._request("POST", "/api", body=payload, headers={"Content-Type": "application/json"})
        request, _ = self._pair()
        self.assertEqual(request["body"]["credenciales"], "[REDACTADO]")
        self.assertEqual(request["body"]["dato"], 1)
        self.assertNotIn("SuperSecreta1", json.dumps(self.recorder.entries))

    def test_no_registra_headers_de_credenciales(self):
        self._request("GET", "/echo-headers",
                      headers={"Authorization": "Bearer secreto-xyz", "Cookie": "JSESSIONID=abc123"})
        serialized = json.dumps(self.recorder.entries)
        self.assertNotIn("secreto-xyz", serialized)
        self.assertNotIn("abc123", serialized)

    def test_query_string_se_separa_y_redacta(self):
        # /reset?token=… llevaba el secreto completo dentro de "path" (auditoría H-05)
        self._request("GET", "/reset?token=super-secreto-123&usuario=ana&modo=web")
        request, response = self._pair()
        self.assertEqual(request["path"], "/reset")
        self.assertEqual(response["path"], "/reset")
        self.assertEqual(request["query"]["token"], "[REDACTADO]")
        self.assertEqual(request["query"]["usuario"], "ana")
        self.assertEqual(request["query"]["modo"], "web")
        self.assertNotIn("super-secreto-123", json.dumps(self.recorder.entries))

    def test_json_malformado_no_se_registra_crudo(self):
        # antes, {"password":"SECRETO",} se registraba como texto crudo (auditoría H-05)
        self._request("POST", "/api", body='{"password":"SECRETO-A-MEDIAS",}',
                      headers={"Content-Type": "application/json"})
        request, _ = self._pair()
        self.assertNotIn("body", request)
        self.assertIn("body_bytes", request)
        self.assertNotIn("SECRETO-A-MEDIAS", json.dumps(self.recorder.entries))

    def test_html_no_se_captura_solo_se_reenvia(self):
        status, headers, payload = self._request("GET", "/pagina")
        self.assertEqual(status, 200)
        self.assertIn(b"hola", payload)
        _, response = self._pair()
        self.assertNotIn("body", response)  # una página JSF completa no es evidencia reducible

    def test_redirect_pasa_intacto(self):
        status, headers, _ = self._request("GET", "/redirect")
        self.assertEqual(status, 303)
        self.assertEqual(headers.get("Location"), "/destino")

    def test_upstream_caido_responde_502_y_lo_registra(self):
        recorder = _MemoryRecorder()
        # upstream a un puerto cerrado de la propia máquina
        dead = ProxyServer(("127.0.0.1", 0), ("127.0.0.1", 1), recorder, upstream_timeout=2.0)
        _serve(dead)
        try:
            connection = HTTPConnection(*dead.server_address, timeout=5)
            connection.request("GET", "/lo-que-sea")
            response = connection.getresponse()
            self.assertEqual(response.status, 502)
            connection.close()
            self.assertEqual(recorder.entries[-1]["status"], 502)
            self.assertIn("upstream inalcanzable", recorder.entries[-1]["note"])
        finally:
            dead.shutdown()
            dead.server_close()

    # --- el navegador del humano es parte del perímetro ---

    def test_toda_respuesta_lleva_la_politica_del_navegador(self):
        from pepper.proxy import BROWSER_POLICY
        for path in ("/echo-headers", "/pagina", "/redirect"):
            _, headers, _ = self._request("GET", path)
            self.assertEqual(headers.get("Content-Security-Policy"), BROWSER_POLICY, path)
            self.assertEqual(headers.get("Referrer-Policy"), "no-referrer", path)
        self.assertIn("default-src 'self'", BROWSER_POLICY)
        self.assertIn("object-src 'self'", BROWSER_POLICY)      # el <object type=text/html> del caso real
        self.assertIn("frame-src 'self'", BROWSER_POLICY)
        self.assertIn("form-action 'self'", BROWSER_POLICY)
        self.assertIn("report-uri /__pepper/csp-report", BROWSER_POLICY)

    def test_el_html_recibe_el_guardian_y_lo_demas_no(self):
        status, headers, body = self._request("GET", "/pagina")
        self.assertEqual(status, 200)
        self.assertIn(b'data-pepper="guard"', body)
        # el fixture no tiene <head>: el guardián va justo tras <html>, antes de cualquier contenido
        self.assertLess(body.index(b'data-pepper="guard"'), body.lower().index(b"<body"))
        self.assertEqual(int(headers["Content-Length"]), len(body))
        _, _, json_body = self._request("GET", "/echo-headers")
        self.assertNotIn(b"data-pepper", json_body)

    def test_la_politica_del_app_se_reemplaza_y_el_preload_externo_se_quita(self):
        from pepper.proxy import BROWSER_POLICY
        _, headers, body = self._request("GET", "/con-csp")
        self.assertEqual(headers.get("Content-Security-Policy"), BROWSER_POLICY)
        self.assertNotIn("default-src *", str(headers))
        self.assertIsNone(headers.get("Link"))
        self.assertIn(b'data-pepper="guard"', body)

    def test_accept_encoding_no_se_reenvia(self):
        _, _, payload = self._request("GET", "/echo-headers", headers={"Accept-Encoding": "gzip, br"})
        self.assertIsNone(json.loads(payload)["accept_encoding"], "el cuerpo debe llegar plano para inyectar el guardián")

    def test_reporte_csp_queda_como_bloqueo_sin_correlation_id(self):
        report = {"csp-report": {"document-uri": "http://127.0.0.1:18080/cita",
                                 "blocked-uri": "https://servidor-real.example/iframe/calc?idTrabajador=77&token=SECRETO",
                                 "effective-directive": "object-src"}}
        status, _, _ = self._request("POST", "/__pepper/csp-report", body=json.dumps(report),
                                     headers={"Content-Type": "application/csp-report"})
        self.assertEqual(status, 204)
        entry = self.recorder.entries[-1]
        self.assertEqual(entry["direction"], "blocked")
        self.assertEqual(entry["kind"], "csp")
        self.assertEqual(entry["blocked_host"], "servidor-real.example")
        self.assertEqual(entry["blocked_uri"], "https://servidor-real.example/iframe/calc")
        self.assertEqual(entry["blocked_query"], {"idTrabajador": "77", "token": "[REDACTADO]"})
        self.assertEqual(entry["directive"], "object-src")
        self.assertNotIn("correlation_id", entry, "un bloqueo no es una petición: no debe anclar una traza")
        self.assertEqual(len([e for e in self.recorder.entries if e.get("direction") == "request"]), 0,
                         "el reporte no se registra como petición ni se reenvía al app")

    def test_reporte_del_guardian_queda_como_bloqueo(self):
        body = json.dumps({"kind": "window.open", "blocked_uri": "https://otro.example/x", "document_uri": "http://127.0.0.1:18080/home"})
        status, _, _ = self._request("POST", "/__pepper/nav-report", body=body, headers={"Content-Type": "text/plain"})
        self.assertEqual(status, 204)
        entry = self.recorder.entries[-1]
        self.assertEqual((entry["direction"], entry["kind"], entry["blocked_host"]), ("blocked", "window.open", "otro.example"))

    def test_get_al_endpoint_de_reportes_no_se_reenvia(self):
        status, _, _ = self._request("GET", "/__pepper/csp-report")
        self.assertEqual(status, 404)
        self.assertEqual(self.recorder.entries, [])

    def test_el_bloqueo_lo_lee_el_parser_como_evidencia_protegida(self):
        from pepper.proxy import blocked_record
        entry = blocked_record("csp", {"csp-report": {"blocked-uri": "https://servidor-real.example/calc?a=1", "document-uri": "http://127.0.0.1/cita"}})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "http.jsonl"
            path.write_text(json.dumps(entry) + "\n", encoding="utf-8")
            t0 = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
            session = Session(session_id="s", flow_name="f", observed_start=t0, observed_end=t0 + timedelta(hours=1),
                              tz=timezone.utc, collectors=[])
            events, unparsed = HttpProxyParser().parse_file(path, "http.jsonl", session)
        self.assertEqual(unparsed, [])
        event = events[0]
        self.assertEqual((event.event_type, event.severity, event.component), ("custom", "warn", "navegador"))
        self.assertIsNone(event.correlation_id)
        self.assertTrue(event.is_protected, "la reducción jamás descarta un bloqueo")
        self.assertIn("servidor-real.example", event.operation)

    def test_el_jsonl_lo_lee_el_parser_del_nucleo(self):
        self._request("POST", "/rechazo", body=json.dumps({"citizenId": 7}),
                      headers={"Content-Type": "application/json"})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "http.jsonl"
            path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in self.recorder.entries) + "\n",
                            encoding="utf-8")
            tz = timezone(timedelta(hours=-6))
            session = Session(session_id="flow-test", flow_name="prueba",
                              observed_start=datetime.now(tz) - timedelta(minutes=5),
                              observed_end=datetime.now(tz) + timedelta(minutes=5),
                              tz=tz, collectors=[])
            events, unparsed = HttpProxyParser().parse_file(path, "http.jsonl", session)
        self.assertEqual(unparsed, [])
        self.assertEqual(len(events), len(self.recorder.entries))
        kinds = {e.event_type for e in events}
        self.assertEqual(kinds, {"http_request", "http_response"})
        self.assertTrue(all(e.correlation_id for e in events))
        by_correlation = {}
        for event in events:
            by_correlation.setdefault(event.correlation_id, []).append(event)
        self.assertTrue(all(len(pair) == 2 for pair in by_correlation.values()))


if __name__ == "__main__":
    unittest.main()
