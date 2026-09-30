---
description: PEPPER completo - dale un binario y un respaldo en legacy/ y entrega docs/pepper/funcional.md (qué hace el sistema, quién lo usa, qué puede hacer cada rol, reglas, estados, lo automático, integraciones, volúmenes y lo que no se sabe). Levanta el sistema aislado, lo recorre solo y escribe el documento. Para solo si el aislamiento no está en verde, falta un insumo, o una persona tiene que autorizar datos.
argument-hint: "[desde: mapa | levantar | explorar | descubrir]"
---

Lee `docs/documentacion/PRINCIPIOS.md` y `.claude/skills/evidencia-runtime/SKILL.md`: reglas duras.

Esto es un solo comando porque el humano no conoce el sistema y no va a operarlo ni a narrarlo. Tú corres el núcleo fase por fase, sin pedir confirmaciones intermedias; reportas en tres líneas al terminar cada una; **solo te detienes** si el aislamiento no está en verde (fail-closed), si falta un insumo (BLOCKED, con la lista de qué conseguir), o si el paquete necesita una autorización de datos que solo una persona puede dar.

**Tú eres un modelo remoto: lo que lees, sale de la máquina.** Un guardia (`scripts/guardia_datos.py`, hook de Claude Code) te impide abrir el respaldo, el desplegable, la evidencia cruda (`evidence/*/http.jsonl`, `screens/`, `containers/`), lo correlacionado antes de sustituir, el `.env` y el compose rendidos, la base desechable (`docker … exec db`, `psql`, `mysql`) y la autorización de datos. No lo rodees: lo que necesitas de ahí lo saca el núcleo, redactado. Lo que sí lees está declarado en `docs/documentacion/THREAT-MODEL.md`.

`$ARGUMENTS` puede decir desde qué fase retomar (`mapa`, `levantar`, `explorar`, `descubrir`); sin argumento se hace todo.

## 0. Insumos

`legacy/` con el desplegable, el respaldo y `legacy/NOTAS.md` (si no existe, cópialo de `templates/NOTAS-LEGACY.md` y sigue: es opcional, pero una línea con el servidor de producción ahorra una desviación). `python3 -m pepper detect legacy/` dice qué perfil aplica. Sin perfil aplicable: escalón 3 — usa el subagente `inspector-legacy` para redactar el borrador (`profiles/<id>/`, `status: draft`) y detente ahí con el reporte: un perfil nuevo lo revisa una persona antes de correr.

## 1. Mapa — lo que el sistema ES

```bash
python3 -m pepper map legacy/<artefacto> --profile <id> --dump legacy/<respaldo> --out docs/pepper/system-map.json
```

Lee `docs/pepper/map/catalogs.md` (roles, menús por rol, estados, parámetros), `screens.md` (pantallas con controles, botones y mensajes), `db.md` (triggers y funciones), `code.md`. Si sale `INCOMPLETO`, di qué faltó y sigue. Reporta: cuántas pantallas, roles, tablas, reglas en la base.

## 2. Levantar — aislado o nada

```bash
python3 -m pepper rehydrate legacy/ --profile <id> --up
```

El núcleo lee la configuración embebida del artefacto, fabrica la red con las IPs que el artefacto espera, restaura el respaldo dentro de la base que espera, manda todo host externo al stub, verifica el aislamiento antes de crear, según Docker antes de arrancar, y en vivo después (con una sonda desde la red interna que intenta salir y tiene que fracasar), y escribe `docs/pepper/environment.json` + `validation.md`. `READY` o `PARTIAL` (externos stubeados) → sigue. `BLOCKED`/`FAILED` → detente con lo que dice el reporte; no improvises un compose a mano. Para diagnosticar un `FAILED`: `docker compose -f pepper-out/rehydrate/docker-compose.yml logs app` (el entorno queda detenido, con sus logs).

## 3. Explorar — el sistema se recorre solo

Escribe `pepper-out/explore.json` a partir del mapa — es lo único que requiere criterio. Va en `pepper-out/`, **nunca en `docs/pepper/`**: trae la contraseña de prueba, y `docs/pepper/` es producto que se versiona.

- `login`: la pantalla con un control `password#…` en `screens.md`; sus selectores (`#id` si el formulario tiene `prependId=false`, si no `#form\:id`), el botón, y el texto del mensaje de rechazo (en `catalogs.md` suele ser un parámetro). Y `login.identity_text`: lo que el sistema muestra SOLO a quien entró (p. ej. `"{user}"` si el encabezado muestra la clave; `{role}` también vale; `login.identity_route` si se ve en otra página; un rol puede traer su propio `identity_text`). Es obligatorio: cada rol entra en un navegador limpio y, si su identidad no aparece, ese rol no se explora.
- `roles`: un rol por cada rol activo del catálogo. **La clave de usuario no la ves tú**: en vez de `user`, escribe `user_sql` con la consulta que, en la base desechable, devuelve UNA clave de un usuario activo de ese rol (las tablas y columnas están en `map/db.md` y `catalogs.md`: la tabla de usuarios, la relación usuario-rol, la columna de activo). El núcleo la corre dentro del contenedor y solo el navegador ve el valor. Contraseña de prueba única. `submit: false` en los roles de solo consulta.
- `credentials.sql`: cómo fijar la contraseña en la base desechable (columna, algoritmo: el código dice qué encoder usa — `code.md`, clase de login). Solo en el contenedor; jamás en un artefacto ni en un ambiente real. Y `credentials.setup_sql`: lo que ese SQL necesite y una base recién restaurada no trae — con bcrypt en PostgreSQL es `CREATE EXTENSION IF NOT EXISTS pgcrypto` (da `crypt()` y `gen_salt()`). Corre una vez, antes de los roles; si falla, el explorador se detiene.
- `fill`: pistas de valores plausibles por campo (un identificador con formato válido, un código postal que exista en el catálogo, correos `@pepper.invalid`).
- Obligatorios además: `base_url` (`http://127.0.0.1:<puerto del ingress>`, el de `environment.json`), `logout_route`, `credentials.db_name` (la base que el artefacto espera), `credentials.db_user` (el usuario del datasource: el contenedor NO tiene otro), `credentials.db_service` (`db`). `pepper explore` valida el archivo antes de tocar nada y dice qué falta.
- El resultado va en `session.json` (`outcome`) y en el código de salida: **0 COMPLETO** (todo se hizo y se comprobó) · **3 PARCIAL** (hay resultado comprobado y también fallas u omisiones: la evidencia sirve, el recorrido no se describe como completo) · **1 FALLIDO** (nada comprobado: corrige y repite) · **4 INTERRUMPIDO** (quedó trabajo sin correr: Ctrl+C, un error del navegador o `--budget` vencido) · 2 insumo inválido. Con 1 o 4 la sesión queda escrita igual: repite con otro `--session`; no hace falta bajar el entorno.

Luego:

```bash
python3 -m pepper explore pepper-out/rehydrate/docker-compose.yml --config pepper-out/explore.json --map docs/pepper/system-map.json --session explore-001 --profile <id> --hosts "<hosts externos>" --budget 3600
```

Entra con cada rol, abre cada pantalla, intenta guardar con todo vacío (rechazos), llena con valores plausibles y guarda, fotografía, y anota qué pantallas mandan a login por rol. Deja `evidence/explore-001/` con `explore.jsonl`, `screens/`, `session.json` y lo capturado del ingress y los contenedores. De ahí tú lees **solo** `explore.jsonl` y `session.json`; las capturas y `http.jsonl` no (el guardia lo impide, y no viajan en el paquete).

Después lee `explore.jsonl`: lo que quedó en `error` o sin un guardado exitoso en las pantallas que importan (el recorrido principal: lo que el sistema existe para hacer, de la primera pantalla a la última) lo cubres con **planes** — pasos encadenados que escribes tú con los ids de `screens.md` y valores coherentes (valores que existan en los catálogos, en el orden en que las pantallas los piden, con la secuencia de roles del recorrido): `python3 -m pepper explore … --session explore-002 --plan pepper-out/planes/recorrido-principal.json`. Formato del plan en `docs/documentacion/REFERENCIA.md`. Un plan por recorrido. **Cada clic declara su efecto** (`"efecto": "modifica"` o `"consulta"`), y **todo lo que modifica lleva `id` y al menos una comprobación posterior que lo nombra** (`"comprueba": "<id>"`): `expect_text` / `expect_route` con lo que el sistema muestra al terminar el trámite — mejor aún, el registro visto desde otra pantalla u otro rol — o `expect_rejected: "<mensaje>"` si lo que se prueba es un rechazo de negocio. Se declara por lo que hace el paso, no por el texto del botón: un `click_at: "#j_idt42"` que guarda es `modifica`; un "Generar reporte" que solo descarga es `consulta` con `"porque"`. `explore` no corre un plan sin eso. Un rechazo confirmado por su `expect_rejected` es resultado de negocio; uno no declarado cuenta como falla (`detail.falla`: `negocio`, distinta de `explorador`, `verificacion`, `sin_comprobar`, `declaracion`, `acceso`, `identidad`). Un plan PARCIAL o FALLIDO se corrige con otro plan, no se reinterpreta.

## 4. Correlacionar y empaquetar

Por cada sesión (`explore-001`, `explore-002`, …):

```bash
python3 -m pepper correlate evidence/<sid> --out pepper-out/<sid>/correlated
python3 -m pepper package pepper-out/<sid>/correlated --legacy legacy/ --map docs/pepper/system-map.json --previous docs/pepper/funcional.json --out pepper-out/<sid>/package --data-mode remote
```

`--previous` solo cuando ya existe `docs/pepper/funcional.json`.

Lo que el escáner no puede leer (el respaldo, el desplegable, un log enorme) **no viaja**: el paquete lo lista en su README bajo "No viaja" y el mapa ya lo leyó. No uses `--include-uninspected`: si de verdad hiciera falta, lo pide y lo autoriza una persona por archivo.

**La decisión de datos es de una persona y tiene alcance (D24, D37).** Si existe `pepper-out/data-boundary.json`, pásalo siempre: `--authorization pepper-out/data-boundary.json`. `package` en modo `remote` compara lo que el paquete trae con lo autorizado — el sistema (perfil y huella del legacy), el destino y las categorías de datos detectadas — y si algo no cabe **no arma nada**: lista las ubicaciones (nunca los valores) y deja `pepper-out/<sid>/package.data-boundary.propuesta.json`. Entonces:

1. Muéstrale a la persona qué pide la propuesta (categorías y el porqué en `why`) y dile: «este paquete va a un modelo remoto con esto adentro — las credenciales se quitan y los datos de personas con patrón viajan con seudónimo, pero lo que no tiene patrón (un nombre, un domicilio, un teléfono) no se detecta».
2. **Ella lo autoriza en SU terminal**, no tú: `python3 -m pepper authorize pepper-out/<sid>/package.data-boundary.propuesta.json --by "<su nombre>"`. El comando se niega a correr sin una terminal interactiva y sin la palabra AUTORIZO escrita a mano; el guardia te impide escribir esa autorización. Cuando te diga que lo hizo, repite `package` con `--authorization`.
3. Sin respuesta, no sigas. Solo vuelve a pedirlo si una sesión trae algo fuera de lo ya autorizado (otra categoría, otra versión del legacy).

Lo que viaja: credenciales como `[CREDENCIAL]`; CURP, RFC, correo, CLABE y tarjeta como seudónimo estable (`[CURP-…]`, el mismo valor da el mismo seudónimo en todo el paquete y en las sesiones siguientes de este sistema); keystores y llaves privadas nunca; el nombre de quien autorizó tampoco.

## 5. Descubrir — qué hace el sistema

Use the descubridor-funcional subagent to produce `pepper-out/<sid>/package/output/funcional.json` and `funcional.md`. Después:

```bash
python3 -m pepper export pepper-out/<sid>/package --manifest pepper-out/<sid>/package.evidence-manifest.json --out docs/pepper/discovery/<sid> --system-doc docs/pepper
```

Si Export rechaza (una fuente que no resuelve, una sección que falta, o un dato de persona o una credencial en la salida), el subagente corrige sobre la evidencia; tú no editas la salida.

## 6. Entrega

`docs/pepper/funcional.md` es el entregable. Cópialo también a `docs/analysis/funcional.md` (`mkdir -p docs/analysis && cp docs/pepper/funcional.md docs/analysis/`), por si el conocimiento sigue hacia stark. Cierra con: las tres cosas que más cambian cómo se entiende el sistema, cuántos recorridos quedaron observados vs solo en código, y la lista de la sección 12 separada en **lo que se resuelve preguntándole a alguien** y **lo que se resuelve con otra ventana** (`/pepper explorar` con un plan, o `/pepper-observe <flujo>` si hay quien lo opere). Recuerda apagar: `docker compose -f pepper-out/rehydrate/docker-compose.yml down -v`.
