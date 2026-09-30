"""Stub de servicios externos para el entorno rehidratado.

Todo host externo que el artefacto invoque (buses, APIs, SMTP, LDAP, sistemas por IP
fija) se resuelve por alias DNS a este stub. Registra CADA conexión en JSON por stdout
y la cierra rápido: ese registro es evidencia de qué dependencias externas invoca
cada flujo observado, y garantiza que el entorno jamás llame a un servicio real con
credenciales reales.

Lo que llega se clasifica por sus primeros bytes, sin suponer HTTP (antes solo HTTP en
claro dejaba rastro; un `https://`, un SMTP o un LDAP colgaban sin evidencia hasta el
timeout del cliente — auditoría 2026-09-29):

  http           una petición HTTP en claro: se lee la línea y los headers, se responde 503
  tls            un ClientHello (0x16 0x03): se cierra; el cliente ve un handshake fallido
  silent-client  el cliente espera que el servidor hable primero (SMTP, MySQL, FTP…): se le
                 manda `421 pepper stub …` y se cierra
  other          cualquier otro protocolo: se cierra

Autocontenido a propósito (solo stdlib, sin imports de `pepper`): Rehydrate lo copia a
`pepper-out/rehydrate/stub/stub.py` y el compose lo monta `:ro` en un `python:3-alpine`:

    python3 -u stub.py --ports 80,8080,9980,443,587

Los puertos son los que el artefacto espera de sus servicios externos; el perfil o el
inspector los dictan.
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import socketserver
import threading
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

DEFAULT_PORTS = "80,443,8080"
FIRST_BYTES_TIMEOUT_S = 1.5     # cuánto se espera a que el cliente hable antes de hablarle nosotros
MAX_HEADER_BYTES = 64 * 1024
BANNER = b"421 pepper stub: servicio externo no disponible en el entorno rehidratado\r\n"
HTTP_503 = (b"HTTP/1.1 503 Service Unavailable\r\nContent-Type: application/json\r\nConnection: close\r\n"
            b"Content-Length: %d\r\n\r\n%s")
HTTP_BODY = b'{"stub":"pepper","status":"external service not available in rehydrated environment"}'

_SECRET_FIELD_RE = re.compile(r"(?i)(pass|pwd|psw|contrase|clave|secret|token|credencial|authorization)")
_REQUEST_LINE_RE = re.compile(rb"^([A-Z]{3,10}) (\S+) HTTP/1\.[01]\r?\n")
_HTTP_START_RE = re.compile(rb"^[A-Z]{3,10} \S")   # con 16 bytes basta para saber que es HTTP


def _query(path: str) -> dict:
    """El query viaja aparte y redactado: un callback con ?token=… lo dejaba entero (auditoría 2026-09-11)."""
    raw = urlsplit(path).query
    if not raw:
        return {}
    pairs = parse_qs(raw, keep_blank_values=True)
    return {"query": {k: ("[REDACTADO]" if _SECRET_FIELD_RE.search(k) else (v[0] if len(v) == 1 else v))
                      for k, v in pairs.items()}}


def _log(entry: dict) -> None:
    print(json.dumps({"ts": datetime.now().astimezone().isoformat(timespec="milliseconds"), **entry},
                     ensure_ascii=False), flush=True)


def classify(first: bytes) -> str:
    """Qué protocolo parece por los primeros bytes."""
    if not first:
        return "silent-client"
    if first[:1] == b"\x16" and first[1:2] == b"\x03":
        return "tls"
    if _HTTP_START_RE.match(first):
        return "http"
    return "other"


class StubConnection(socketserver.StreamRequestHandler):
    timeout = 10

    def handle(self) -> None:
        port = self.server.server_address[1]
        client = self.client_address[0]
        self.connection.settimeout(FIRST_BYTES_TIMEOUT_S)
        try:
            first = self.connection.recv(64, socket.MSG_PEEK)
        except socket.timeout:
            first = b""
        except OSError:
            return
        protocol = classify(first)
        entry = {"port": port, "client": client, "protocol": protocol}
        if protocol == "http":
            self.connection.settimeout(self.timeout)
            self._http(entry)
            return
        if protocol == "silent-client":
            try:
                self.connection.sendall(BANNER)
            except OSError:
                pass
        _log(entry)

    def _http(self, entry: dict) -> None:
        try:
            head = b""
            while b"\r\n\r\n" not in head and b"\n\n" not in head and len(head) < MAX_HEADER_BYTES:
                chunk = self.connection.recv(4096)
                if not chunk:
                    break
                head += chunk
        except OSError:
            head = b""
        match = _REQUEST_LINE_RE.match(head)
        if not match:
            entry["protocol"] = "other"
            _log(entry)
            return
        method, path = match.group(1).decode("ascii", "replace"), match.group(2).decode("utf-8", "replace")
        headers = {}
        for line in head.split(b"\n", 1)[1].split(b"\n"):
            name, _, value = line.decode("latin-1").partition(":")
            if _:
                headers[name.strip().lower()] = value.strip()
        try:
            length = int(headers.get("content-length") or 0)
        except ValueError:
            length = 0
        body_bytes = min(len(head.split(b"\r\n\r\n", 1)[1]) if b"\r\n\r\n" in head else 0, length)
        while body_bytes < length:
            try:
                chunk = self.connection.recv(min(65536, length - body_bytes))
            except OSError:
                break
            if not chunk:
                break
            body_bytes += len(chunk)
        entry.update({"method": method, "path": path.partition("?")[0], **_query(path),
                      "host": headers.get("host"), "body_bytes": body_bytes})
        _log(entry)
        try:
            self.connection.sendall(HTTP_503 % (len(HTTP_BODY), HTTP_BODY))
        except OSError:
            pass


class StubServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


# Compatibilidad con quien importaba el handler HTTP: el mismo nombre, el mismo registro JSON.
StubHandler = StubConnection


def serve(port: int) -> None:
    StubServer(("0.0.0.0", port), StubConnection).serve_forever()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="stub de servicios externos de PEPPER: registra toda conexión y la cierra; 503 a HTTP")
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
