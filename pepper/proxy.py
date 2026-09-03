"""Proxy HTTP de PEPPER: el ingress del entorno rehidratado.

Reenviador puro con memoria. Cada petición que entra recibe un `correlation_id`,
se reenvía intacta al app (con el header `X-Pepper-Correlation-Id` inyectado) y
deja dos líneas JSON —petición y respuesta— en el formato que lee el
`HttpProxyParser` del núcleo (`http.jsonl`). Es la pieza que amarra
petición → SQL → log en Correlate sin depender de afinidad ni ventana temporal.

Autocontenido a propósito: solo biblioteca estándar, sin imports del paquete
`pepper`, porque en el compose este archivo se monta solo dentro del contenedor
del ingress (`python:3-alpine`) y se ejecuta directo:

    python3 -u proxy.py --listen 0.0.0.0:8080 --upstream 10.4.2.10:8080

Las líneas salen por stdout, así `docker logs` del ingress ES el http.jsonl;
con `--out` también se escriben a un archivo.

Lo que nunca registra: headers de credenciales (Authorization, Cookie,
Set-Cookie…) ni el valor de campos que parezcan credenciales (password,
contraseña, clave, secret, token…), que se sustituyen por "[REDACTADO]".
La evidencia cita ubicaciones, no secretos.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
import uuid
from datetime import datetime
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

# Headers hop-by-hop (RFC 7230 §6.1): son del tramo, no del mensaje; no se reenvían.
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}
# Headers que llevan credenciales: se reenvían al app, jamás al registro.
_SECRET_HEADERS = {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key"}
_SECRET_FIELD_RE = re.compile(r"(?i)(pass|pwd|contrase|clave|secret|token|credencial|authorization)")
_REDACTED = "[REDACTADO]"

_MAX_CAPTURE_BYTES = 65536   # cuerpos más grandes no se interpretan: solo se anota el tamaño
_MAX_TEXT_CHARS = 2048       # tope para cuerpos JSON que no parsean

CORRELATION_HEADER = "X-Pepper-Correlation-Id"


class _BadRequest(ValueError):
    """La petición no se puede leer (chunk o Content-Length malformados): 400 y no se reenvía."""


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _REDACTED if _SECRET_FIELD_RE.search(str(key)) else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _capture_body(content_type: str, data: bytes) -> Tuple[Optional[Any], Optional[int]]:
    """(body interpretado y redactado, bytes no interpretados). Solo JSON y formularios."""
    if not data:
        return None, None
    if len(data) > _MAX_CAPTURE_BYTES:
        return None, len(data)
    kind = content_type.split(";", 1)[0].strip().lower()
    if kind == "application/x-www-form-urlencoded":
        pairs = parse_qs(data.decode("utf-8", errors="replace"), keep_blank_values=True)
        flat = {key: values[0] if len(values) == 1 else values for key, values in pairs.items()}
        return _redact(flat), None
    if kind.endswith("json"):
        try:
            return _redact(json.loads(data.decode("utf-8", errors="replace"))), None
        except ValueError:
            # JSON malformado: la redacción por claves no aplica y el texto crudo
            # puede llevar el secreto entero (auditoría H-05). No se registra.
            return None, len(data)
    return None, len(data)


class Recorder:
    """Escribe una línea JSON por registro, con candado: el proxy es multihilo."""

    def __init__(self, out_path: Optional[str] = None, stdout: bool = True):
        self._lock = threading.Lock()
        self._stdout = stdout
        self._file = open(out_path, "a", encoding="utf-8") if out_path else None

    def record(self, entry: Dict[str, Any]) -> None:
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            if self._stdout:
                sys.stdout.write(line + "\n")
                sys.stdout.flush()
            if self._file:
                self._file.write(line + "\n")
                self._file.flush()

    def close(self) -> None:
        if self._file:
            self._file.close()


class PepperProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "pepper-proxy"

    # El stdout es el http.jsonl: el log de accesos por defecto se silencia.
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass

    def _read_request_body(self) -> bytes:
        try:
            if (self.headers.get("Transfer-Encoding") or "").lower() == "chunked":
                data = bytearray()
                while True:
                    size_line = self.rfile.readline()
                    size = int(size_line.split(b";")[0].strip() or b"0", 16)
                    if size == 0:
                        self.rfile.readline()
                        return bytes(data)
                    data += self.rfile.read(size)
                    self.rfile.readline()
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as error:
            raise _BadRequest(f"cuerpo ilegible: {error}") from None
        if length < 0:
            raise _BadRequest("Content-Length negativo")
        return self.rfile.read(length) if length else b""

    def _forward(self, correlation_id: str, body: bytes) -> Tuple[int, str, List[Tuple[str, str]], bytes]:
        host, port = self.server.upstream  # type: ignore[attr-defined]
        connection = HTTPConnection(host, port, timeout=self.server.upstream_timeout)  # type: ignore[attr-defined]
        try:
            connection.putrequest(self.command, self.path, skip_host=True, skip_accept_encoding=True)
            has_host = False
            for name, value in self.headers.items():
                lowered = name.lower()
                if lowered in _HOP_BY_HOP or lowered == CORRELATION_HEADER.lower():
                    continue
                if lowered == "content-length":
                    continue  # se recalcula: el cuerpo ya está completo en memoria
                if lowered == "host":
                    has_host = True
                connection.putheader(name, value)
            if not has_host:
                connection.putheader("Host", f"{host}:{port}")
            if body or self.command in ("POST", "PUT", "PATCH"):
                connection.putheader("Content-Length", str(len(body)))
            connection.putheader(CORRELATION_HEADER, correlation_id)
            connection.endheaders(body if body else None)
            response = connection.getresponse()
            payload = response.read()
            return response.status, response.reason, response.getheaders(), payload
        finally:
            connection.close()

    def _record_request(self, correlation_id: str, body: bytes) -> None:
        # El query string viaja aparte y redactado: /reset?token=… llevaba el
        # secreto completo dentro de "path" (auditoría H-05).
        path_only, _, _ = self.path.partition("?")
        query_raw = urlsplit(self.path).query
        entry: Dict[str, Any] = {
            "ts": _now_iso(),
            "direction": "request",
            "method": self.command,
            "path": path_only,
            "correlation_id": correlation_id,
            "client": self.client_address[0],
        }
        if query_raw:
            pairs = parse_qs(query_raw, keep_blank_values=True)
            entry["query"] = _redact({k: v[0] if len(v) == 1 else v for k, v in pairs.items()})
        content_type = self.headers.get("Content-Type")
        if content_type:
            entry["content_type"] = content_type
        captured, raw_bytes = _capture_body(content_type or "", body)
        if captured is not None:
            entry["body"] = captured
        elif raw_bytes:
            entry["body_bytes"] = raw_bytes
        self.server.recorder.record(entry)  # type: ignore[attr-defined]

    def _record_response(self, correlation_id: str, status: int, duration_ms: int,
                         content_type: str, payload: bytes, note: Optional[str] = None) -> None:
        entry: Dict[str, Any] = {
            "ts": _now_iso(),
            "direction": "response",
            "method": self.command,
            "path": self.path.partition("?")[0],
            "status": status,
            "duration_ms": duration_ms,
            "correlation_id": correlation_id,
        }
        kind = content_type.split(";", 1)[0].strip().lower()
        if kind.endswith("json"):
            captured, _ = _capture_body(content_type, payload)
            if captured is not None:
                entry["body"] = captured
        if note:
            entry["note"] = note
        self.server.recorder.record(entry)  # type: ignore[attr-defined]

    def _fail(self, correlation_id: str, status: int, duration_ms: int, error: str,
              detail: str, note: str) -> None:
        """Respuesta generada por el proxy (400/502), registrada ANTES de escribirla al
        cliente: si se registrara después, el cliente puede terminar y Observe cerrar
        la ventana antes de que exista la línea de respuesta."""
        message = json.dumps({"error": error, "detail": detail}).encode()
        self._record_response(correlation_id, status, duration_ms, "application/json", message, note=note)
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(message)))
            self.end_headers()
            self.wfile.write(message)
        except OSError:
            pass

    def _handle(self) -> None:
        correlation_id = f"req-{uuid.uuid4().hex[:12]}"
        try:
            body = self._read_request_body()
        except _BadRequest as error:
            # Sin cuerpo confiable no hay nada que reenviar; la conexión quedó fuera de
            # sincronía con el cliente, así que se cierra en vez de reutilizarse.
            self._record_request(correlation_id, b"")
            self._fail(correlation_id, 400, 0, "pepper-proxy: petición ilegible", str(error),
                       note=f"petición ilegible: {error}")
            self.close_connection = True
            return
        self._record_request(correlation_id, body)
        started = time.monotonic()
        try:
            status, reason, headers, payload = self._forward(correlation_id, body)
        except (OSError, HTTPException) as error:
            duration_ms = int((time.monotonic() - started) * 1000)
            self._fail(correlation_id, 502, duration_ms, "pepper-proxy: el app no respondió", str(error),
                       note=f"upstream inalcanzable: {error}")
            return
        duration_ms = int((time.monotonic() - started) * 1000)

        # La respuesta observada se fija antes de exponer sus bytes al cliente;
        # elimina la carrera request-only en capturas y pruebas concurrentes.
        content_type = next((value for name, value in headers if name.lower() == "content-type"), "")
        self._record_response(correlation_id, status, duration_ms, content_type, payload)

        self.send_response(status, reason)
        for name, value in headers:
            lowered = name.lower()
            if lowered in _HOP_BY_HOP or lowered == "content-length":
                continue
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if self.command != "HEAD" and payload:
            try:
                self.wfile.write(payload)
            except OSError:
                pass

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _handle


class ProxyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, listen: Tuple[str, int], upstream: Tuple[str, int],
                 recorder: Recorder, upstream_timeout: float = 120.0):
        super().__init__(listen, PepperProxyHandler)
        self.upstream = upstream
        self.recorder = recorder
        self.upstream_timeout = upstream_timeout


def _host_port(value: str) -> Tuple[str, int]:
    host, _, port = value.rpartition(":")
    if not host or not port.isdigit():
        raise argparse.ArgumentTypeError(f"se esperaba host:puerto, no {value!r}")
    return host, int(port)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pepper proxy",
        description="proxy HTTP de PEPPER: reenvía al app, inyecta correlation_id y emite http.jsonl",
    )
    parser.add_argument("--listen", type=_host_port, default=("0.0.0.0", 8080),
                        help="host:puerto donde escuchar (default 0.0.0.0:8080)")
    parser.add_argument("--upstream", type=_host_port, required=True,
                        help="host:puerto del app rehidratado")
    parser.add_argument("--out", default=None,
                        help="además de stdout, escribir el http.jsonl a este archivo")
    parser.add_argument("--timeout", type=float, default=120.0,
                        help="segundos de espera por respuesta del app (default 120)")
    return parser


def run(args: argparse.Namespace) -> int:
    recorder = Recorder(out_path=args.out)
    server = ProxyServer(args.listen, args.upstream, recorder, upstream_timeout=args.timeout)
    listen_host, listen_port = args.listen
    upstream_host, upstream_port = args.upstream
    sys.stderr.write(
        f"pepper-proxy: {listen_host}:{listen_port} -> {upstream_host}:{upstream_port} "
        f"(correlation en {CORRELATION_HEADER}; http.jsonl por stdout)\n"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        recorder.close()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
