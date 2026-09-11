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
from typing import Any, Dict, List, Optional

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
        self._browser = None
        self._pw = None

    # ------------------------------------------------------------ infraestructura

    def __enter__(self) -> "Explorer":
        from playwright.sync_api import sync_playwright

        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.shots.mkdir(exist_ok=True)
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
        context = self._browser.new_context(ignore_https_errors=True, viewport={"width": 1366, "height": 900})
        context.set_default_timeout(self.timeout_ms)
        self._page = context.new_page()
        self._page.on("dialog", lambda d: d.accept())
        self._log = self.log_path.open("a", encoding="utf-8")
        return self

    def __exit__(self, *exc) -> None:
        try:
            self._log.close()
        finally:
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
        try:
            self.page.wait_for_load_state("networkidle", timeout=self.timeout_ms)
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

        def psql(sql: str) -> "subprocess.CompletedProcess[str]":
            command = ["docker", "compose", "-f", str(docker_compose), "exec", "-T", creds.get("db_service", "db"),
                       "psql", "-v", "ON_ERROR_STOP=1", "-U", creds.get("db_user", "postgres"), "-d", creds["db_name"], "-Atc", sql]
            return subprocess.run(command, capture_output=True, text=True)

        setup = creds.get("setup_sql")
        if setup:
            result = psql(setup)
            if result.returncode != 0:
                error = result.stderr.strip()[:300]
                self._write(Action(role="*", route="", kind="credentials", label="setup_sql", started=_now(),
                                   result="error", detail={"stderr": error}))
                raise RuntimeError(f"credentials.setup_sql falló en la base desechable: {error}")
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
            raise RuntimeError("ningún rol quedó con credencial en la base desechable; sin eso no hay nada que explorar. "
                               + " · ".join(failures[:3]))
        return ready

    # ------------------------------------------------------------ sesión

    def login(self, role: Dict[str, Any]) -> bool:
        login = self.config["login"]
        action = Action(role=role["name"], route=login["route"], kind="login", label=f"entrar como {role['name']}", started=_now())
        self.page.goto(self.base + login["route"])
        self._settle(400)
        try:
            self.page.fill(login["user_field"], role["user"])
            self.page.fill(login["password_field"], role["password"])
            self.page.click(login["submit"])
            self._settle()
        except Exception as error:
            action.result, action.detail = "error", {"error": str(error)[:200]}
            action.screenshot = self._shot(f"{role['name']}_login_error")
            self._write(action)
            return False
        action.url_after = self.page.url
        action.messages = self._messages()
        ok = self._route_of(self.page.url) != login["route"] and not any(
            login.get("failure_text") and login["failure_text"] in m for m in action.messages)
        action.result = "ok" if ok else "rejected"
        action.screenshot = self._shot(f"{role['name']}_login")
        self._write(action)
        return ok

    def logout(self, role: str) -> None:
        route = self.config.get("logout_route")
        if route:
            action = Action(role=role, route=route, kind="logout", label="salir", started=_now())
            try:
                self.page.goto(self.base + route)
                self._settle(300)
                action.result = "ok"
            except Exception as error:
                action.result, action.detail = "error", {"error": str(error)[:200]}
            self._write(action)
        self.page.context.clear_cookies()

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
            action.result, action.detail = "error", {"error": str(error)[:200]}
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
                action.result = "error"
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

    def walk(self, docker_compose: Optional[Path] = None, submit: bool = True) -> Dict[str, Any]:
        """El recorrido automático completo: credenciales → por rol: entrar, cada pantalla, rechazos, llenado, salir."""
        ready = self.grant_credentials(docker_compose)
        routes = self.routes()
        summary: Dict[str, Any] = {"roles": {}, "routes": len(routes)}
        for role in self.config.get("roles", []):
            if role["name"] not in ready:
                summary["roles"][role["name"]] = "sin credencial"
                continue
            if not self.login(role):
                summary["roles"][role["name"]] = "login rechazado"
                continue
            opened = rejected = 0
            for route in routes:
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
            summary["roles"][role["name"]] = f"{opened}/{len(routes)} pantallas, {rejected} rechazos provocados"
        return summary

    def run_plan(self, plan: List[Dict[str, Any]], docker_compose: Optional[Path] = None) -> Dict[str, Any]:
        """Pasos escritos por el agente. Cada paso es un dict con UNA clave:
        login: <rol> · goto: <ruta> · fill: {selector: valor} · select: {id_selectonemenu: texto} ·
        click: <texto del botón> · click_at: <selector css> · check: <selector> · wait: <segundos> ·
        expect_text: <texto> · expect_route: <ruta> · note: <texto> · logout: true
        """
        self.grant_credentials(docker_compose)
        role = ""
        ok = failed = 0
        roles = {r["name"]: r for r in self.config.get("roles", [])}
        for index, step in enumerate(plan, 1):
            (key, value), = step.items() if len(step) == 1 else (list(step.items())[0],)
            action = Action(role=role, route=self._route_of(self.page.url) if self.page.url.startswith("http") else "",
                            kind="plan", label=f"{index}. {key}: {json.dumps(value, ensure_ascii=False)[:80]}", started=_now())
            try:
                if key == "login":
                    role = value
                    action.role = role
                    action.result = "ok" if self.login(roles[value]) else "rejected"
                elif key == "goto":
                    response = self.page.goto(self.base + value)
                    self._settle(600)
                    action.status = response.status if response else None
                    action.result = "ok"
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
                    action.result = "ok" if self._click_button(str(value)) else "error"
                    action.messages = self._messages()
                    if any(_REJECT_TEXT_RE.search(m) for m in action.messages):
                        action.result = "rejected"
                elif key == "check":
                    self.page.check(value)
                    action.result = "ok"
                elif key == "click_at":
                    # un control por selector CSS (p. ej. la caja visible de un radio de PrimeFaces,
                    # cuyo <input> real está oculto y no se puede marcar directo)
                    self.page.locator(str(value)).first.click(timeout=self.timeout_ms)
                    self._settle()
                    action.result = "ok"
                elif key == "wait":
                    self.page.wait_for_timeout(int(float(value) * 1000))
                    action.result = "ok"
                elif key == "expect_text":
                    found = self.page.get_by_text(str(value)).count() > 0 or any(str(value) in m for m in self._messages())
                    action.result = "ok" if found else "error"
                elif key == "expect_route":
                    action.result = "ok" if self._route_of(self.page.url) == value else "error"
                elif key == "note":
                    action.result = "ok"
                elif key == "logout":
                    self.logout(role)
                    action.result = "ok"
                else:
                    action.result, action.detail = "error", {"error": f"paso desconocido: {key}"}
            except Exception as error:
                action.result, action.detail = "error", {"error": str(error)[:300]}
            action.url_after = self.page.url if self.page.url.startswith("http") else ""
            if key in ("click", "click_at", "goto", "expect_text", "expect_route", "select", "fill"):
                action.screenshot = self._shot(f"plan_{index:02d}_{key}")
            self._write(action)
            ok += action.result == "ok"
            failed += action.result in ("error", "rejected")
        return {"steps": len(plan), "ok": ok, "failed": failed}


def operator_note(actions: List[Dict[str, Any]], summary: Dict[str, Any], kind: str) -> str:
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
    if roles:
        parts.append(f"Roles: {roles}.")
    parts.append(f"{len(actions)} acciones registradas en explore.jsonl; {len(rejected)} rechazos provocados; "
                 f"{len(redirected)} pantallas que mandaron a login.")
    if messages:
        parts.append("Mensajes de rechazo vistos: " + " | ".join(m[:120] for m in messages[:15]) + ".")
    return " ".join(parts)


def write_session(out_dir: Path, session_id: str, flow_name: str, started: datetime, ended: datetime,
                  profile_id: Optional[str], note: str, collectors: List[Dict[str, str]]) -> Path:
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
    path = out_dir / "session.json"
    path.write_text(json.dumps(session, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
