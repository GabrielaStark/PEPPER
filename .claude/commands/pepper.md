---
description: PEPPER completo - dale un binario y un respaldo en legacy/ y entrega docs/pepper/funcional.md (qué hace el sistema, quién lo usa, qué puede hacer cada rol, reglas, estados, lo automático, integraciones, volúmenes y lo que no se sabe). Levanta el sistema aislado, lo recorre solo y escribe el documento. Para solo si el aislamiento no está en verde o falta un insumo.
argument-hint: "[desde: mapa | levantar | explorar | descubrir]"
---

Lee `docs/documentacion/PRINCIPIOS.md` y `.claude/skills/evidencia-runtime/SKILL.md`: reglas duras.

Esto es un solo comando porque el humano no conoce el sistema y no va a operarlo ni a narrarlo. Tú corres el núcleo fase por fase, sin pedir confirmaciones intermedias; reportas en tres líneas al terminar cada una; **solo te detienes** si el aislamiento no está en verde (fail-closed) o si falta un insumo (BLOCKED, con la lista de qué conseguir). Nada del legacy sale de la máquina: los contenedores no tienen salida, el navegador del explorador solo habla con `127.0.0.1`, y jamás se resuelve ni se contacta un host o IP del artefacto desde fuera de esa red.

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

El núcleo lee la configuración embebida del artefacto, fabrica la red con las IPs que el artefacto espera, restaura el respaldo dentro de la base que espera, manda todo host externo al stub, verifica el aislamiento antes de levantar y en vivo después, y escribe `docs/pepper/environment.json` + `validation.md`. `READY` o `PARTIAL` (externos stubeados) → sigue. `BLOCKED`/`FAILED` → detente con lo que dice el reporte; no improvises un compose a mano.

## 3. Explorar — el sistema se recorre solo

Escribe `docs/pepper/explore.json` a partir del mapa — es lo único que requiere criterio:

- `login`: la pantalla con un control `password#…` en `screens.md`; sus selectores (`#id` si el formulario tiene `prependId=false`, si no `#form\:id`), el botón, y el texto del mensaje de rechazo (en `catalogs.md` suele ser un parámetro).
- `roles`: un usuario ACTIVO por rol activo, sacado de los catálogos (tabla de roles y relación usuario-rol del mapa; si el mapa no vuelca la tabla de usuarios por ser de personas, consúltala en la base desechable con `docker compose exec db psql` — solo la clave de usuario, nunca nombres). Contraseña de prueba única. `submit: false` en los roles de solo consulta.
- `credentials.sql`: cómo fijar la contraseña en la base desechable (columna, algoritmo: el código dice qué encoder usa — `code.md`, clase de login). Solo en el contenedor; jamás en un artefacto ni en un ambiente real. Y `credentials.setup_sql`: lo que ese SQL necesite y una base recién restaurada no trae — con bcrypt en PostgreSQL es `CREATE EXTENSION IF NOT EXISTS pgcrypto` (da `crypt()` y `gen_salt()`). Corre una vez, antes de los roles; si falla, el explorador se detiene.
- `fill`: pistas de valores plausibles por campo (CURP con formato válido, un CP que exista en el catálogo, correos `@pepper.invalid`).

Luego:

```bash
python3 -m pepper explore pepper-out/rehydrate/docker-compose.yml --config docs/pepper/explore.json --map docs/pepper/system-map.json --session explore-001 --profile <id> --hosts "<hosts externos>"
```

Entra con cada rol, abre cada pantalla, intenta guardar con todo vacío (rechazos), llena con valores plausibles y guarda, fotografía, y anota qué pantallas mandan a login por rol. Deja `evidence/explore-001/` con `explore.jsonl`, `screens/`, `session.json` y lo capturado del ingress y los contenedores.

Después lee `explore.jsonl`: lo que quedó en `error` o sin un guardado exitoso en las pantallas que importan (el recorrido principal: registrar → turno → atender → calificar) lo cubres con **planes** — pasos encadenados que escribes tú con los ids de `screens.md` y valores coherentes (un CP del catálogo con sus colonias, un motivo del catálogo, la secuencia de roles): `python3 -m pepper explore … --session explore-002 --plan pepper-out/planes/recorrido-principal.json`. Formato del plan en `pepper/explore.py` (`run_plan`). Un plan por recorrido; los rechazos y los efectos en la base son el resultado, no un fallo.

## 4. Correlacionar y empaquetar

Por cada sesión (`explore-001`, `explore-002`, …):

```bash
python3 -m pepper correlate evidence/<sid> --out pepper-out/<sid>/correlated
python3 -m pepper package pepper-out/<sid>/correlated --legacy legacy/ --map docs/pepper/system-map.json --previous docs/pepper/funcional.json --out pepper-out/<sid>/package --data-mode remote --allow-sensitive --acknowledge-unscanned
```

`--previous` solo cuando ya existe `docs/pepper/funcional.json`. Las banderas de datos las autorizó el humano al elegir modelo remoto para este legacy (D24); si no lo ha hecho, pregúntaselo una vez y no sigas sin respuesta.

## 5. Descubrir — qué hace el sistema

Use the descubridor-funcional subagent to produce `pepper-out/<sid>/package/output/funcional.json` and `funcional.md`. Después:

```bash
python3 -m pepper export pepper-out/<sid>/package --manifest pepper-out/<sid>/package.evidence-manifest.json --out docs/pepper/discovery/<sid> --system-doc docs/pepper
```

Si Export rechaza, el subagente corrige sobre la evidencia; tú no editas la salida.

## 6. Entrega

`docs/pepper/funcional.md` es el entregable. Cierra con: las tres cosas que más cambian cómo se entiende el sistema, cuántos recorridos quedaron observados vs solo en código, y la lista de la sección 12 separada en **lo que se resuelve preguntándole a alguien** y **lo que se resuelve con otra ventana** (`/pepper explorar` con un plan, o `/pepper-observe <flujo>` si hay quien lo opere). Recuerda apagar: `docker compose -f pepper-out/rehydrate/docker-compose.yml down -v`.
