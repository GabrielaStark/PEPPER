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

El navegador del humano es parte del perímetro. Los contenedores están en una
red interna, pero el HTML del legacy puede apuntar a sus servidores reales
(un <object>, un <iframe>, un <img>, un fetch, un window.open) y el navegador
los pediría directo — con VPN, a producción — sin que ningún contenedor lo vea.
Por eso cada respuesta sale con una Content-Security-Policy que solo permite
cargar desde el propio ingress: el navegador bloquea lo demás ANTES de resolver
un nombre, y le reporta al ingress qué bloqueó (report-uri). Para lo que CSP no
cubre (window.open, clic en un enlace externo, envío de un formulario) se
inyecta un guardián de pocas líneas en cada página HTML que lo intercepta y lo
reporta. Ambos reportes quedan en http.jsonl como `direction: "blocked"`:
evidencia de la dependencia externa, que antes se perdía.
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

# Política que el ingress impone al navegador: nada sale a otro origen. Los
# 'unsafe-*' son inevitables en un legacy (JSF/PrimeFaces usan inline y eval);
# lo que importa es que TODO destino sea 'self'. report-uri manda al ingress
# cada bloqueo: es evidencia de dependencia externa.
BROWSER_POLICY = (
    "default-src 'self' data: blob: 'unsafe-inline' 'unsafe-eval'; "
    "frame-src 'self'; object-src 'self'; child-src 'self' blob:; worker-src 'self' blob:; "
    "connect-src 'self'; form-action 'self'; base-uri 'self'; "
    "report-uri /__pepper/csp-report"
)
# Headers del app que podrían relajar o sustituir la política, o precargar de fuera.
_STRIPPED_RESPONSE_HEADERS = {"content-security-policy", "content-security-policy-report-only", "link", "refresh"}
# Endpoints propios del ingress: el navegador reporta aquí lo que bloqueó.
_REPORT_PATHS = {"/__pepper/csp-report": "csp", "/__pepper/nav-report": "navigation"}
# Página propia a la que se reescribe toda navegación hacia otro origen que el app
# intente provocar (Location de un 3xx, <meta http-equiv=refresh>): CSP gobierna lo que
# la página CARGA, no a dónde NAVEGA el documento entero (auditoría 2026-09-11).
_BLOCKED_PATH = "/__pepper/blocked"
_MAX_REQUEST_BYTES = 32 * 1024 * 1024   # más que eso no es una pantalla de un legacy: 413, sin agotar memoria
_META_REFRESH_RE = re.compile(rb"<meta\s+[^>]*http-equiv\s*=\s*[\"\']?refresh[\"\']?[^>]*>", re.IGNORECASE)
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
# Guardián para lo que CSP no cubre: window.open, clic en <a href> externo y submit
# a otro origen. Intercepta, no navega, y reporta. Solo ASCII: se inyecta en bytes.
_GUARD_SCRIPT = (
    b"<script data-pepper=\"guard\">(function(){var O=location.origin;"
    b"function X(u){try{return new URL(u,location.href).origin!==O}catch(e){return false}}"
    b"function R(k,u){try{var b=JSON.stringify({kind:k,blocked_uri:String(u),document_uri:location.href});"
    b"if(navigator.sendBeacon){navigator.sendBeacon('/__pepper/nav-report',b)}else{fetch('/__pepper/nav-report',{method:'POST',body:b,keepalive:true})}}catch(e){}}"
    b"var W=window.open;window.open=function(u){if(u&&X(u)){R('window.open',u);return null}return W.apply(this,arguments)};"
    b"document.addEventListener('click',function(e){var t=e.target;var a=(t&&t.closest)?t.closest('a[href]'):null;"
    b"if(a&&X(a.href)){e.preventDefault();e.stopImmediatePropagation();R('link',a.href)}},true);"
    b"document.addEventListener('submit',function(e){var f=e.target;if(f&&f.action&&X(f.action)){e.preventDefault();e.stopImmediatePropagation();R('form',f.action)}},true);"
    b"})();</script>"
)

_MAX_CAPTURE_BYTES = 65536   # cuerpos más grandes no se interpretan: solo se anota el tamaño
_MAX_TEXT_CHARS = 2048       # tope para cuerpos JSON que no parsean

CORRELATION_HEADER = "X-Pepper-Correlation-Id"


class _BadRequest(ValueError):
    """La petición no se puede leer (chunk o Content-Length malformados): 400 y no se reenvía."""


class _TooLarge(ValueError):
    """Cuerpo mayor que _MAX_REQUEST_BYTES: 413 antes de leerlo entero."""


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


def guard_html(content_type: str, content_encoding: str, payload: bytes) -> Tuple[bytes, Optional[str]]:
    """Inyecta el guardián al inicio de <head> (o al inicio del documento) en respuestas HTML.

    Devuelve (cuerpo, nota). Si el cuerpo viene comprimido no se toca —la política
    CSP del header aplica igual— y se anota para que quede en la evidencia."""
    kind = content_type.split(";", 1)[0].strip().lower()
    if kind != "text/html" or not payload:
        return payload, None
    if content_encoding and content_encoding.strip().lower() not in ("", "identity"):
        return payload, f"guardián del navegador no inyectado: respuesta comprimida ({content_encoding})"
    lower = payload.lower()
    at = 0
    for tag in (b"<head", b"<html"):
        i = lower.find(tag)
        if i >= 0:
            j = lower.find(b">", i)
            if j >= 0:
                at = j + 1
                break
    return payload[:at] + _GUARD_SCRIPT + payload[at:], None


def strip_meta_refresh(payload: bytes) -> Tuple[bytes, List[str]]:
    """Quita todo <meta http-equiv=refresh> cuyo destino sea absoluto (otro origen) y
    devuelve los destinos: una navegación top-level que la CSP no frena."""
    blocked: List[str] = []
    def replace(match: "re.Match[bytes]") -> bytes:
        tag = match.group(0)
        url = re.search(rb"url\s*=\s*[\"\']?([^\"\'>;\s]+)", tag, re.IGNORECASE)
        target = url.group(1).decode("utf-8", "replace") if url else ""
        if target.lower().startswith(("http://", "https://", "//")):
            blocked.append(target)
            return b"<!-- pepper: meta refresh hacia otro origen bloqueado -->"
        return tag
    return _META_REFRESH_RE.sub(replace, payload), blocked


def decode_body(content_encoding: str, payload: bytes) -> Tuple[bytes, bool]:
    """Descomprime gzip/deflate para poder inyectar el guardián; (cuerpo, ¿se pudo?)."""
    encoding = (content_encoding or "").strip().lower()
    try:
        if encoding == "gzip":
            import gzip
            return gzip.decompress(payload), True
        if encoding == "deflate":
            import zlib
            try:
                return zlib.decompress(payload), True
            except zlib.error:
                return zlib.decompress(payload, -zlib.MAX_WBITS), True
    except (OSError, EOFError, ValueError):
        return payload, False
    return payload, encoding in ("", "identity")


def _strip_userinfo(netloc: str) -> str:
    """`usuario:clave@host:puerto` → `host:puerto`. El userinfo de una URL es una credencial."""
    return netloc.rsplit("@", 1)[-1]


def sanitize_url(uri: str) -> Tuple[str, str, str, Optional[Dict[str, Any]]]:
    """(uri limpia, host, path, query redactado o None).

    De una URL solo se guarda esquema, host, puerto y ruta. Se quita el userinfo
    (`usuario:clave@`), el fragmento y el query; el query vuelve aparte, redactado por
    nombre de campo, para que un `?token=…` o un `?password=…` jamás quede en claro en
    http.jsonl. Aplica igual al destino bloqueado que a la página que lo intentó: los
    dos viajan en el paquete (revisión 2026-09-21)."""
    if "://" in uri or uri.startswith("//"):
        parts = urlsplit(uri)
        if parts.netloc:
            host = _strip_userinfo(parts.netloc)
            path = parts.path or "/"
            query = None
            if parts.query:
                pairs = parse_qs(parts.query, keep_blank_values=True)
                query = _redact({k: v[0] if len(v) == 1 else v for k, v in pairs.items()})
            return f"{parts.scheme}://{host}{path}" if parts.scheme else f"//{host}{path}", host, path, query
    # el navegador a veces reporta solo un origen sin ruta, un esquema (`data`, `blob`) o `inline`:
    # aun así no se conserva nada después de `?` o `#`, ni un `usuario:clave@` delante
    bare = re.split(r"[?#]", uri, 1)[0]
    if "@" in bare:
        head, _, tail = bare.rpartition("@")
        bare = (head.split("//", 1)[0] + "//" if "//" in head else "") + tail
    return bare, bare, "", None


def blocked_record(kind: str, report: Dict[str, Any]) -> Dict[str, Any]:
    """Normaliza un reporte del navegador (CSP o guardián) a una línea de http.jsonl.

    Ninguna URL se guarda entera: ni la bloqueada ni la del documento. El query del
    destino bloqueado se guarda aparte y redactado: puede llevar identificadores reales;
    nunca credenciales en claro."""
    if kind == "csp":
        body = report.get("csp-report") if isinstance(report.get("csp-report"), dict) else report
        uri = str(body.get("blocked-uri") or body.get("blockedURL") or "")
        document = str(body.get("document-uri") or body.get("documentURL") or "")
        entry: Dict[str, Any] = {
            "ts": _now_iso(), "direction": "blocked", "kind": "csp",
            "directive": str(body.get("effective-directive") or body.get("violated-directive") or ""),
        }
    else:
        uri = str(report.get("blocked_uri") or "")
        document = str(report.get("document_uri") or "")
        entry = {"ts": _now_iso(), "direction": "blocked", "kind": str(report.get("kind") or "navigation")}
    entry["document_uri"] = sanitize_url(document)[0] if document else ""
    clean, host, path, query = sanitize_url(uri)
    entry["blocked_host"] = host
    if path:
        entry["blocked_path"] = path
    entry["blocked_uri"] = clean
    if query:
        entry["blocked_query"] = query
    return entry


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


_READ_BLOCK = 64 * 1024
_MAX_LINE = 65536


class PepperProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "pepper-proxy"

    # El stdout es el http.jsonl: el log de accesos por defecto se silencia.
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass

    # Tiempo máximo esperando bytes del cliente: un cuerpo que no llega no bloquea el proxy.
    timeout = 60

    def _read_exact(self, size: int) -> bytes:
        """Exactamente `size` bytes, en bloques acotados; un cuerpo corto es una petición mala, no vacía."""
        data = bytearray()
        while len(data) < size:
            block = self.rfile.read(min(_READ_BLOCK, size - len(data)))
            if not block:
                raise _BadRequest(f"cuerpo truncado: llegaron {len(data)} de {size} bytes")
            data += block
        return bytes(data)

    def _read_request_body(self) -> bytes:
        """El límite se aplica ANTES de leer: un chunk declarado de 64 MiB pedía 64 MiB de una vez y
        un cuerpo truncado pasaba como vacío (auditoría 2026-09-21, P2-01)."""
        try:
            if (self.headers.get("Transfer-Encoding") or "").lower() == "chunked":
                data = bytearray()
                while True:
                    size_line = self.rfile.readline(_MAX_LINE)
                    if not size_line.endswith(b"\n"):
                        raise _BadRequest("tamaño de chunk ilegible")
                    size = int(size_line.split(b";")[0].strip() or b"0", 16)
                    if size < 0:
                        raise _BadRequest("chunk negativo")
                    if size == 0:
                        while True:   # trailers hasta la línea vacía
                            trailer = self.rfile.readline(_MAX_LINE)
                            if trailer in (b"\r\n", b"\n", b""):
                                break
                            if not trailer.endswith(b"\n"):
                                raise _BadRequest("trailer ilegible")
                        return bytes(data)
                    if len(data) + size > _MAX_REQUEST_BYTES:
                        raise _TooLarge(len(data) + size)
                    data += self._read_exact(size)
                    if self.rfile.readline(_MAX_LINE) not in (b"\r\n", b"\n"):
                        raise _BadRequest("terminador de chunk inválido")
            length = int(self.headers.get("Content-Length") or 0)
        except (_BadRequest, _TooLarge):
            raise
        except ValueError as error:
            raise _BadRequest(f"cuerpo ilegible: {error}") from None
        if length < 0:
            raise _BadRequest("Content-Length negativo")
        if length > _MAX_REQUEST_BYTES:
            raise _TooLarge(length)
        return self._read_exact(length) if length else b""

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
                if lowered == "accept-encoding":
                    continue  # el cuerpo debe llegar plano: el guardián se inyecta en el HTML
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
            self.send_header("Content-Security-Policy", BROWSER_POLICY)
            self.send_header("Content-Length", str(len(message)))
            self.end_headers()
            self.wfile.write(message)
        except OSError:
            pass

    def _rewrite_location(self, status: int, headers: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
        """Un 3xx del app hacia otro origen mandaría el navegador del humano a un servidor
        real (con VPN, a producción) sin que ningún contenedor lo vea. Se reescribe a la
        página de bloqueo del ingress y se registra. Un Location hacia el propio app
        (su IP interna, que el navegador no alcanza) se vuelve relativo al ingress."""
        if status not in _REDIRECT_STATUSES:
            return headers
        upstream_host, upstream_port = self.server.upstream  # type: ignore[attr-defined]
        own = {(self.headers.get("Host") or "").lower(), f"{upstream_host}:{upstream_port}".lower(),
               upstream_host.lower()}
        out: List[Tuple[str, str]] = []
        for name, value in headers:
            if name.lower() != "location":
                out.append((name, value)); continue
            parts = urlsplit(value)
            if not parts.netloc:
                out.append((name, value)); continue           # relativo: se queda en el ingress
            if parts.netloc.lower() in own:
                rel = parts.path or "/"
                out.append((name, rel + (f"?{parts.query}" if parts.query else "")))
                continue
            self.server.recorder.record(blocked_record("navigation", {  # type: ignore[attr-defined]
                "kind": "redirect", "blocked_uri": value, "document_uri": self.path.partition("?")[0]}))
            out.append((name, f"{_BLOCKED_PATH}?to={parts.netloc}"))
        return out

    def _serve_blocked_page(self) -> None:
        to = parse_qs(urlsplit(self.path).query).get("to", ["otro origen"])[0]
        safe = "".join(ch for ch in to if ch.isalnum() or ch in ".-:_")[:120]
        page = (f"<!doctype html><meta charset=utf-8><title>PEPPER</title><body style=font-family:sans-serif>"
                f"<h2>Navegaci&oacute;n bloqueada</h2><p>El sistema intent&oacute; llevar tu navegador a "
                f"<b>{safe}</b>. En un entorno rehidratado nada sale hacia servidores reales; el intento "
                f"qued&oacute; registrado como dependencia externa.</p><p><a href=\"javascript:history.back()\">Volver</a></p>").encode()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Security-Policy", BROWSER_POLICY)
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)
        except OSError:
            pass

    def _handle_report(self, kind: str, body: bytes) -> None:
        """El navegador dice qué bloqueó. Se registra (sin correlation_id: no es una
        petición al app y no debe anclar una traza) y se responde 204."""
        if self.command != "POST":
            self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers(); return
        try:
            report = json.loads(body.decode("utf-8", errors="replace")) if body else {}
        except ValueError:
            report = {}
        if not isinstance(report, dict):
            report = {}
        self.server.recorder.record(blocked_record(kind, report))  # type: ignore[attr-defined]
        try:
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
        except OSError:
            pass

    def _handle(self) -> None:
        correlation_id = f"req-{uuid.uuid4().hex[:12]}"
        try:
            body = self._read_request_body()
        except _TooLarge as error:
            self._record_request(correlation_id, b"")
            self._fail(correlation_id, 413, 0, "pepper-proxy: cuerpo demasiado grande", str(error),
                       note=f"cuerpo de {error} bytes rechazado (tope {_MAX_REQUEST_BYTES})")
            self.close_connection = True
            return
        except _BadRequest as error:
            # Sin cuerpo confiable no hay nada que reenviar; la conexión quedó fuera de
            # sincronía con el cliente, así que se cierra en vez de reutilizarse.
            self._record_request(correlation_id, b"")
            self._fail(correlation_id, 400, 0, "pepper-proxy: petición ilegible", str(error),
                       note=f"petición ilegible: {error}")
            self.close_connection = True
            return
        report_kind = _REPORT_PATHS.get(self.path.partition("?")[0])
        if report_kind:
            self._handle_report(report_kind, body)
            return
        if self.path.partition("?")[0] == _BLOCKED_PATH:
            self._serve_blocked_page()
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
        encoding = next((value for name, value in headers if name.lower() == "content-encoding"), "")
        is_html = content_type.split(";", 1)[0].strip().lower() == "text/html"
        drop_encoding = False
        if is_html and encoding:
            payload, decoded = decode_body(encoding, payload)
            if decoded:
                drop_encoding = True   # se sirve plano: el guardián va adentro
                encoding = ""
        payload, guard_note = guard_html(content_type, encoding, payload)
        if is_html and not encoding:
            payload, refreshes = strip_meta_refresh(payload)
            for target in refreshes:
                self.server.recorder.record(blocked_record("navigation", {  # type: ignore[attr-defined]
                    "kind": "meta-refresh", "blocked_uri": target, "document_uri": self.path.partition("?")[0]}))
        headers = self._rewrite_location(status, headers)
        self._record_response(correlation_id, status, duration_ms, content_type, payload, note=guard_note)

        self.send_response(status, reason)
        for name, value in headers:
            lowered = name.lower()
            if lowered in _HOP_BY_HOP or lowered == "content-length" or lowered in _STRIPPED_RESPONSE_HEADERS:
                continue
            if drop_encoding and lowered == "content-encoding":
                continue
            self.send_header(name, value)
        self.send_header("Content-Security-Policy", BROWSER_POLICY)
        self.send_header("Referrer-Policy", "no-referrer")
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
