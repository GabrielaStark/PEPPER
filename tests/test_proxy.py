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
        elif self.path.startswith("/salto-externo"):
            self._reply(302, b"", content_type="text/plain",
                        extra_headers=[("Location", "https://servidor-real.invalid/panel?id=77&token=S")])
        elif self.path.startswith("/salto-a-mi-ip"):
            host, port = self.server.server_address
            self._reply(302, b"", content_type="text/plain", extra_headers=[("Location", f"http://{host}:{port}/destino?x=1")])
        elif self.path.startswith("/meta-refresh-barra"):
            # para el navegador `/\h/x` es `http://h/x` (WHATWG); urlsplit no le ve netloc
            self._reply(200, b'<html><head><meta http-equiv="refresh" content="0;url=/\\servidor-real.invalid/x">'
                             b'</head><body>x</body></html>', content_type="text/html")
        elif self.path.startswith("/meta-refresh"):
            self._reply(200, b'<html><head><meta http-equiv="refresh" content="0;url=https://servidor-real.invalid/x">'
                             b'<meta http-equiv="refresh" content="5;url=/local"></head><body>x</body></html>', content_type="text/html")
        elif self.path.startswith("/salto-"):
            # Location que urlsplit lee como "sin netloc" o "relativo" y el navegador como otro origen
            # (revisión 2026-09-22, P1): todos deben terminar en la página de bloqueo
            location = {
                "/salto-barra-invertida": "/\\servidor-real.invalid/path",
                "/salto-triple-barra": "///servidor-real.invalid/path",
                "/salto-esquema-sin-netloc": "http:///servidor-real.invalid/path",
                "/salto-javascript": "javascript:alert(1)",
                "/salto-con-tab": "/\t/servidor-real.invalid/x",
                "/salto-esquema-sin-barras": "https:servidor-real.invalid/x",
            }[self.path.partition("?")[0]]
            self._reply(302, b"", content_type="text/plain", extra_headers=[("Location", location)])
        elif self.path.startswith("/gzip"):
            import gzip
            self._reply(200, gzip.compress(b"<html><head></head><body>comprimido</body></html>"),
                        content_type="text/html", extra_headers=[("Content-Encoding", "gzip")])
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

    def test_chunk_declarado_enorme_se_rechaza_antes_de_leerlo(self):
        # P2-01 (auditoría 2026-09-21): un chunk de 64 MiB pedía 64 MiB de una vez
        import socket
        with socket.create_connection(self.proxy.server_address, timeout=5) as sock:
            sock.sendall(b"POST /login HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n"
                         b"4000000\r\nhola")
            raw = sock.recv(4096)
        self.assertTrue(raw.startswith(b"HTTP/1.1 413"), raw[:40])

    def test_cuerpo_truncado_es_400_no_vacio(self):
        import socket
        with socket.create_connection(self.proxy.server_address, timeout=5) as sock:
            sock.sendall(b"POST /login HTTP/1.1\r\nHost: x\r\nContent-Length: 20\r\n\r\nhola")
            sock.shutdown(socket.SHUT_WR)
            raw = sock.recv(4096)
        self.assertTrue(raw.startswith(b"HTTP/1.1 400"), raw[:40])
        _, response = self._pair()
        self.assertIn("truncado", response["note"])

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

    # --- navegación top-level: lo que la CSP no gobierna ---

    def test_un_3xx_hacia_otro_origen_se_reescribe_y_se_registra(self):
        status, headers, _ = self._request("GET", "/salto-externo")
        self.assertEqual(status, 302)
        self.assertEqual(headers.get("Location"), "/__pepper/blocked?to=servidor-real.invalid")
        entry = next(e for e in self.recorder.entries if e.get("direction") == "blocked")
        self.assertEqual((entry["kind"], entry["blocked_host"]), ("redirect", "servidor-real.invalid"))
        self.assertEqual(entry["blocked_query"]["token"], "[REDACTADO]")
        status, headers, body = self._request("GET", "/__pepper/blocked?to=servidor-real.invalid")
        self.assertEqual(status, 200)
        self.assertIn(b"servidor-real.invalid", body)
        self.assertIn("default-src 'self'", headers.get("Content-Security-Policy", ""))

    def test_un_3xx_hacia_la_ip_interna_del_app_se_vuelve_relativo(self):
        status, headers, _ = self._request("GET", "/salto-a-mi-ip")
        self.assertEqual(status, 302)
        self.assertEqual(headers.get("Location"), "/destino?x=1")
        self.assertFalse(any(e.get("direction") == "blocked" for e in self.recorder.entries))

    def test_un_3xx_que_solo_el_navegador_lee_como_otro_origen_tambien_se_bloquea(self):
        # P1 (revisión 2026-09-22): `/\h/p`, `///h/p`, `http:///h/p` pasaban intactos, "relativos"
        expected_to = {
            "/salto-barra-invertida": "destino-ambiguo",
            "/salto-triple-barra": "servidor-real.invalid",
            "/salto-esquema-sin-netloc": "servidor-real.invalid",
            "/salto-javascript": "javascript:",
            "/salto-con-tab": "destino-ambiguo",
            "/salto-esquema-sin-barras": "destino-ambiguo",
        }
        for path, to in expected_to.items():
            self.recorder.entries.clear()
            status, headers, _ = self._request("GET", path)
            self.assertEqual(status, 302, path)
            self.assertEqual(headers.get("Location"), f"/__pepper/blocked?to={to}", path)
            blocked = [e for e in self.recorder.entries if e.get("direction") == "blocked"]
            self.assertEqual(len(blocked), 1, path)
            self.assertEqual(blocked[0]["kind"], "redirect", path)
            self.assertEqual(blocked[0]["resolved_host"], to, path)

    def test_meta_refresh_con_barra_invertida_se_quita(self):
        _, _, body = self._request("GET", "/meta-refresh-barra")
        self.assertNotIn(b"servidor-real.invalid", body)
        self.assertIn(b"pepper: meta refresh hacia otro origen bloqueado", body)
        entry = next(e for e in self.recorder.entries if e.get("direction") == "blocked")
        self.assertEqual(entry["kind"], "meta-refresh")

    def test_meta_refresh_externo_se_quita_y_el_local_se_queda(self):
        _, _, body = self._request("GET", "/meta-refresh")
        self.assertNotIn(b"servidor-real.invalid", body)
        self.assertIn(b"pepper: meta refresh hacia otro origen bloqueado", body)
        self.assertIn(b'url=/local', body)
        entry = next(e for e in self.recorder.entries if e.get("direction") == "blocked")
        self.assertEqual((entry["kind"], entry["blocked_host"]), ("meta-refresh", "servidor-real.invalid"))

    def test_html_comprimido_se_sirve_plano_con_guardian(self):
        status, headers, body = self._request("GET", "/gzip")
        self.assertEqual(status, 200)
        self.assertIsNone(headers.get("Content-Encoding"))
        self.assertIn(b"comprimido", body)
        self.assertIn(b'data-pepper="guard"', body)
        self.assertEqual(int(headers["Content-Length"]), len(body))

    def test_cuerpo_gigante_responde_413_sin_leerlo(self):
        import socket
        with socket.create_connection(self.proxy.server_address, timeout=5) as sock:
            sock.sendall(b"POST /login HTTP/1.1\r\nHost: x\r\nContent-Length: 99999999999\r\n\r\nhola")
            raw = sock.recv(4096)
        self.assertTrue(raw.startswith(b"HTTP/1.1 413"), raw[:40])

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


class ClasificacionDeNavegacionTest(unittest.TestCase):
    """`classify_navigation` decide con la semántica del navegador (WHATWG URL), no con urlsplit."""

    OWN = {"127.0.0.1:18080", "10.4.2.10:8080", "10.4.2.10"}

    def test_lo_que_el_navegador_manda_a_otro_origen_se_bloquea(self):
        from pepper.proxy import classify_navigation
        for value, host in (("https://example.invalid/path", "example.invalid"),
                            ("///example.invalid/path", "example.invalid"),
                            ("http:///example.invalid/p", "example.invalid"),
                            (" //example.invalid", "example.invalid"),
                            ("http://u:p@example.invalid/", "example.invalid"),
                            ("http://127.0.0.1:18080@example.invalid/", "example.invalid"),
                            ("javascript:alert(1)", "javascript:"),
                            ("data:text/html,x", "data:"),
                            ("/\\example.invalid/path", "destino-ambiguo"),
                            ("/\t/example.invalid/x", "destino-ambiguo"),
                            ("\r\n//example.invalid", "destino-ambiguo"),
                            ("http:/ruta", "destino-ambiguo"),
                            ("https:example.invalid/x", "destino-ambiguo"),
                            ("//", "destino-ambiguo")):
            self.assertEqual(classify_navigation(value, self.OWN), ("blocked", host), repr(value))

    def test_lo_que_se_queda_en_el_ingress_se_queda(self):
        from pepper.proxy import classify_navigation
        for value, resolved in (("/destino", "/destino"), ("destino?x=1", "destino?x=1"), ("?x=1", "?x=1"),
                                ("#f", "#f"), ("", ""), ("1a:foo", "1a:foo"),
                                ("http://10.4.2.10:8080/d?x=1", "/d?x=1"), ("http://10.4.2.10/", "/"),
                                ("//127.0.0.1:18080/a/b?q=1#f", "/a/b?q=1"),
                                ("HTTP://10.4.2.10:8080/", "/")):
            self.assertEqual(classify_navigation(value, self.OWN), ("relative", resolved), repr(value))


class LectorChunkedTest(unittest.TestCase):
    """El framing chunked contra streams en memoria, sin sockets (P2, revisión 2026-09-22):
    tamaño hexadecimal no vacío, chunk cero explícito, CRLF exactos, trailers válidos."""

    def _read(self, raw):
        import email.message
        import io

        from pepper.proxy import PepperProxyHandler
        handler = PepperProxyHandler.__new__(PepperProxyHandler)
        handler.rfile = io.BytesIO(raw)
        handler.headers = email.message.Message()
        handler.headers["Transfer-Encoding"] = "chunked"
        return handler._read_request_body()

    def test_un_cuerpo_bien_formado_se_lee(self):
        self.assertEqual(self._read(b"4\r\nhola\r\n0\r\n\r\n"), b"hola")
        self.assertEqual(self._read(b"2;ext=1\r\nho\r\n2\r\nla\r\n0\r\nX-Checksum: abc\r\n\r\n"), b"hola")
        self.assertEqual(self._read(b"0\r\n\r\n"), b"")

    def test_los_finales_invalidos_son_peticion_mala(self):
        from pepper.proxy import _BadRequest
        for raw, reason in ((b"4\r\nhola\r\n\r\n", "sin chunk cero: la línea vacía no es un tamaño"),
                            (b"4\r\nhola\r\n0\r\n", "EOF antes del cierre de los trailers"),
                            (b"4\r\nhola\r\n0\r\ninvalid-header\r\n\r\n", "trailer sin sintaxis de cabecera"),
                            (b"4\r\nhola\n0\r\n\r\n", "terminador de chunk con LF solo"),
                            (b"4\nhola\r\n0\r\n\r\n", "tamaño con LF solo"),
                            (b"4\r\nhola\r\n0\r\n\n", "cierre de trailers con LF solo"),
                            (b"4\r\nhola\r\n0\r\nContent-Length: 4\r\n\r\n", "trailer prohibido"),
                            (b"4\r\nhola\r\n0\r\nX: a\x01b\r\n\r\n", "trailer con control"),
                            (b"-4\r\nhola\r\n0\r\n\r\n", "tamaño negativo"),
                            (b"\r\nhola\r\n0\r\n\r\n", "tamaño vacío"),
                            (b"4\r\nhol", "cuerpo truncado"),
                            (b"4\r\nhola", "sin terminador")):
            with self.assertRaises(_BadRequest, msg=reason):
                self._read(raw)

    def test_demasiados_trailers_es_peticion_mala(self):
        from pepper.proxy import _BadRequest
        trailers = b"".join(b"X-%d: v\r\n" % i for i in range(40))
        with self.assertRaises(_BadRequest):
            self._read(b"0\r\n" + trailers + b"\r\n")


class NavegacionHermeticaTest(unittest.TestCase):
    """Un Chromium real detrás del ingress: ninguna navegación sale de 127.0.0.1.

    Toda petición del navegador se intercepta y se anota; la que no vaya al ingress es fallo.
    Además el resolver de Chromium se inhabilita (todo nombre → NOTFOUND salvo 127.0.0.1), así
    que aun sin la intercepción nada saldría de la máquina. Se salta sin Playwright o sin Chromium."""

    PATHS = ("/salto-externo", "/salto-barra-invertida", "/salto-triple-barra", "/salto-esquema-sin-netloc",
             "/salto-javascript", "/salto-con-tab", "/salto-esquema-sin-barras", "/meta-refresh", "/meta-refresh-barra")

    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest("necesita playwright")
        cls.upstream = ThreadingHTTPServer(("127.0.0.1", 0), _UpstreamHandler)
        cls.upstream.daemon_threads = True
        _serve(cls.upstream)
        cls.recorder = _MemoryRecorder()
        cls.proxy = ProxyServer(("127.0.0.1", 0), cls.upstream.server_address, cls.recorder, upstream_timeout=5.0)
        _serve(cls.proxy)
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(
                args=["--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1"])
        except Exception as error:  # noqa: BLE001 — sin navegador instalado la prueba se salta, no falla
            cls.playwright.stop()
            cls.proxy.shutdown(); cls.proxy.server_close()
            cls.upstream.shutdown(); cls.upstream.server_close()
            raise unittest.SkipTest(f"chromium no disponible: {str(error).splitlines()[0]}")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.proxy.shutdown(); cls.proxy.server_close()
        cls.upstream.shutdown(); cls.upstream.server_close()

    def _navigate(self, origin, path):
        """(url final, contenido, peticiones fuera del origen) tras navegar a origin+path."""
        foreign = []
        context = self.browser.new_context()
        page = context.new_page()

        def note(request):
            if not request.url.startswith(origin + "/"):
                foreign.append(request.url)
        context.on("request", note)

        def handle(route, request):
            if request.url.startswith(origin + "/"):
                route.continue_()
            else:
                if request.url not in foreign:
                    foreign.append(request.url)
                route.abort()
        page.route("**/*", handle)
        try:
            page.goto(origin + path, wait_until="load", timeout=10000)
            page.wait_for_timeout(500)   # un meta refresh de 0 s dispara después de load
        except Exception:  # noqa: BLE001 — una navegación abortada es justo lo que se mide
            pass
        url = page.url
        try:
            content = page.content()
        except Exception:  # noqa: BLE001 — una página a medio navegar (abortada) no tiene contenido que leer
            content = ""
        context.close()
        return url, content, foreign

    def test_el_arnes_detecta_una_salida_real(self):
        # control positivo: sin el ingress, el mismo 302 SÍ intenta salir y el arnés lo ve
        host, port = self.upstream.server_address
        _, _, foreign = self._navigate(f"http://{host}:{port}", "/salto-externo")
        self.assertTrue(any("servidor-real.invalid" in u for u in foreign), foreign)

    def test_ninguna_navegacion_sale_del_ingress(self):
        host, port = self.proxy.server_address
        origin = f"http://{host}:{port}"
        for path in self.PATHS:
            url, content, foreign = self._navigate(origin, path)
            self.assertEqual(foreign, [], path)
            self.assertTrue(url.startswith(origin + "/"), (path, url))
            if path.startswith("/salto-"):
                self.assertIn("/__pepper/blocked", url, path)
                self.assertIn("bloqueada", content, path)
            else:
                self.assertNotIn("servidor-real.invalid", content, path)


if __name__ == "__main__":
    unittest.main()


class UrlSanitizationTest(unittest.TestCase):
    """Ninguna URL entra a http.jsonl con userinfo, query ni fragmento: ni la bloqueada ni la del documento.

    `document_uri` se guardaba entera (con `?token=…`) y `blocked_host`/`blocked_uri`
    conservaban `usuario:contraseña@host`; esa evidencia viaja en el paquete (revisión 2026-09-21).
    """

    def test_userinfo_query_y_fragmento_no_quedan_en_ninguna_url(self):
        from pepper.proxy import blocked_record

        entry = blocked_record("csp", {"csp-report": {
            "blocked-uri": "https://usuario:ClaveSecreta@servidor.example:8443/calc?token=TokenSecreto&id=7#frag",
            "document-uri": "http://127.0.0.1:18080/cita?token=OtroSecreto#x",
            "effective-directive": "frame-src"}})
        dumped = json.dumps(entry)
        for secret in ("ClaveSecreta", "TokenSecreto", "OtroSecreto", "usuario:", "#frag"):
            self.assertNotIn(secret, dumped)
        self.assertEqual(entry["blocked_host"], "servidor.example:8443")
        self.assertEqual(entry["blocked_uri"], "https://servidor.example:8443/calc")
        self.assertEqual(entry["blocked_path"], "/calc")
        self.assertEqual(entry["document_uri"], "http://127.0.0.1:18080/cita")
        self.assertEqual(entry["blocked_query"], {"token": "[REDACTADO]", "id": "7"})

    def test_el_reporte_del_guardian_recibe_la_misma_limpieza(self):
        from pepper.proxy import blocked_record

        entry = blocked_record("navigation", {
            "kind": "window.open",
            "blocked_uri": "https://u:ClaveSecreta@otro.example/x?password=Abc123",
            "document_uri": "http://u:OtraClave@127.0.0.1:18080/home?sessionToken=T0k3n"})
        dumped = json.dumps(entry)
        for secret in ("ClaveSecreta", "OtraClave", "Abc123", "T0k3n", "@"):
            self.assertNotIn(secret, dumped)
        self.assertEqual(entry["document_uri"], "http://127.0.0.1:18080/home")
        self.assertEqual(entry["blocked_uri"], "https://otro.example/x")

    def test_un_destino_sin_esquema_tampoco_conserva_nada_detras(self):
        from pepper.proxy import blocked_record

        for raw, expected in (("inline", "inline"), ("data", "data"),
                              ("u:ClaveSecreta@host?token=T0k3n#f", "host"),
                              ("//u:ClaveSecreta@host/x?token=T0k3n", "//host/x")):
            entry = blocked_record("navigation", {"kind": "form", "blocked_uri": raw, "document_uri": ""})
            self.assertEqual(entry["blocked_uri"], expected, raw)
            self.assertNotIn("ClaveSecreta", json.dumps(entry))
            self.assertNotIn("T0k3n", json.dumps(entry))
