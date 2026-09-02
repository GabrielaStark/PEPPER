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
                              "host": self.headers.get("Host")})
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

    def test_preserva_host_del_cliente(self):
        _, _, payload = self._request("GET", "/echo-headers", headers={"Host": "siat.local:18080"})
        self.assertEqual(json.loads(payload)["host"], "siat.local:18080")

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
        form = "usuario=gcarmona&password=SuperSecreta1&recordar=on"
        self._request("POST", "/login", body=form,
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
        request, _ = self._pair()
        self.assertEqual(request["body"]["usuario"], "gcarmona")
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
