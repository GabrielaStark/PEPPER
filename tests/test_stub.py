"""El stub registra cada conexión externa (HTTP, TLS, protocolos donde el servidor habla primero)
sin dejar secretos en claro en la URL, y la cierra rápido."""

import contextlib
import io
import json
import socket
import sys
import threading
import time
import unittest
from http.client import HTTPConnection
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pepper.stub import StubConnection, StubServer, classify  # noqa: E402
from tests.entorno import exige_sockets_loopback  # noqa: E402


class StubTest(unittest.TestCase):
    def setUp(self):
        exige_sockets_loopback()   # sin permiso para abrir sockets, se salta con motivo (en CI, falla)
        self.server = StubServer(("127.0.0.1", 0), StubConnection)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.out = io.StringIO()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def _entries(self):
        return [json.loads(l) for l in self.out.getvalue().splitlines() if l.startswith("{")]

    def test_el_query_va_aparte_y_redactado(self):
        with contextlib.redirect_stdout(self.out):
            c = HTTPConnection(*self.server.server_address, timeout=5)
            c.request("GET", "/callback?token=SECRETO&folio=77")
            self.assertEqual(c.getresponse().status, 503)
            c.close()
            time.sleep(0.2)
        entry = self._entries()[-1]
        self.assertEqual(entry["protocol"], "http")
        self.assertEqual(entry["path"], "/callback")
        self.assertEqual(entry["query"], {"token": "[REDACTADO]", "folio": "77"})
        self.assertNotIn("SECRETO", self.out.getvalue())

    def test_un_client_hello_tls_queda_registrado_y_la_conexion_se_cierra(self):
        with contextlib.redirect_stdout(self.out):
            s = socket.create_connection(self.server.server_address, timeout=5)
            s.sendall(b"\x16\x03\x01\x00\xf1\x01\x00\x00\xed\x03\x03" + b"\x00" * 32)
            data = s.recv(64)
            s.close()
            time.sleep(0.2)
        self.assertEqual(data, b"")   # cerrada sin handshake: el cliente ve un fallo inmediato
        self.assertEqual(self._entries()[-1]["protocol"], "tls")

    def test_un_cliente_que_espera_al_servidor_recibe_un_421_y_queda_registrado(self):
        with contextlib.redirect_stdout(self.out):
            s = socket.create_connection(self.server.server_address, timeout=5)
            banner = s.recv(128)
            s.close()
            time.sleep(0.2)
        self.assertTrue(banner.startswith(b"421 pepper stub"))
        self.assertEqual(self._entries()[-1]["protocol"], "silent-client")

    def test_clasificacion_por_primeros_bytes(self):
        self.assertEqual(classify(b""), "silent-client")
        self.assertEqual(classify(b"\x16\x03\x01"), "tls")
        self.assertEqual(classify(b"POST /x HTTP/1.1\r\n"), "http")
        self.assertEqual(classify(b"\x00\x00\x00\x0a\x01"), "other")


if __name__ == "__main__":
    unittest.main()
