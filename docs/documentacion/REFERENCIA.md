# Referencia de PEPPER

> Para el camino feliz, [`QUICKSTART.md`](QUICKSTART.md). Aquí: qué hace cada comando del núcleo, qué esperar de `/pepper` en cada fase y cómo validar lo que entrega. Qué protege y qué no: [`THREAT-MODEL.md`](THREAT-MODEL.md).

## 1. `/pepper`, fase por fase

| Fase | Núcleo | Qué esperar | Se detiene si |
|---|---|---|---|
| insumos | `pepper detect legacy/` | el perfil que aplica y con qué señales | no hay perfil → `inspector-legacy` redacta un borrador y para (lo revisa una persona) |
| mapa | `pepper map` | `docs/pepper/system-map.json` + `map/`: `surface.md` (rutas, jobs, hosts), `db.md` (tablas, triggers y funciones con cuerpo, redactados), `catalogs.md` (roles, menús por rol, estados, parámetros, distribuciones), `screens.md` (pantallas: controles, botones, mensajes), `code.md` (clases: métodos, constantes, cadenas) | nunca; si falta `javap` o el respaldo no es del formato declarado, sale `INCOMPLETO` y sigue |
| levantar | `pepper rehydrate --up` | red con las IPs del artefacto, base restaurada con el nombre que espera, externos al stub, `isolate` antes de crear, según Docker antes de arrancar, y en vivo con una sonda desde la red interna; `docs/pepper/environment.json` + `validation.md` | `BLOCKED` (falta desplegable, respaldo o configuración con datasource; ambigüedad que la persona resuelve con NOTAS.md, `--config-profile` o `--dump`; un valor del artefacto que no cabe en una plantilla) · `FAILED` (no arrancó, o aislamiento en rojo: el entorno queda detenido) |
| explorar | `pepper explore` | `pepper-out/explore.json` lo escribe el agente desde el mapa (las claves de usuario por rol las resuelve el núcleo dentro del contenedor con `user_sql`); el núcleo entra con cada rol en un Chromium con el resolver cerrado, abre cada pantalla, provoca rechazos, llena y guarda, fotografía; después planes para flujos encadenados; `evidence/explore-*/` | aislamiento en vivo en rojo |
| descubrir | `correlate` → `package` → `descubridor-funcional` → `export` | **`docs/pepper/funcional.md`** (y `discovery/<sid>/`) | el paquete trae datos fuera de lo autorizado (la persona autoriza en su terminal) · Export rechaza (el subagente corrige sobre la evidencia) |

### Cómo validar el entregable

- [ ] La sección 1 se entiende sin conocer el sistema; la 2 dice quién y qué puede hacer cada quien (matriz opción × rol) y coincide con lo que el explorador vio (pantallas que mandan a login por rol).
- [ ] Cada afirmación trae su origen; nada dice `[observado]` que ninguna sesión ejecutó. Export comprueba que cada fuente exista; que la fuente sostenga la afirmación lo compruebas tú al leer.
- [ ] Las reglas se leen como las contaría alguien de la oficina; las que viven en triggers, constantes o parámetros están marcadas como escondidas; las confirmadas por un rechazo provocado citan el mensaje.
- [ ] Los estados traen cuántos registros reales hay en cada uno.
- [ ] La sección 12 separa lo que se pregunta a alguien de lo que se observa; nunca está vacía.
- [ ] Sin nombres de personas, CURP, correos ni contraseñas (el mapa redacta, Export rechaza lo que tiene patrón; un nombre propio lo detectas tú).

## 2. El núcleo a mano

```bash
python3 -m pepper detect legacy/ [--json]                                          # qué perfil aplica, con qué señales
python3 -m pepper map <artefacto> --profile <id> --dump <respaldo> --out docs/pepper/system-map.json [--evidence evidence/<sid>]
python3 -m pepper rehydrate legacy/ --profile <id> [--up] [--port 18080] [--wait 300] [--out pepper-out/rehydrate] [--docs docs/pepper] [--notes legacy/NOTAS.md]
#   [--config-profile <nombre>] [--dump legacy/<archivo>]                           # elecciones humanas cuando hay varios perfiles completos o varios respaldos (quedan registradas)
python3 -m pepper isolate <compose> [--hosts a,b] [--live] [--ingress ingress] [--out reporte.md]   # AISLADO / NO AISLADO / NO VERIFICADO (los dos últimos bloquean)
python3 -m pepper explore <compose> --config pepper-out/explore.json --map <mapa> --session <sid> --profile <id> [--hosts a,b] [--plan plan.json] [--budget s] [--no-submit] [--headed] [--flow-name n] [--settle 12] [--margin 30] [--out evidence] [--ingress ingress]
python3 -m pepper explore <compose> --config pepper-out/explore.json --session <sid> --profile <id> --observe --headed --flow-name "<flujo>"   # una persona opera en el navegador hermético
#   salida: 0 COMPLETO · 3 PARCIAL · 1 FALLIDO · 4 INTERRUMPIDO · 2 insumo inválido (también en session.json → outcome)
python3 -m pepper collect <compose> <sid> --start <ISO> --end <ISO> [--margin 30] [--out evidence] [--ingress ingress]   # la ventana de una persona, a mano
python3 -m pepper correlate evidence/<sid> --out pepper-out/<sid>/correlated [--profile <id>] [--tolerance-ms 500]
python3 -m pepper package pepper-out/<sid>/correlated --legacy legacy/ --map <mapa> --previous docs/pepper/funcional.json --out pepper-out/<sid>/package --data-mode remote [--authorization pepper-out/data-boundary.json] [--manifest-out ruta] [--include-uninspected]
python3 -m pepper authorize pepper-out/<sid>/package.data-boundary.propuesta.json --by "<nombre>" [--out pepper-out/data-boundary.json]   # SOLO la persona, en su terminal; pide escribir AUTORIZO
python3 -m pepper export pepper-out/<sid>/package --manifest pepper-out/<sid>/package.evidence-manifest.json --check
python3 -m pepper export pepper-out/<sid>/package --manifest pepper-out/<sid>/package.evidence-manifest.json --out docs/pepper/discovery/<sid> --system-doc docs/pepper
python3 -m pepper validate <archivo>... [--schema NOMBRE]                           # contratos de schemas/
python3 -m pepper proxy --upstream <host:puerto> [--listen 0.0.0.0:8080] [--out http.jsonl] [--timeout 120]   # el ingress (lo monta el compose; no lo corras en el host con un legacy)
python3 -m pepper demo [--out pepper-out/legacy-demo]                               # el fixture, sin Docker
python3 -m unittest discover -s tests · python3 scripts/verificar.py
```

### `explore.json`

```json
{
  "base_url": "http://127.0.0.1:18080",
  "login": {"route": "/login", "user_field": "#txtNombre", "password_field": "#txtPassword", "submit": "#btnLogin", "failure_text": "Datos erroneos",
            "identity_text": "{user}"},
  "logout_route": "/salir",
  "credentials": {"db_service": "db", "db_user": "postgres", "db_name": "<base>",
                  "setup_sql": "CREATE EXTENSION IF NOT EXISTS pgcrypto",
                  "sql": "UPDATE <usuarios> SET <contrasena> = crypt('{password}', gen_salt('bf', 10)) WHERE <clave> = '{user}'"},
  "roles": [{"name": "ADMIN", "user_sql": "SELECT <clave> FROM <usuarios> u JOIN <usuario_rol> r ON … WHERE r.rol = 'ADMIN' AND u.activo = 1 LIMIT 1", "password": "<prueba>"},
            {"name": "CONSULTAS", "user_sql": "…", "password": "…", "submit": false}],
  "routes": ["/home", "/solicitud"],
  "fill": {"identificador|clave_unica": "<valor con formato válido>", "cp|codigopostal": "<CP del catálogo>", "correo|email": "prueba@pepper.invalid"}
}
```

`roles[].user_sql` (o `user`, si la clave es pública): la consulta corre dentro de la base desechable con el cliente del perfil y solo el navegador ve la clave; el agente escribe la consulta desde `map/db.md` y `catalogs.md`, nunca la clave. `routes` es opcional (default: las rutas GET del mapa). `submit: false` = solo abrir pantallas (matriz de acceso). Las credenciales se fijan **solo** en la base del contenedor. `login.identity_text` es obligatorio: lo que el sistema muestra solo a quien entró (`{user}`, `{role}`; `login.identity_route` si se ve en otra página; un rol puede traer el suyo). Cada rol entra en un contexto de navegador nuevo y sin su identidad en pantalla no se explora.

### Un plan

```json
[
  {"login": "VENTANILLA"}, {"goto": "/solicitud"},
  {"fill": {"#formSolicitud\\:txtIdentificador": "PEPR900101HMCPPR09", "#formSolicitud\\:txtNombre": "Prueba"}},
  {"select": {"formSolicitud:cboTipo": "Cambio de domicilio"}},
  {"click": "Guardar", "efecto": "modifica", "id": "registrar-solicitud"},
  {"expect_text": "exitosamente", "comprueba": "registrar-solicitud"},
  {"logout": true}, {"login": "SUPERVISOR"}, {"goto": "/bandeja"},
  {"expect_text": "PEPR900101HMCPPR09", "comprueba": "registrar-solicitud"},
  {"click_at": "#j_idt42", "efecto": "modifica", "id": "atender"},
  {"expect_text": "Atendida", "comprueba": "atender"},
  {"click": "Generar reporte", "efecto": "consulta", "porque": "descarga un PDF; no guarda nada"},
  {"wait": 70}, {"note": "esperar al job de cada minuto"}
]
```

Acciones (una por paso): `login`, `goto`, `fill` (selector → valor), `select` (id del selectOneMenu → texto), `click` (texto del botón), `click_at` (selector), `check`, `wait` (s), `note`, `logout`, y las comprobaciones `expect_text`, `expect_absent`, `expect_route`, `expect_rejected` (texto del rechazo, o `true`).

Junto a la acción, el paso **declara** qué hace — sin depender del texto del botón ni del selector:

- `efecto`: `"modifica"` o `"consulta"`. Obligatorio en `click` y `click_at`; opcional en `goto`, `fill`, `select`, `check` (un GET que borra o un combo que guarda al cambiar también se declaran).
- `id`: nombre del paso. Obligatorio en lo que modifica.
- `comprueba: <id>`: en una comprobación, qué acción comprueba. Todo lo que modifica necesita al menos una comprobación posterior que lo nombre — en la misma pantalla o después, con otro rol. `expect_rejected` siempre dice qué rechazo esperaba.
- `porque`: obligatorio si un clic cuyo texto suele guardar (guardar, registrar, generar…) se declara `"consulta"`.

Sin eso el plan no corre. Cada paso queda en `explore.jsonl` con resultado, mensajes, captura, `detail.paso`, `detail.tipo`, lo declarado y, si falló, `detail.falla`: `explorador`, `negocio`, `verificacion`, `acceso`, `identidad`, `sistema`, `sin_comprobar` o `declaracion` (un clic declarado `"consulta"` tras el cual apareció un mensaje de que se guardó algo). Un rechazo que su `expect_rejected` confirma es resultado de negocio, no falla. Veredicto: **COMPLETO** (todo corrió, sin fallas, y cada acción que modifica tiene su propia comprobación cumplida), **PARCIAL** (alguna comprobada, alguna falla u omisión), **FALLIDO** (ninguna comprobación cumplida, o ninguna de las acciones que modifican quedó comprobada), **INTERRUMPIDO** (pasos sin correr: presupuesto, error, Ctrl+C). Comprobaciones de otras pantallas no cuentan por un guardado.

### Lo que el agente puede leer

El agente que orquesta es un modelo remoto. El guardia de datos (`scripts/guardia_datos.py`, hook `PreToolUse` de Claude Code declarado en `.claude/settings.json`) bloquea: `legacy/` (salvo listar y leer con `Read` un texto chico que el escáner declare limpio), `evidence/*/{screens,http.jsonl,containers,raw}`, `pepper-out/*/correlated`, `pepper-out/rehydrate/.env`, el compose y el restore rendidos (se pasan como argumento, no se abren), `docker … exec|logs db`, `docker cp`, los clientes de base (`psql`, `mysql`, `sqlcmd`…), y escribir en `legacy/` o en la autorización. Lo que sí lee: `docs/pepper/**`, `evidence/*/explore.jsonl`, `evidence/*/session.json`, `pepper-out/*/package/**`. Fail-closed: un error interno bloquea. Quien desarrolla PEPPER lo desactiva para editar el guardia con `PEPPER_GUARDIA_DEV=1`.

## 3. Glosario

- **Mapa del sistema**: lo que el sistema ES, sacado del artefacto y del respaldo: rutas, jobs, pantallas, clases, tablas, catálogos, triggers, distribuciones. Redactado: columnas de personas y credenciales por nombre (español e inglés) y por patrón de valor; cuerpos de funciones con credenciales tachadas.
- **Rehydrate**: reconstruir el legacy en contenedores desechables, fiel al original, con la red que el artefacto espera y sin salida. Estados `READY` / `PARTIAL` / `BLOCKED` / `FAILED`.
- **Ingress**: el proxy de PEPPER; único puerto publicado (loopback); inyecta `correlation_id`, emite `http.jsonl`, aísla al navegador (CSP con `webrtc 'block'`, guardián, `Location` y `meta refresh` reescritos).
- **Stub**: a donde se resuelve todo host externo del artefacto; registra toda conexión (HTTP, TLS, protocolos donde el servidor habla primero) y la cierra.
- **Sonda de salida**: un contenedor efímero en la misma red interna que intenta salir a internet y al host; si sale, NO AISLADO. Corre en `isolate --live`.
- **Explorador**: el Chromium local, con el resolver cerrado (solo el host del ingress) y sin service workers, que recorre el sistema por el ingress con cada rol; deja `explore.jsonl` y capturas. Con `--observe --headed`, el mismo navegador para que una persona opere.
- **Guardia de datos**: el hook que acota lo que el agente puede leer y escribir en el workspace.
- **Ventana / sesión**: el intervalo que se captura (`evidence/<sid>/`), del explorador o de una persona.
- **Documento funcional** (`funcional.md/json`): el entregable — qué hace el sistema, 12 secciones fijas, cada afirmación con su origen; del sistema, acumulado sesión a sesión.
- **Origen**: `observado` (se vio ejecutar), `en_codigo`, `en_base`, `en_datos`, `en_config`, `en_doc`, `humano`. **Confianza**: `confirmada` (observado + código/base), `sustentada`, `inferida`, `contradicha`, `desconocida`.
- **Perfil**: todo el conocimiento de un sistema concreto como datos (`profiles/<id>/`): detección, extractores del mapa, receta de rehydrate, parsers, lectura de formularios, fixtures. `draft` o `validated`. Parametriza lectores de formatos que el núcleo ya trae.
- **Escalón**: 1 = hay perfil (`draft` o `validated`; el estado se declara en la salida) → todo automático; 2 = sin perfil pero el sistema corre en otro lado → `/pepper-observe` con colectores genéricos; 3 = ni corre → borrador de perfil y `BLOCKED`.
- **Autorización de datos**: `pepper-out/data-boundary.json`, escrita por `pepper authorize` desde la terminal de una persona; dice qué sistema, qué destino y qué categorías; la llave de seudónimos es por sistema y nunca viaja.
- **Procedencia (stark)**: `confirmada` / `inferida` / `en-duda`. PEPPER entrega como máximo `inferida`; solo una persona con nombre confirma.
