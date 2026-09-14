"""El stub registra cada llamada externa sin dejar secretos en claro en la URL."""

import contextlib
import io
import json
import sys
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pepper.stub import StubHandler  # noqa: E402


class StubTest(unittest.TestCase):
    def test_el_query_va_aparte_y_redactado(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                c = HTTPConnection(*server.server_address, timeout=5)
                c.request("GET", "/callback?token=SECRETO&folio=77")
                self.assertEqual(c.getresponse().status, 503)
                c.close()
        finally:
            server.shutdown(); server.server_close()
        entry = json.loads([l for l in out.getvalue().splitlines() if l.startswith("{")][-1])
        self.assertEqual(entry["path"], "/callback")
        self.assertEqual(entry["query"], {"token": "[REDACTADO]", "folio": "77"})
        self.assertNotIn("SECRETO", out.getvalue())


if __name__ == "__main__":
    unittest.main()
