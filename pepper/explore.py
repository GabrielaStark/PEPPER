"""`pepper explore`: el sistema se recorre solo, por el ingress, con un navegador local.

Qué hace, igual cada vez (Principio 3):

  1. Fija una credencial por rol en la base DESECHABLE (la del contenedor; jamás
     un artefacto ni un ambiente real) con el SQL que declara `explore.json`.
  2. Por cada rol: entra, y por cada ruta del mapa abre la pantalla, la
     fotografía (título, encabezados, botones, campos), intenta guardar con todo
     vacío para provocar los rechazos, llena los campos con valores plausibles y
     vuelve a intentar, y anota qué mensajes salieron y a dónde fue a parar.
     Después pide con ese rol las rutas que su menú NO muestra: la matriz real
     rol × pantalla.
  3. Ejecuta, si se le da, un PLAN escrito por el agente: pasos encadenados
     (entrar como X, ir a Y, llenar, elegir, apretar, esperar, comprobar) para
     recorrer un flujo de punta a punta cruzando roles y estados.
  4. Deja `evidence/<sesión>/explore.jsonl` (una línea por acción, con ventana,
     resultado, mensajes y captura de pantalla) y `session.json`; `pepper collect`
     copia lo que el ingress y los contenedores vieron en esa ventana.

Todo pasa por el ingress (127.0.0.1:<puerto>), nunca directo al app ni a la base.
El navegador es headless y local; el ingress le impone la misma política que al
navegador de una persona (D25): lo que el legacy intente traer de un servidor
real se bloquea y se registra. Solo corre con `pepper isolate --live` en verde.

Lo que el explorador NO sabe: qué hace la oficina de verdad. Descubre lo que el
sistema permite y rechaza; el sentido de negocio lo pone el discovery con los
datos y las preguntas para una persona.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

DEFAULT_TIMEOUT_MS = 15000
SETTLE_MS = 1200          # PrimeFaces manda ajax tras cada cambio; se espera a que calme
MAX_BUTTONS_PER_SCREEN = 8
# Botones que no guardan nada: no vale la pena "probarlos" y algunos cierran sesión.
_SKIP_BUTTON_RE = re.compile(r"(?i)^(cancelar|regresar|salir|cerrar|volver|limpiar|no|descargar|imprimir|exportar|generar pdf)\b")
_SUBMIT_HINT_RE = re.compile(r"(?i)guardar|registr|agregar|buscar|filtrar|generar|enviar|aceptar|continuar|agendar|confirmar|tomar|iniciar|finalizar|aplicar|cargar")
# Cómo se ve un rechazo en pantalla (mensajes de validación del framework o del negocio).
_REJECT_TEXT_RE = re.compile(r"(?i)obligatori|requerid|inv[aá]lid|debe |no se puede|no existe|err[oó]neo|incorrect|favor de|seleccion[ae]|ya existe|no se encontr|error")


@dataclass
class Action:
    role: str
    route: str
    kind: str                      # screen | empty_submit | filled_submit | access | plan
    label: str = ""
    started: str = ""
    ended: str = ""
    url_after: str = ""
    status: Optional[int] = None
    result: str = ""               # ok | rejected | redirected | error | forbidden
    messages: List[str] = field(default_factory=list)
    screenshot: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)

    def record(self) -> Dict[str, Any]:
        out = {"ts": self.started, "ended": self.ended, "role": self.role, "route": self.route, "kind": self.kind,
               "label": self.label, "result": self.result, "url_after": self.url_after}
        if self.status is not None:
            out["status"] = self.status
        if self.messages:
            out["messages"] = self.messages
        if self.screenshot:
            out["screenshot"] = self.screenshot
        if self.detail:
            out["detail"] = self.detail
        return out


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def plausible_value(field_id: str, label: str, input_type: str, hints: Dict[str, str]) -> Optional[str]:
    """Un valor plausible por el nombre del campo: primero las pistas del sistema, luego heurísticas."""
    key = f"{field_id} {label}".lower()
    for pattern, value in hints.items():
        if re.search(pattern, key, re.I):
            return value
    if input_type in ("checkbox", "radio", "file", "hidden", "submit", "button"):
        return None
    if re.search(r"curp", key):
        return "PEPR900101HMCPPR09"
    if re.search(r"rfc", key):
        return "PEPR900101AB1"
    if re.search(r"correo|email|mail", key):
        return "prueba@pepper.invalid"
    if re.search(r"tel|cel|phone", key):
        return "5555555555"
    if re.search(r"edad|numero|num|nu[a-z]*ext|cantidad|monto|folio|turno|expediente", key):
        return "10"
    if re.search(r"fecha|date|fch|fc[a-z]", key) or input_type == "date":
        return datetime.now().strftime("%d/%m/%Y")
    if re.search(r"hora|hour", key):
        return "10:00"
    if re.search(r"cp|codigo ?postal|postal", key):
        return "50000"
    if re.search(r"pass|contrase|clave", key):
        return None
    if re.search(r"nombre|name", key):
        return "Prueba"
    if re.search(r"apellido|paterno|materno", key):
        return "Pepper"
    if re.search(r"razon|social|empresa|patron|actividad|puesto|calle|colonia|observ|coment|motivo|hecho|descrip|texto", key):
        return "Prueba PEPPER"
    if input_type in ("text", "textarea", "search", ""):
        return "Prueba"
    if input_type == "number":
        return "1"
    return None


def identity_text(login: Dict[str, Any], role: Dict[str, Any]) -> str:
    """Lo que debe verse en pantalla tras entrar con `role`: `role.identity_text` o
    `login.identity_text`, con `{user}` y `{role}` sustituidos."""
    template = role.get("identity_text") or login.get("identity_text") or ""
    return str(template).replace("{user}", str(role.get("user", ""))).replace("{role}", str(role.get("name", "")))


class CredentialsError(RuntimeError):
    """No se pudo fijar ninguna credencial de prueba: sin eso no hay nada que explorar."""


class Explorer:
    def __init__(self, config: Dict[str, Any], system_map: Dict[str, Any], out_dir: Path,
                 headless: bool = True, timeout_ms: int = DEFAULT_TIMEOUT_MS):
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError as error:
            raise RuntimeError("pepper explore necesita Playwright: pip install playwright && python3 -m playwright install chromium") from error
        self.config = config
        self.map = system_map
        self.out_dir = out_dir
        self.shots = out_dir / "screens"
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.base = config["base_url"].rstrip("/")
        self.log_path = out_dir / "explore.jsonl"
        self.actions: List[Action] = []
        self.hints: Dict[str, str] = config.get("fill") or {}
        self._page = None
        self._context = None
        self._browser = None
        self._pw = None

    # ------------------------------------------------------------ infraestructura

    def __enter__(self) -> "Explorer":
        from playwright.sync_api import sync_playwright

        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.shots.mkdir(exist_ok=True)
        self._log = self.log_path.open("a", encoding="utf-8")
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
        self._context = None
        self._fresh_context()
        self.ready: Optional[List[str]] = None
        self.deadline: Optional[float] = None
        return self

    def _fresh_context(self) -> None:
        """Cierra el contexto del navegador y abre uno nuevo, vacío.

        Un contexto nuevo no hereda NADA del anterior: cookies, localStorage, sessionStorage,
        IndexedDB, caché, service workers. Antes se reutilizaba uno solo y al salir se borraban
        las cookies: un sistema que guarda la sesión fuera de ellas habría atribuido al rol
        siguiente lo que puede hacer el anterior (revisión 2026-09-24)."""
        if self._context is not None:
            try:
                self._context.close()
            except Exception:
                pass
        context = self._browser.new_context(viewport={"width": 1366, "height": 900})
        self._context = context
        context.set_default_timeout(self.timeout_ms)
        # El navegador del explorador corre en el host (con VPN). La CSP del ingress frena lo
        # que la página carga, pero no una navegación top-level por script (location.href=).
        # Aquí sí se puede cerrar del todo: toda petición que no vaya al ingress se aborta y
        # queda registrada; toda ventana nueva se cierra (auditoría 2026-09-11).
        base = self.base
        # El ingress es el ORIGEN (esquema, host y puerto), no la ruta base: con un contexto
        # (`…:18080/openboxes`) los reportes CSP del navegador a `/__pepper/csp-report` se abortaban
        # aquí y la evidencia de lo que la página intentó cargar de fuera se perdía (prueba real 2026-09-22).
        parts = urlsplit(base)
        origin = f"{parts.scheme}://{parts.netloc}"
        def only_ingress(route):
            url = route.request.url
            if url.startswith(origin + "/") or url == origin or url == base or url.startswith(("data:", "blob:", "about:")):
                route.continue_()
            else:
                self._write(Action(role="*", route="", kind="blocked", label=url[:160], started=_now(), result="rejected",
                                   detail={"blocked_uri": url[:300], "why": "fuera del ingress"}))
                route.abort("blockedbyclient")
        context.route("**/*", only_ingress)
        # La página principal se crea ANTES de registrar el cierre de popups: el evento "page" de la
        # propia página llegaba con `_page` aún sin asignar y la cerraba ("Target page, context or
        # browser has been closed" en el primer goto; prueba real 2026-09-22, máquina cargada).
        self._page = context.new_page()
        context.on("page", lambda popup: popup.close() if popup != self._page else None)
        self._page.on("dialog", lambda d: d.accept())

    def __exit__(self, *exc) -> None:
        try:
            self._log.close()
        finally:
            if self._context is not None:
                try:
                    self._context.close()
                except Exception:
                    pass
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()

    @property
    def page(self):
        return self._page

    def _write(self, action: Action) -> None:
        action.ended = action.ended or _now()
        self.actions.append(action)
        self._log.write(json.dumps(action.record(), ensure_ascii=False) + "\n")
        self._log.flush()

    def _shot(self, name: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_")[:120]
        path = self.shots / f"{len(self.actions) + 1:03d}_{safe}.png"
        try:
            self.page.screenshot(path=str(path), full_page=False)
        except Exception:
            return ""
        return path.relative_to(self.out_dir).as_posix()

    def _settle(self, ms: int = SETTLE_MS) -> None:
        # networkidle acotado: una pantalla con un poll o un spinner nunca queda "idle" y cada
        # espera costaba 15 s (varias por pantalla). El re-dibujo de PrimeFaces cabe en 3 s.
        try:
            self.page.wait_for_load_state("networkidle", timeout=min(self.timeout_ms, 3000))
        except Exception:
            pass
        self.page.wait_for_timeout(ms)

    def _fill_input(self, selector: str, value: str) -> None:
        """Escribe y avisa al framework (change + blur): PrimeFaces manda el valor por ajax al perder el
        foco; sin eso, el siguiente re-dibujo del formulario lo borra."""
        locator = self.page.locator(selector).first
        locator.fill(str(value), timeout=self.timeout_ms)
        try:
            locator.dispatch_event("change")
            locator.dispatch_event("blur")
        except Exception:
            pass

    def _select_by_text(self, menu_id: str, text: str) -> str:
        """Elige en un selectOneMenu de PrimeFaces el ítem cuyo texto contiene `text` (dos intentos:
        el panel puede cerrarse por un re-dibujo). Devuelve el texto elegido."""
        last: Optional[Exception] = None
        for _ in range(2):
            try:
                self.page.keyboard.press("Escape")
                menu = self.page.locator(f"[id='{menu_id}']").first
                menu.scroll_into_view_if_needed(timeout=3000)
                menu.locator(".ui-selectonemenu-trigger").click(timeout=5000)
                panel = self.page.locator(f"[id='{menu_id}_panel'] li.ui-selectonemenu-item")
                panel.first.wait_for(state="visible", timeout=5000)
                item = panel.filter(has_text=str(text)).first
                if item.count() == 0:
                    raise RuntimeError(f"sin opción {text!r} en {menu_id}")
                chosen = " ".join(item.inner_text().split())
                item.click(timeout=5000)
                self._settle(700)
                return chosen
            except Exception as error:  # noqa: BLE001
                last = error
                self.page.wait_for_timeout(500)
        raise RuntimeError(f"select {menu_id}={text!r}: {last}")

    def _messages(self) -> List[str]:
        """Mensajes visibles del framework y del negocio (growl, messages, message, alerts)."""
        texts: List[str] = []
        for selector in (".ui-growl-item", ".ui-messages", ".ui-message", ".alert", "[role=alert]", ".text-danger", ".error"):
            try:
                for el in self.page.locator(selector).all():
                    if el.is_visible():
                        t = " ".join(el.inner_text().split())
                        if t and t not in texts:
                            texts.append(t[:300])
            except Exception:
                continue
        return texts

    def _route_of(self, url: str) -> str:
        path = url[len(self.base):] if url.startswith(self.base) else url
        return path.split("?")[0].split(";")[0] or "/"

    # ------------------------------------------------------------ credenciales

    def grant_credentials(self, docker_compose: Optional[Path]) -> List[str]:
        """Fija la contraseña de un usuario por rol en la base desechable. Devuelve qué roles quedaron listos.

        `credentials.setup_sql` (opcional) corre UNA vez antes — lo que el encoder necesite,
        p. ej. la extensión que da `crypt()`/`gen_salt()`. En la primera corrida en frío la
        base recién restaurada no la tenía y los seis roles quedaron "sin credencial" sin que
        nada se detuviera: un fallo de credenciales es fatal, no una nota al pie."""
        creds = self.config.get("credentials") or {}
        sql_template = creds.get("sql")
        if not sql_template or docker_compose is None:
            return [r["name"] for r in self.config.get("roles", []) if r.get("password")]

        secrets = [r["password"] for r in self.config.get("roles", []) if r.get("password")]

        # El cliente de la base lo declara el perfil (`rehydrate.database.probe`: psql, mysql…);
        # `pepper explore --profile` lo copia a `credentials.client`. Sin perfil, psql como antes.
        client = creds.get("client") or ["psql", "-v", "ON_ERROR_STOP=1", "-U", "{db_user}", "-d", "{db_name}", "-Atc", "{sql}"]
        client_env = creds.get("client_env") or {}
        # El UPDATE con la clave de prueba y el usuario real no debe quedar en el log de la base
        # (que viaja al paquete): el perfil dice cómo callar la sesión.
        quiet = creds.get("quiet_prefix", "SET log_statement = 'none'; " if not creds.get("client") else "")

        def psql(sql: str) -> "subprocess.CompletedProcess[str]":
            values = {"db_user": creds.get("db_user", "postgres"), "db_name": creds["db_name"],
                      "db_password": creds.get("db_password", "")}
            # los marcadores también dentro del SQL (`UPDATE {db_name}.user …`): un cliente como
            # `mysql -e` no selecciona base, y `.format` no entra en el valor de `{sql}`
            for key, value in values.items():
                sql = sql.replace("{" + key + "}", str(value))
            values["sql"] = quiet + sql
            argv = [str(part).format(**values) for part in client]
            env_flags = [f for k, v in client_env.items() for f in ("-e", f"{k}={str(v).format(**values)}")]
            command = ["docker", "compose", "-f", str(docker_compose), "exec", "-T", *env_flags,
                       creds.get("db_service", "db"), *argv]
            result = subprocess.run(command, capture_output=True, text=True)
            err = result.stderr
            for secret in secrets + ([creds["db_password"]] if creds.get("db_password") else []):
                err = err.replace(secret, "[REDACTADO]")
            err = "\n".join(line for line in err.splitlines() if not line.startswith(("LINE ", "        ")))
            result.stderr = err
            return result

        setup = creds.get("setup_sql")
        if setup:
            result = psql(setup)
            if result.returncode != 0:
                error = result.stderr.strip()[:300]
                self._write(Action(role="*", route="", kind="credentials", label="setup_sql", started=_now(),
                                   result="error", detail={"stderr": error}))
                raise CredentialsError(f"credentials.setup_sql falló en la base desechable: {error}")
        ready: List[str] = []
        failures: List[str] = []
        for role in self.config.get("roles", []):
            if not role.get("user") or not role.get("password"):
                continue
            sql = sql_template.replace("{user}", role["user"]).replace("{password}", role["password"])
            result = psql(sql)
            if result.returncode == 0:
                ready.append(role["name"])
            else:
                error = result.stderr.strip()[:300]
                failures.append(f"{role['name']}: {error}")
                self._write(Action(role=role["name"], route="", kind="credentials", started=_now(), result="error",
                                   detail={"stderr": error}))
        if not ready:
            raise CredentialsError("ningún rol quedó con credencial en la base desechable; sin eso no hay nada que explorar. "
                               + " · ".join(failures[:3]))
        self.ready = ready
        return ready

    # ------------------------------------------------------------ sesión

    def login(self, role: Dict[str, Any]) -> bool:
        """Entra con un rol en un contexto de navegador NUEVO y comprueba quién quedó autenticado."""
        login = self.config["login"]
        self._fresh_context()
        action = Action(role=role["name"], route=login["route"], kind="login", label=f"entrar como {role['name']}", started=_now())
        try:
            self.page.goto(self.base + login["route"])
            self._settle(400)
            self.page.fill(login["user_field"], role["user"])
            self.page.fill(login["password_field"], role["password"])
            self.page.click(login["submit"])
            self._settle()
        except Exception as error:
            action.result, action.detail = "error", {"error": str(error)[:200], "falla": "explorador"}
            action.screenshot = self._shot(f"{role['name']}_login_error")
            self._write(action)
            return False
        action.url_after = self.page.url
        action.messages = self._messages()
        ok = self._route_of(self.page.url) != login["route"] and not any(
            login.get("failure_text") and login["failure_text"] in m for m in action.messages)
        action.result = "ok" if ok else "rejected"
        if not ok:
            action.detail = {"falla": "acceso"}
        elif not self._identity_confirmed(role, action):
            # Entró, pero no se puede señalar QUIÉN: atribuirle permisos sería adivinar.
            action.result = "error"
            ok = False
        action.screenshot = self._shot(f"{role['name']}_login")
        self._write(action)
        return ok

    def _identity_confirmed(self, role: Dict[str, Any], action: Action) -> bool:
        """El texto que el sistema muestra solo al usuario autenticado (`login.identity_text`, con
        `{user}` y `{role}`) tiene que estar en pantalla — o en `login.identity_route`, si la
        identidad se ve en otra página. Sin eso no se explora con ese rol."""
        login = self.config["login"]
        expected = identity_text(login, role)
        try:
            if login.get("identity_route"):
                self.page.goto(self.base + login["identity_route"])
                self._settle(400)
            found = bool(expected) and expected in (self.page.locator("body").inner_text() or "")
        except Exception as error:  # noqa: BLE001
            action.detail = {"falla": "explorador", "identidad": "sin comprobar", "error": str(error)[:200]}
            return False
        if found:
            action.detail = {"identidad": "confirmada"}
            return True
        action.detail = {"falla": "identidad", "identidad": "no confirmada",
                         "error": "el texto de identidad del rol no aparece tras entrar"}
        return False

    def logout(self, role: str) -> None:
        """Sale por la ruta del sistema (queda como evidencia) y descarta el contexto entero."""
        route = self.config.get("logout_route")
        if route:
            action = Action(role=role, route=route, kind="logout", label="salir", started=_now())
            try:
                self.page.goto(self.base + route)
                self._settle(300)
                action.result = "ok"
            except Exception as error:
                action.result, action.detail = "error", {"error": str(error)[:200], "falla": "explorador"}
            self._write(action)
        self._fresh_context()

    # ------------------------------------------------------------ pantallas

    def snapshot(self) -> Dict[str, Any]:
        """Lo que hay en pantalla: título, encabezados, botones visibles, campos, selects."""
        data: Dict[str, Any] = {"title": self.page.title()}
        try:
            data["headings"] = [" ".join(t.split()) for t in self.page.locator("h1, h2, h3, h4").all_inner_texts() if t.strip()][:12]
        except Exception:
            data["headings"] = []
        buttons: List[str] = []
        for el in self.page.locator("button, input[type=submit], a.ui-button, .ui-commandlink").all():
            try:
                if el.is_visible():
                    t = " ".join((el.inner_text() or el.get_attribute("value") or el.get_attribute("title") or "").split())
                    if t and t not in buttons:
                        buttons.append(t[:60])
            except Exception:
                continue
        data["buttons"] = buttons[:40]
        fields: List[Dict[str, str]] = []
        for el in self.page.locator("input, textarea").all():
            try:
                if not el.is_visible():
                    continue
                t = (el.get_attribute("type") or "text").lower()
                if t in ("hidden", "submit", "button"):
                    continue
                fields.append({"id": el.get_attribute("id") or el.get_attribute("name") or "", "type": t,
                               "placeholder": el.get_attribute("placeholder") or ""})
            except Exception:
                continue
        data["fields"] = fields[:60]
        try:
            data["selects"] = [s.get_attribute("id") or "" for s in self.page.locator(".ui-selectonemenu").all() if s.is_visible()][:40]
        except Exception:
            data["selects"] = []
        return data

    def open_screen(self, role: str, route: str) -> Action:
        action = Action(role=role, route=route, kind="screen", label=f"abrir {route}", started=_now())
        status: Optional[int] = None
        try:
            response = self.page.goto(self.base + route)
            status = response.status if response else None
            self._settle(600)
        except Exception as error:
            action.result, action.detail = "error", {"error": str(error)[:200], "falla": "explorador"}
            self._write(action)
            return action
        action.status = status
        action.url_after = self.page.url
        landed = self._route_of(self.page.url)
        login_route = self.config["login"]["route"]
        if landed == login_route and route != login_route:
            action.result = "redirected"
        elif status and status >= 400:
            action.result = "forbidden" if status in (401, 403) else "error"
        else:
            action.result = "ok"
        action.messages = self._messages()
        action.detail = self.snapshot()
        if action.result == "error":
            # un 5xx es lo que el SISTEMA respondió: evidencia observada, no un tropiezo del explorador
            action.detail["falla"] = "sistema"
        action.screenshot = self._shot(f"{role}_{route}")
        self._write(action)
        return action

    def _candidate_buttons(self) -> List[str]:
        names: List[str] = []
        for el in self.page.locator("button, input[type=submit]").all():
            try:
                if not el.is_visible() or not el.is_enabled():
                    continue
                t = " ".join((el.inner_text() or el.get_attribute("value") or "").split())
                if not t or _SKIP_BUTTON_RE.search(t) or t in names:
                    continue
                names.append(t[:60])
            except Exception:
                continue
        # primero los que parecen guardar/buscar; después el resto, con tope
        names.sort(key=lambda n: 0 if _SUBMIT_HINT_RE.search(n) else 1)
        return names[:MAX_BUTTONS_PER_SCREEN]

    def _click_button(self, name: str) -> bool:
        try:
            locator = self.page.get_by_role("button", name=name, exact=True)
            if locator.count() == 0:
                locator = self.page.locator("button, input[type=submit]").filter(has_text=name)
            locator.first.click(timeout=self.timeout_ms)
            self._settle()
            return True
        except Exception:
            return False

    def _submit_result(self, action: Action, route_before: str) -> None:
        action.url_after = self.page.url
        action.messages = self._messages()
        landed = self._route_of(self.page.url)
        if any(_REJECT_TEXT_RE.search(m) for m in action.messages):
            action.result = "rejected"
        elif landed != route_before:
            action.result = "redirected"
        else:
            action.result = "ok"

    def try_empty_submit(self, role: str, route: str) -> None:
        """Con todo vacío, cada botón que parezca guardar: los rechazos son la evidencia más valiosa."""
        for name in self._candidate_buttons():
            if not _SUBMIT_HINT_RE.search(name):
                continue
            self.open_screen_quiet(route)
            action = Action(role=role, route=route, kind="empty_submit", label=name, started=_now())
            if not self._click_button(name):
                action.result, action.detail = "error", {"falla": "explorador", "error": "no se pudo apretar el botón"}
                self._write(action)
                continue
            self._submit_result(action, route)
            action.screenshot = self._shot(f"{role}_{route}_vacio_{name}")
            self._write(action)

    def open_screen_quiet(self, route: str) -> None:
        try:
            self.page.goto(self.base + route)
            self._settle(400)
        except Exception:
            pass

    def fill_form(self) -> Dict[str, str]:
        """Llena lo visible con valores plausibles: primero los inputs (algunos disparan ajax que
        re-dibuja el formulario, p. ej. la CURP), después los selects de PrimeFaces por su panel
        (disparan cascadas estado → municipio → colonia), radios y checks."""
        filled: Dict[str, str] = {}
        for el in self.page.locator("input, textarea").all():
            try:
                if not el.is_visible() or not el.is_enabled():
                    continue
                t = (el.get_attribute("type") or "text").lower()
                fid = el.get_attribute("id") or el.get_attribute("name") or ""
                if t in ("radio", "checkbox"):
                    if t == "radio" and not el.is_checked():
                        el.check(timeout=2000)
                        filled[fid] = "•"
                    continue
                if t in ("hidden", "submit", "button", "file"):
                    continue
                if el.input_value():
                    continue
                value = plausible_value(fid, el.get_attribute("placeholder") or "", t, self.hints)
                if value is None:
                    continue
                el.fill(value, timeout=2000)
                el.dispatch_event("change")
                filled[fid] = value
            except Exception:
                continue
        self._settle(800)
        # selects: de uno en uno, re-localizando cada vez (la cascada re-dibuja los siguientes)
        seen: set = set()
        for _ in range(40):
            menus = [m for m in self.page.locator(".ui-selectonemenu").all()
                     if m.is_visible() and (m.get_attribute("id") or "") not in seen]
            if not menus:
                break
            menu = menus[0]
            mid = menu.get_attribute("id") or f"menu-{len(seen)}"
            seen.add(mid)
            try:
                menu.locator(".ui-selectonemenu-trigger").click(timeout=3000)
                panel = self.page.locator(f"[id='{mid}_panel'] li.ui-selectonemenu-item")
                panel.first.wait_for(state="visible", timeout=3000)
                items = panel.all()
                target = items[1] if len(items) > 1 else items[0]
                text = " ".join(target.inner_text().split())
                target.click(timeout=3000)
                filled[mid] = text[:40]
                self._settle(700)
            except Exception:
                try:
                    self.page.keyboard.press("Escape")
                except Exception:
                    pass
                continue
        self._settle(600)
        return filled

    def try_filled_submit(self, role: str, route: str) -> None:
        for name in self._candidate_buttons():
            if not _SUBMIT_HINT_RE.search(name):
                continue
            self.open_screen_quiet(route)
            action = Action(role=role, route=route, kind="filled_submit", label=name, started=_now())
            action.detail = {"filled": self.fill_form()}
            if not self._click_button(name):
                action.result = "error"
                action.detail.update({"falla": "explorador", "error": "no se pudo apretar el botón"})
                self._write(action)
                continue
            self._submit_result(action, route)
            action.screenshot = self._shot(f"{role}_{route}_lleno_{name}")
            self._write(action)

    # ------------------------------------------------------------ recorridos

    def routes(self) -> List[str]:
        declared = self.config.get("routes")
        if declared:
            return list(declared)
        skip = {self.config["login"]["route"], self.config.get("logout_route", "")}
        seen: List[str] = []
        for e in self.map.get("entrypoints", []):
            if e.get("kind") in ("http_route", "screen") and e.get("method", "GET") in ("GET", "") \
                    and e["path"] not in skip and e["path"] not in seen and "{" not in e["path"]:
                seen.append(e["path"])
        return seen

    def _out_of_time(self) -> bool:
        return bool(self.deadline) and time.time() > self.deadline

    def walk(self, docker_compose: Optional[Path] = None, submit: bool = True) -> Dict[str, Any]:
        """El recorrido automático completo: credenciales → por rol: entrar, cada pantalla, rechazos, llenado, salir.

        Cada rol entra en un contexto de navegador nuevo y con su identidad comprobada; si el
        presupuesto de tiempo se agota, el resumen lo dice (`interrupted`) y el veredicto es
        INTERRUMPIDO, no un éxito con menos pantallas."""
        ready = self.ready if self.ready is not None else self.grant_credentials(docker_compose)
        routes = self.routes()
        summary: Dict[str, Any] = {"mode": "walk", "roles": {}, "routes": len(routes)}
        for role in self.config.get("roles", []):
            if self._out_of_time():
                summary["roles"][role["name"]] = "sin recorrer: presupuesto de tiempo agotado"
                summary["interrupted"] = "presupuesto de tiempo agotado"
                continue
            if role["name"] not in ready:
                summary["roles"][role["name"]] = "sin credencial"
                continue
            if not self.login(role):
                falla = (self.actions[-1].detail or {}).get("falla") if self.actions else None
                summary["roles"][role["name"]] = {"acceso": "login rechazado", "identidad": "identidad no confirmada"}.get(
                    falla or "", "login con error del explorador")
                continue
            opened = rejected = visited = 0
            for route in routes:
                if self._out_of_time():
                    summary["interrupted"] = "presupuesto de tiempo agotado"
                    break
                visited += 1
                action = self.open_screen(role["name"], route)
                if action.result != "ok":
                    continue
                opened += 1
                if submit and role.get("submit", True) and (action.detail.get("fields") or action.detail.get("selects")):
                    before = len(self.actions)
                    self.try_empty_submit(role["name"], route)
                    self.try_filled_submit(role["name"], route)
                    rejected += sum(1 for a in self.actions[before:] if a.result == "rejected")
            self.logout(role["name"])
            text = f"{opened}/{len(routes)} pantallas, {rejected} rechazos provocados"
            if visited < len(routes):
                text += f" (cortado por presupuesto: {len(routes) - visited} rutas sin visitar)"
            summary["roles"][role["name"]] = text
        return summary

    def run_plan(self, plan: List[Dict[str, Any]], docker_compose: Optional[Path] = None) -> Dict[str, Any]:
        """Pasos escritos por el agente. Cada paso es un dict con UNA acción:
        login: <rol> · goto: <ruta> · fill: {selector: valor} · select: {id_selectonemenu: texto} ·
        click: <texto del botón> · click_at: <selector css> · check: <selector> · wait: <segundos> ·
        note: <texto> · logout: true · y las comprobaciones: expect_text: <texto> ·
        expect_absent: <texto> · expect_route: <ruta> · expect_rejected: <texto o true>
        y, junto a la acción, su declaración: `efecto` ("modifica" | "consulta"; obligatorio en los
        clics), `id`, `porque`; y en las comprobaciones, `comprueba: <id>` (ver `plan_problems`).

        Cada paso queda con `detail.paso`, `detail.tipo`, lo declarado (`efecto`, `id`,
        `comprueba`) y, si falló, `detail.falla`: explorador | negocio | verificacion | acceso |
        identidad | sistema | declaracion (un clic declarado "consulta" tras el cual el sistema dijo
        que guardó algo). Con el presupuesto vencido no se corre ningún paso más."""
        if self.ready is None:
            self.grant_credentials(docker_compose)
        role = ""
        aborted = False
        roles = {r["name"]: r for r in self.config.get("roles", [])}
        executed = 0
        summary: Dict[str, Any] = {"mode": "plan", "steps": len(plan)}
        for index, step in enumerate(plan, 1):
            if self._out_of_time():
                summary["interrupted"] = f"presupuesto de tiempo agotado antes del paso {index}"
                self._write(Action(role=role, route="", kind="plan", label=f"{index}. presupuesto agotado", started=_now(),
                                   result="skipped", detail={"falla": "presupuesto", "sin_correr": len(plan) - index + 1}))
                break
            key, value = step_action(step)
            declared = {k: step[k] for k in ("efecto", "id", "comprueba", "porque") if isinstance(step, dict) and k in step}
            effect = f" [{declared['efecto']}]" if declared.get("efecto") else ""
            action = Action(role=role, route=self._route_of(self.page.url) if self.page.url.startswith("http") else "",
                            kind="plan", label=f"{index}. {key}{effect}: {json.dumps(value, ensure_ascii=False)[:80]}",
                            started=_now())
            try:
                if key is None:
                    action.result, action.detail = "error", {"falla": "explorador", "error": "el paso no tiene exactamente una acción"}
                elif aborted and key != "login":
                    action.result, action.detail = "skipped", {"falla": "omitido", "why": "el login anterior falló"}
                elif key == "login":
                    role = value
                    action.role = role
                    if value not in roles:
                        raise KeyError(f"rol desconocido en el plan: {value}")
                    entered = self.login(roles[value])
                    login_detail = self.actions[-1].detail if self.actions else {}
                    action.result = "ok" if entered else ("rejected" if login_detail.get("falla") == "acceso" else "error")
                    if not entered:
                        action.detail = {"falla": login_detail.get("falla", "explorador")}
                    aborted = not entered
                elif key == "goto":
                    response = self.page.goto(self.base + value)
                    self._settle(600)
                    action.status = response.status if response else None
                    action.result = "ok"
                    if action.status and action.status >= 400:
                        action.result, action.detail = "error", {"falla": "sistema"}
                elif key == "fill":
                    for selector, text in value.items():
                        self._fill_input(selector, str(text))
                    self._settle(500)
                    action.result = "ok"
                elif key == "select":
                    chosen = {mid: self._select_by_text(mid, str(text)) for mid, text in value.items()}
                    action.detail = {"selected": chosen}
                    action.result = "ok"
                elif key == "click":
                    before = self._messages()
                    if not self._click_button(str(value)):
                        action.result, action.detail = "error", {"falla": "explorador", "error": "no se pudo apretar el botón"}
                    else:
                        action.messages = self._messages()
                        action.result = "ok"
                        if any(_REJECT_TEXT_RE.search(m) for m in action.messages):
                            action.result, action.detail = "rejected", {"falla": "negocio"}
                        self._check_declaration(action, declared, before)
                elif key == "check":
                    self.page.check(value)
                    action.result = "ok"
                elif key == "click_at":
                    # un control por selector CSS (p. ej. la caja visible de un radio de PrimeFaces,
                    # cuyo <input> real está oculto y no se puede marcar directo)
                    before = self._messages()
                    self.page.locator(str(value)).first.click(timeout=self.timeout_ms)
                    self._settle()
                    action.messages = self._messages()
                    action.result = "ok"
                    if any(_REJECT_TEXT_RE.search(m) for m in action.messages):
                        action.result, action.detail = "rejected", {"falla": "negocio"}
                    self._check_declaration(action, declared, before)
                elif key == "wait":
                    self.page.wait_for_timeout(int(float(value) * 1000))
                    action.result = "ok"
                elif key == "expect_text":
                    found = self.page.get_by_text(str(value)).count() > 0 or any(str(value) in m for m in self._messages())
                    action.result = "ok" if found else "error"
                elif key == "expect_absent":
                    present = self.page.get_by_text(str(value)).count() > 0 or any(str(value) in m for m in self._messages())
                    action.result = "error" if present else "ok"
                elif key == "expect_route":
                    action.result = "ok" if self._route_of(self.page.url) == value else "error"
                elif key == "expect_rejected":
                    action.messages = self._messages()
                    wanted = "" if value is True else str(value)
                    seen = any(wanted in m for m in action.messages) if wanted else any(_REJECT_TEXT_RE.search(m) for m in action.messages)
                    action.result = "ok" if seen else "error"
                elif key == "note":
                    action.result = "ok"
                elif key == "logout":
                    self.logout(role)
                    action.result = "ok"
                else:
                    action.result, action.detail = "error", {"falla": "explorador", "error": f"paso desconocido: {key}"}
                if key in EXPECT_KEYS and action.result == "error" and not action.detail:
                    action.detail = {"falla": "verificacion"}
            except Exception as error:  # noqa: BLE001
                action.result, action.detail = "error", {"falla": "explorador", "error": str(error)[:300]}
            action.detail = dict(action.detail, paso=index, tipo=key, **declared)
            action.url_after = self.page.url if self.page.url.startswith("http") else ""
            if key in ("click", "click_at", "goto", "expect_text", "expect_absent", "expect_route", "expect_rejected", "select", "fill"):
                action.screenshot = self._shot(f"plan_{index:02d}_{key}")
            self._write(action)
            executed += 1
        summary["executed"] = executed
        return summary

    @staticmethod
    def _check_declaration(action: Action, declared: Dict[str, Any], before: List[str]) -> None:
        """Un clic declarado "consulta" tras el cual el SISTEMA dice que guardó algo (un mensaje que
        no estaba antes del clic) contradice la declaración: queda como falla `declaracion` y el
        veredicto no puede ser COMPLETO."""
        if declared.get("efecto") == "consulta" and action.result == "ok" \
                and any(_SAVED_TEXT_RE.search(m) for m in action.messages if m not in before):
            action.detail = {"falla": "declaracion",
                             "error": "declarado \"consulta\", pero el sistema respondió que guardó algo"}


STEP_KEYS = ("login", "goto", "fill", "select", "click", "click_at", "check", "wait", "note", "logout",
             "expect_text", "expect_absent", "expect_route", "expect_rejected")
EXPECT_KEYS = ("expect_text", "expect_absent", "expect_route", "expect_rejected")
# Lo que un paso puede declarar además de su acción.
META_KEYS = ("efecto", "id", "comprueba", "porque")
EFFECTS = ("modifica", "consulta")
# Pasos que pueden cambiar datos: pueden declarar `efecto`; los clics DEBEN declararlo.
_EFFECT_KEYS = ("goto", "fill", "select", "check", "click", "click_at")
_CLICK_KEYS = ("click", "click_at")
# Texto de botón que suele guardar: no decide nada (la declaración decide), pero declarar
# "consulta" en uno de estos exige decir por qué.
_COMMIT_RE = re.compile(r"(?i)guardar|registr|agregar|enviar|aceptar|agendar|confirmar|tomar|iniciar|finalizar|aplicar|"
                        r"cargar|generar|continuar|crear|alta|autoriz|aprob|rechaz|cerrar|asignar|turnar|firmar|capturar|"
                        r"eliminar|borrar|baja|save|submit|delete")
# Lo que el SISTEMA dice cuando guardó algo. Tras un clic declarado "consulta", contradice la declaración.
# Solo tiempo pasado ("se guardó", "registro guardado con éxito"): "se actualiza cada minuto" no cuenta.
_SAVED_TEXT_RE = re.compile(
    r"(?i)\bse\s+(?:ha[n]?\s+)?(?:guard[oó]|registr[oó]|agreg[oó]|actualiz[oó]|elimin[oó]|envi[oó]|cre[oó]|"
    r"guardad[oa]s?|registrad[oa]s?|agregad[oa]s?|actualizad[oa]s?|eliminad[oa]s?|enviad[oa]s?|cread[oa]s?|"
    r"di[oó]\s+de\s+alta|dad[oa]\s+de\s+alta)\b|"
    r"\b(?:guardad|registrad|actualizad|eliminad|agregad|cread)[oa]s?\s+(?:con\s+[eé]xito|correctamente|exitosamente|satisfactoriamente)|"
    r"\b(?:registro|guardado|alta)\s+exitos[oa]")


def step_action(step: Dict[str, Any]) -> Tuple[Optional[str], Any]:
    """(acción, valor) de un paso: la única clave de STEP_KEYS; (None, None) si no hay exactamente una."""
    keys = [k for k in step if k in STEP_KEYS] if isinstance(step, dict) else []
    return (keys[0], step[keys[0]]) if len(keys) == 1 else (None, None)


def plan_problems(plan: Any) -> List[str]:
    """Qué impide correr un plan; vacío si se puede. Se comprueba ANTES de abrir el navegador.

    Que un botón se deje apretar no demuestra que el trámite exista. Por eso el plan DECLARA qué
    cambia datos y cómo se comprueba, sin depender del texto del botón ni del selector:
      - cada `click` / `click_at` declara `efecto`: "modifica" o "consulta";
      - todo paso que modifica lleva `id`, y al menos una comprobación POSTERIOR lo nombra con
        `comprueba: <id>` (en la misma pantalla o después, con otro rol);
      - `expect_rejected` dice qué rechazo esperaba: `comprueba: <id>`;
      - un clic cuyo texto suele guardar (guardar, registrar, enviar…) declarado "consulta" dice
        `porque`."""
    if not isinstance(plan, list) or not plan:
        return ["el plan debe ser una lista de pasos no vacía"]
    problems: List[str] = []
    ids: Dict[str, int] = {}
    parsed: List[Tuple[int, Optional[str], Any, Dict[str, Any]]] = []
    for index, step in enumerate(plan, 1):
        if not isinstance(step, dict):
            problems.append(f"paso {index}: cada paso es un objeto")
            continue
        key, value = step_action(step)
        if key is None:
            problems.append(f"paso {index}: lleva exactamente UNA acción ({', '.join(STEP_KEYS)})")
            continue
        extra = [k for k in step if k != key and k not in META_KEYS]
        if extra:
            problems.append(f"paso {index}: claves desconocidas {', '.join(map(repr, extra))} (además de la acción: {', '.join(META_KEYS)})")
        meta = {k: step[k] for k in META_KEYS if k in step}
        parsed.append((index, key, value, meta))
        effect = meta.get("efecto")
        if key in _CLICK_KEYS and effect is None:
            problems.append(f"paso {index}: {key} {str(value)!r} no declara su efecto: \"efecto\": \"modifica\" o \"consulta\"")
        if effect is not None and key not in _EFFECT_KEYS:
            problems.append(f"paso {index}: {key} no lleva efecto (solo {', '.join(_EFFECT_KEYS)})")
        elif effect is not None and effect not in EFFECTS:
            problems.append(f"paso {index}: efecto {effect!r} no es \"modifica\" ni \"consulta\"")
        if "porque" in meta and (effect is None or not str(meta["porque"]).strip()):
            problems.append(f"paso {index}: \"porque\" acompaña a un efecto declarado y no va vacío")
        if effect == "consulta" and key in _CLICK_KEYS and _COMMIT_RE.search(str(value)) and not str(meta.get("porque", "")).strip():
            problems.append(f"paso {index}: {key} {str(value)!r} suele guardar y se declaró \"consulta\": di \"porque\" "
                            "(p. ej. descarga un PDF) o declara \"modifica\" con su comprobación")
        if "id" in meta:
            if key in EXPECT_KEYS:
                problems.append(f"paso {index}: una comprobación no lleva id; usa \"comprueba\" para nombrar la acción")
            elif not isinstance(meta["id"], str) or not meta["id"].strip():
                problems.append(f"paso {index}: id vacío")
            elif meta["id"] in ids:
                problems.append(f"paso {index}: id {meta['id']!r} repetido (ya es del paso {ids[meta['id']]})")
            else:
                ids[meta["id"]] = index
        if "comprueba" in meta:
            if key not in EXPECT_KEYS:
                problems.append(f"paso {index}: solo una comprobación (expect_*) lleva \"comprueba\"")
            elif meta["comprueba"] not in ids:
                problems.append(f"paso {index}: comprueba {meta['comprueba']!r}, que no es el id de un paso ANTERIOR")
        if key == "expect_rejected" and "comprueba" not in meta:
            problems.append(f"paso {index}: expect_rejected dice qué rechazo esperaba: \"comprueba\": <id de la acción>")
    if not any(key in EXPECT_KEYS for _, key, _, _ in parsed):
        problems.append("el plan no comprueba nada: agrega al menos un expect_text, expect_absent, expect_route o expect_rejected")
    checked = {meta["comprueba"] for _, key, _, meta in parsed if key in EXPECT_KEYS and "comprueba" in meta}
    for index, key, value, meta in parsed:
        if meta.get("efecto") != "modifica":
            continue
        if not meta.get("id"):
            problems.append(f"paso {index}: {key} {str(value)!r} modifica datos y no tiene \"id\" para que una comprobación lo nombre")
        elif meta["id"] not in checked:
            problems.append(f"paso {index}: {key} {str(value)!r} modifica datos y ninguna comprobación posterior dice "
                            f"\"comprueba\": \"{meta['id']}\"")
    return problems


# Veredicto de una exploración. El código de salida lo dice sin leer la salida: solo COMPLETO es 0.
STATUS_CODES = {"COMPLETO": 0, "FALLIDO": 1, "PARCIAL": 3, "INTERRUMPIDO": 4}


def _detail(record: Dict[str, Any]) -> Dict[str, Any]:
    return record.get("detail") or {}


def plan_verdict(records: List[Dict[str, Any]], steps: int) -> Dict[str, Any]:
    """Reconcilia los pasos registrados de un plan: qué acciones cambiaron datos y cuáles quedaron
    comprobadas por su propia comprobación (`comprueba`), qué rechazó el negocio a propósito, qué
    falló y de quién fue la falla, y cuántos pasos no llegaron a correr.

    COMPLETO exige que CADA acción que modifica tenga su comprobación cumplida: comprobaciones de
    otras pantallas no cuentan por ella. Si ninguna quedó comprobada, el plan es FALLIDO."""
    ran = [r for r in records if r.get("kind") == "plan" and "paso" in _detail(r)]
    by_id = {_detail(r)["id"]: r for r in ran if _detail(r).get("id")}
    expectations = [r for r in ran if _detail(r).get("tipo") in EXPECT_KEYS]
    passed = [r for r in expectations if r.get("result") == "ok"]
    checks: Dict[str, List[Dict[str, Any]]] = {}
    for r in expectations:
        target = _detail(r).get("comprueba")
        if target:
            checks.setdefault(target, []).append(r)
    # un expect_rejected cumplido vuelve ESPERADO el rechazo de la acción que nombra
    expected = {_detail(by_id[_detail(r)["comprueba"]])["paso"] for r in passed
                if _detail(r).get("tipo") == "expect_rejected" and _detail(r).get("comprueba") in by_id
                and by_id[_detail(r)["comprueba"]].get("result") == "rejected"}
    modifying = [r for r in ran if _detail(r).get("efecto") == "modifica"]
    verified = [r for r in modifying if any(c.get("result") == "ok" for c in checks.get(_detail(r).get("id") or "", []))]
    failures: Dict[str, int] = {}

    def fail(kind: str) -> None:
        failures[kind] = failures.get(kind, 0) + 1

    for r in ran:
        d = _detail(r)
        if r.get("result") in ("error", "rejected") and d["paso"] not in expected:
            fail(d.get("falla") or "explorador")
        elif d.get("falla") == "declaracion":
            fail("declaracion")
    for r in modifying:
        # corrió, pero nada lo nombra: comprobaciones de otras cosas no cuentan por él
        if r.get("result") in ("ok", "rejected") and not checks.get(_detail(r).get("id") or ""):
            fail("sin_comprobar")
    skipped = sum(1 for r in ran if r.get("result") == "skipped")
    not_run = max(steps - len(ran), 0)
    counts = {"pasos": steps, "corridos": len(ran), "sin_correr": not_run, "omitidos": skipped,
              "comprobaciones": len(expectations), "comprobaciones_ok": len(passed),
              "modifican": len(modifying), "modifican_comprobadas": len(verified),
              "rechazos_esperados": len(expected), "fallas": failures}
    fallas = ", ".join(f"{n} de {k}" for k, n in sorted(failures.items()))
    mods = f"{len(verified)}/{len(modifying)} acciones que modifican datos comprobadas"
    checked = f"{len(passed)}/{len(expectations)} comprobaciones cumplidas"
    if not_run:
        status, reason = "INTERRUMPIDO", f"{not_run} de {steps} pasos sin correr"
    elif not passed or (modifying and not verified):
        status, reason = "FALLIDO", (f"{mods}; {checked}" if modifying else f"ninguna comprobación del plan se cumplió ({len(expectations)} escritas)") \
            + (f"; fallas: {fallas}" if fallas else "")
    elif not failures and not skipped and len(verified) == len(modifying):
        status, reason = "COMPLETO", f"{mods}; {checked}; {len(expected)} rechazos esperados"
    else:
        status, reason = "PARCIAL", f"{mods}; {checked}" + (f"; fallas: {fallas}" if fallas else "") \
            + (f"; {skipped} pasos omitidos" if skipped else "")
    return {"status": status, "reason": reason, "counts": counts}


def walk_verdict(summary: Dict[str, Any], records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """El recorrido automático: COMPLETO solo si todos los roles entraron con identidad confirmada,
    cada ruta dio una respuesta del sistema y el explorador no tropezó en ningún lado."""
    roles = summary.get("roles") or {}
    entered = [r for r, s in roles.items() if "pantallas" in str(s)]
    missing = {r: s for r, s in roles.items() if r not in entered}
    stumbles = sum(1 for r in records if _detail(r).get("falla") == "explorador")
    screens_ok = sum(1 for r in records if r.get("kind") == "screen" and r.get("result") == "ok")
    counts = {"roles": len(roles), "roles_dentro": len(entered), "pantallas_ok": screens_ok, "fallas_explorador": stumbles}
    if summary.get("interrupted"):
        return {"status": "INTERRUMPIDO", "reason": str(summary["interrupted"]), "counts": counts}
    if not entered:
        return {"status": "FALLIDO", "reason": "ningún rol entró al sistema: " + ", ".join(f"{r}: {s}" for r, s in roles.items()),
                "counts": counts}
    if not screens_ok:
        return {"status": "FALLIDO", "reason": "no se abrió ninguna pantalla", "counts": counts}
    if not missing and not stumbles:
        return {"status": "COMPLETO", "reason": f"{len(entered)} roles, {screens_ok} pantallas abiertas", "counts": counts}
    parts = [f"{r}: {s}" for r, s in missing.items()]
    if stumbles:
        parts.append(f"{stumbles} acciones que el explorador no pudo hacer")
    return {"status": "PARCIAL", "reason": "; ".join(parts), "counts": counts}


def config_problems(config: Dict[str, Any]) -> List[str]:
    """Qué le falta a explore.json para poder correr; vacío si está completo. Antes un KeyError
    a mitad del arranque era todo lo que veía el humano."""
    problems: List[str] = []
    if not isinstance(config.get("base_url"), str) or not config["base_url"].startswith("http"):
        problems.append("falta base_url (http://127.0.0.1:<puerto del ingress>)")
    login = config.get("login") or {}
    for key in ("route", "user_field", "password_field", "submit"):
        if not login.get(key):
            problems.append(f"falta login.{key}")
    roles = config.get("roles") or []
    if not roles:
        problems.append("falta roles (al menos uno con name, user y password)")
    for role in roles:
        if not all(role.get(k) for k in ("name", "user", "password")):
            problems.append(f"rol incompleto: {role.get('name') or '?'} (name, user, password)")
        elif not identity_text(login, role):
            problems.append(f"rol {role['name']}: falta login.identity_text (o identity_text del rol): el texto que el "
                            "sistema muestra solo a quien entró, p. ej. \"{user}\"; sin él no se sabe con qué identidad se exploró")
    creds = config.get("credentials")
    if creds and creds.get("sql") and not creds.get("db_name"):
        problems.append("credentials.sql sin credentials.db_name")
    return problems


def outcome(summary: Dict[str, Any], actions: List[Dict[str, Any]], captured_files: List[str],
            plan_steps: Optional[int] = None) -> Dict[str, Any]:
    """El veredicto de la sesión: {status, code, reason, counts}. Solo COMPLETO sale con 0."""
    if plan_steps is not None or summary.get("mode") == "plan":
        verdict = plan_verdict(actions, plan_steps if plan_steps is not None else int(summary.get("steps", 0)))
    else:
        verdict = walk_verdict(summary, actions)
    if summary.get("error"):
        verdict = dict(verdict, status="INTERRUMPIDO", reason=f"el explorador se detuvo por un error: {summary['error']}")
    elif summary.get("interrupted") and verdict["status"] != "INTERRUMPIDO":
        verdict = dict(verdict, status="INTERRUMPIDO", reason=f"recorrido interrumpido: {summary['interrupted']}")
    if verdict["status"] in ("COMPLETO", "PARCIAL") and "http.jsonl" not in captured_files:
        verdict = dict(verdict, status="FALLIDO",
                       reason="no se capturó http.jsonl del ingress: sin él no hay correlation_id y Correlate no tiene anclas")
    return dict(verdict, code=STATUS_CODES[verdict["status"]])


def operator_note(actions: List[Dict[str, Any]], summary: Dict[str, Any], kind: str,
                  verdict: Optional[Dict[str, Any]] = None) -> str:
    """La nota de la sesión, redactada desde lo que el explorador hizo (no se le pregunta a nadie)."""
    roles = ", ".join(f"{r}: {s}" for r, s in (summary.get("roles") or {}).items())
    rejected = [a for a in actions if a.get("result") == "rejected"]
    redirected = [a for a in actions if a.get("result") == "redirected" and a.get("kind") == "screen"]
    messages: List[str] = []
    for a in rejected:
        for m in a.get("messages", []):
            if m not in messages:
                messages.append(m)
    parts = [f"Sesión ejercitada POR EL AGENTE (explorador de PEPPER) con un navegador headless local, todo por el ingress; modo {kind}."]
    if verdict:
        parts.append(f"Resultado: {verdict['status']} — {verdict['reason']}.")
    if roles:
        parts.append(f"Roles: {roles}.")
    parts.append(f"{len(actions)} acciones registradas en explore.jsonl; {len(rejected)} rechazos provocados; "
                 f"{len(redirected)} pantallas que mandaron a login.")
    if messages:
        parts.append("Mensajes de rechazo vistos: " + " | ".join(m[:120] for m in messages[:15]) + ".")
    return " ".join(parts)


def write_session(out_dir: Path, session_id: str, flow_name: str, started: datetime, ended: datetime,
                  profile_id: Optional[str], note: str, collectors: List[Dict[str, str]],
                  verdict: Optional[Dict[str, Any]] = None) -> Path:
    tz = started.strftime("%z")
    tz = f"{tz[:3]}:{tz[3:]}" if tz else "Z"
    session = {
        "session_id": session_id,
        "flow_name": flow_name,
        "observed_start": started.isoformat(timespec="seconds"),
        "observed_end": ended.isoformat(timespec="seconds"),
        "timezone": tz,
        "operator_note": note,
        "environment": {"profile_id": profile_id, "support_tier": 1},
        "collectors": collectors,
    }
    if verdict:
        session["outcome"] = {"status": verdict["status"], "reason": verdict["reason"], "counts": verdict.get("counts", {})}
    path = out_dir / "session.json"
    path.write_text(json.dumps(session, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
