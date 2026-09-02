"""Stub de servicios externos para el entorno rehidratado.

Todo host externo que el artefacto invoque (buses, APIs, SMTP, sistemas por IP
fija) se resuelve por alias DNS a este stub. Responde 503 a todo y registra cada
petición en JSON por stdout: ese registro es evidencia de qué dependencias
externas invoca cada flujo observado — y garantiza que el entorno jamás llame a
un servicio real con credenciales reales.

Autocontenido a propósito (solo stdlib, sin imports de `pepper`): Rehydrate lo
copia a `pepper-out/rehydrate/stub/stub.py` y el compose lo monta `:ro` en un
`python:3-alpine`:

    python3 -u stub.py --ports 80,8080,9980,443,587

Los puertos son los que el artefacto espera de sus servicios externos; el perfil
o el inspector los dictan.
"""

from __future__ import annotations

import argparse
import json
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_PORTS = "80,443,8080"


class StubHandler(BaseHTTPRequestHandler):
    def _log(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        print(json.dumps({
            "ts": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "port": self.server.server_address[1],
            "method": self.command,
            "path": self.path,
            "host": self.headers.get("Host"),
            "client": self.client_address[0],
            "body_bytes": len(body),
        }, ensure_ascii=False), flush=True)
        self.send_response(503)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"stub":"pepper","status":"external service not available in rehydrated environment"}')

    do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = do_OPTIONS = do_PATCH = _log

    def log_message(self, *args) -> None:  # el stdout es el registro JSON; nada más
        pass


def serve(port: int) -> None:
    ThreadingHTTPServer.allow_reuse_address = True
    ThreadingHTTPServer(("0.0.0.0", port), StubHandler).serve_forever()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="stub de servicios externos de PEPPER: 503 a todo, registro JSON por stdout")
    parser.add_argument("--ports", default=DEFAULT_PORTS,
                        help=f"puertos a escuchar, separados por coma (default {DEFAULT_PORTS})")
    args = parser.parse_args(argv)
    ports = [int(p) for p in args.ports.split(",") if p.strip()]
    for port in ports:
        threading.Thread(target=serve, args=(port,), daemon=True).start()
    print(json.dumps({"stub": "pepper", "listening": ports}), flush=True)
    threading.Event().wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
