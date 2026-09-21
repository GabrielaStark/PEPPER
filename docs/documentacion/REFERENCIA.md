# Referencia de PEPPER

> Para el camino feliz, [`QUICKSTART.md`](QUICKSTART.md). Aquí: qué hace cada comando del núcleo, qué esperar de `/pepper` en cada fase y cómo validar lo que entrega.

## 1. `/pepper`, fase por fase

| Fase | Núcleo | Qué esperar | Se detiene si |
|---|---|---|---|
| insumos | `pepper detect legacy/` | el perfil que aplica y con qué señales | no hay perfil → `inspector-legacy` redacta un borrador y para (lo revisa una persona) |
| mapa | `pepper map` | `docs/pepper/system-map.json` + `map/`: `surface.md` (rutas, jobs, hosts), `db.md` (tablas, triggers y funciones con cuerpo), `catalogs.md` (roles, menús por rol, estados, parámetros, distribuciones), `screens.md` (pantallas: controles, botones, mensajes), `code.md` (clases: métodos, constantes, cadenas) | nunca; si falta `javap` o el respaldo no es custom, sale `INCOMPLETO` y sigue |
| levantar | `pepper rehydrate --up` | red con las IPs del artefacto, base restaurada con el nombre que espera, externos al stub, `isolate` antes y en vivo, `docs/pepper/environment.json` + `validation.md` | `BLOCKED` (falta desplegable, respaldo o configuración con datasource; o hay ambigüedad —varios perfiles completos, varios respaldos, versión del servidor sin declarar— que la persona resuelve con NOTAS.md, `--config-profile` o `--dump`) · `FAILED` (no arrancó, o aislamiento en rojo) |
| explorar | `pepper explore` | `docs/pepper/explore.json` lo escribe el agente desde el mapa; el núcleo entra con cada rol, abre cada pantalla, provoca rechazos, llena y guarda, fotografía; después planes para flujos encadenados; `evidence/explore-*/` | aislamiento en vivo en rojo |
| descubrir | `correlate` → `package` → `descubridor-funcional` → `export` | **`docs/pepper/funcional.md`** (y `discovery/<sid>/`) | Export rechaza (el subagente corrige sobre la evidencia) |

### Cómo validar el entregable

- [ ] La sección 1 se entiende sin conocer el sistema; la 2 dice quién y qué puede hacer cada quien (matriz opción × rol) y coincide con lo que el explorador vio (pantallas que mandan a login por rol).
- [ ] Cada afirmación trae su origen; nada dice `[observado]` que ninguna sesión ejecutó.
- [ ] Las reglas se leen como las contaría alguien de la oficina; las que viven en triggers, constantes o parámetros están marcadas como escondidas; las confirmadas por un rechazo provocado citan el mensaje.
- [ ] Los estados traen cuántos registros reales hay en cada uno.
- [ ] La sección 12 separa lo que se pregunta a alguien de lo que se observa; nunca está vacía.
- [ ] Sin nombres de personas, CURP, correos ni contraseñas (el mapa redacta; el documento no debe reintroducirlos).

## 2. El núcleo a mano

```bash
python3 -m pepper detect legacy/                                                  # qué perfil aplica, con qué señales
python3 -m pepper map <artefacto> --profile <id> --dump <respaldo> --out docs/pepper/system-map.json
python3 -m pepper rehydrate legacy/ --profile <id> [--up] [--port 18080]           # plan (compose, restore.sh, .env) y, con --up, el entorno corriendo
#   [--config-profile <nombre>] [--dump legacy/<archivo>]                           # elecciones humanas cuando hay varios perfiles completos o varios respaldos (quedan registradas)
python3 -m pepper isolate <compose> [--hosts a,b] [--live]                         # AISLADO / NO AISLADO / NO VERIFICADO (los dos últimos bloquean)
python3 -m pepper explore <compose> --config docs/pepper/explore.json --map <mapa> --session <sid> --profile <id> [--plan plan.json] [--no-submit] [--headed]
python3 -m pepper collect <compose> <sid> --start <ISO> --end <ISO>                # la ventana de una persona
python3 -m pepper correlate evidence/<sid> --out pepper-out/<sid>/correlated [--profile <id>]
python3 -m pepper package pepper-out/<sid>/correlated --legacy legacy/ --map <mapa> --previous docs/pepper/funcional.json --out pepper-out/<sid>/package --data-mode remote
python3 -m pepper export pepper-out/<sid>/package --manifest pepper-out/<sid>/package.evidence-manifest.json --check
python3 -m pepper export … --out docs/pepper/discovery/<sid> --system-doc docs/pepper
python3 -m pepper validate <archivo>... [--schema NOMBRE]                           # contratos de schemas/
python3 -m pepper proxy --upstream <host:puerto>                                    # el ingress (lo monta el compose)
python3 -m pepper demo                                                              # el fixture, sin Docker
python3 -m unittest discover -s tests · python3 scripts/verificar.py
```

### `explore.json`

```json
{
  "base_url": "http://127.0.0.1:18080",
  "login": {"route": "/login", "user_field": "#txtNombre", "password_field": "#txtPassword", "submit": "#btnLogin", "failure_text": "Datos erroneos"},
  "logout_route": "/salir",
  "credentials": {"db_service": "db", "db_user": "postgres", "db_name": "<base>", "sql": "UPDATE <usuarios> SET <contrasena> = crypt('{password}', gen_salt('bf', 10)) WHERE <clave> = '{user}'"},
  "roles": [{"name": "ADMIN", "user": "<clave>", "password": "<prueba>"}, {"name": "CONSULTAS", "user": "…", "password": "…", "submit": false}],
  "routes": ["/home", "/cita"],
  "fill": {"curp": "<CURP con formato válido>", "cp|codigopostal": "<CP del catálogo>", "correo|email": "prueba@pepper.invalid"}
}
```

`routes` es opcional (default: las rutas GET del mapa). `submit: false` = solo abrir pantallas (matriz de acceso). Las credenciales se fijan **solo** en la base del contenedor.

### Un plan

```json
[
  {"login": "RECEPCION"}, {"goto": "/cita"},
  {"fill": {"#formCita\\:txtCurp": "PEPR900101HMCPPR09", "#formCita\\:txtNombre": "Prueba"}},
  {"select": {"formCita:cboMotivo": "Despido"}},
  {"click": "Guardar"}, {"expect_text": "exitosamente"},
  {"logout": true}, {"login": "PROCURADOR"}, {"goto": "/procurador"}, {"click": "Recibir turnos"},
  {"wait": 70}, {"note": "esperar al job de cada minuto"}
]
```

Pasos: `login`, `goto`, `fill` (selector → valor), `select` (id del selectOneMenu → texto), `click` (texto del botón), `check`, `wait` (s), `expect_text`, `expect_route`, `note`, `logout`. Cada paso queda en `explore.jsonl` con resultado, mensajes y captura.

## 3. Glosario

- **Mapa del sistema**: lo que el sistema ES, sacado del artefacto y del respaldo: rutas, jobs, pantallas, clases, tablas, catálogos, triggers, distribuciones.
- **Rehydrate**: reconstruir el legacy en contenedores desechables, fiel al original, con la red que el artefacto espera y sin salida. Estados `READY` / `PARTIAL` / `BLOCKED` / `FAILED`.
- **Ingress**: el proxy de PEPPER; único puerto publicado (loopback); inyecta `correlation_id`, emite `http.jsonl`, aísla al navegador (CSP + guardián).
- **Stub**: a donde se resuelve todo host externo del artefacto; responde error y registra.
- **Explorador**: el navegador headless local que recorre el sistema por el ingress con cada rol; deja `explore.jsonl` y capturas.
- **Ventana / sesión**: el intervalo que se captura (`evidence/<sid>/`), del explorador o de una persona.
- **Documento funcional** (`funcional.md/json`): el entregable — qué hace el sistema, 12 secciones fijas, cada afirmación con su origen; del sistema, acumulado sesión a sesión.
- **Origen**: `observado` (se vio ejecutar), `en_codigo`, `en_base`, `en_datos`, `en_config`, `en_doc`, `humano`. **Confianza**: `confirmada` (observado + código/base), `sustentada`, `inferida`, `contradicha`, `desconocida`.
- **Perfil**: todo el conocimiento de un stack como datos (`profiles/<id>/`): detección, extractores del mapa, receta de rehydrate, colectores, parsers, lectura de formularios. `draft` o `validated`.
- **Escalón**: 1 = hay perfil (`draft` o `validated`; el estado se declara en la salida) → todo automático; 2 = sin perfil pero el sistema corre en otro lado → `/pepper-observe` con colectores genéricos; 3 = ni corre → borrador de perfil y `BLOCKED`.
- **Procedencia (stark)**: `confirmada` / `inferida` / `en-duda`. PEPPER entrega como máximo `inferida`; solo una persona con nombre confirma.
