# Clave de respuestas del fixture

Qué debe encontrar PEPPER al procesar este legacy. Sirve como criterio de aceptación del pipeline: no basta con que produzca *algo*, tiene que producir **esto**.

`runtime-discovery.json` de esta carpeta es la salida de referencia. Está escrita a mano (`engine.agent: "golden-fixture"`), valida contra `schemas/runtime-discovery.schema.json`, todas sus `raw_ref` apuntan a líneas reales de `raw-evidence/` y sus `event_id` son los que `pepper correlate` asigna al procesar el fixture. `pepper export` la acepta tal cual (es uno de los tests).

## Las tres trampas

### 1. Regla de negocio escondida — debe encontrarla

El sistema exige que el ciudadano esté en estado `ACTIVE` para registrar una solicitud. Un ciudadano `SUSPENDED` se rechaza con HTTP 409 **antes de cualquier escritura**.

No está documentada en ningún lado; al contrario, el manual dice que el campo es estadístico. La ventana observada la prueba dos veces: el intento rechazado (ciudadano 1003) y el exitoso (ciudadano 1001).

→ Debe aparecer como regla candidata con confianza alta (`fuertemente_sustentada`), con evidencia del `SELECT` de `citizen`, del WARN de rechazo y del 409, y con `code_refs` a `ApplicationService.java:35-38`.

### 2. Contradicción — debe reportarla, no ignorarla

`manual-tecnico.md` §3 paso 5 afirma que se envía un correo de confirmación al ciudadano, y `application.properties` tiene `notificaciones.habilitado=true`. En la ejecución **no hay rastro de envío de correo**: el flujo pasa del INSERT de historial directo al HTTP 201. En el código, `NotificationService` está inyectado en `ApplicationService` pero `sendRegistrationEmail` nunca se invoca — código muerto.

→ Debe aparecer en `contradictions`, **nunca** como regla candidata ni como dependencia observada. El error clásico sería listar el SMTP como dependencia por leerlo en la configuración: no hay evidencia de ejecución que lo respalde.

La segunda contradicción es más sutil: el manual §4 dice que el estado del ciudadano se usa "con fines estadísticos", cuando en realidad condiciona el registro. Es la documentación contradiciendo a la regla #1.

### 3. Desconocido — debe declararlo, no inventarlo

`ApplicationService.java:40-42` bifurca a `processForeignApplication` cuando la nacionalidad no es `MX` (agrega un historial `PENDING_CONSULAR_REVIEW`). El flujo observado nunca ejercitó esa rama: ambos intentos fueron de ciudadanos mexicanos.

→ Debe aparecer en `unknowns` con la recomendación de observar una ejecución con el ciudadano 1005 (BR). **No** debe describirse como comportamiento observado: el código prueba que la rama existe, no que se ejecutó.

Lo mismo aplica al camino de ciudadano inexistente (404), que tampoco se ejercitó.

## Ruido que debe reducirse

`raw-evidence/` incluye ruido deliberado que `correlate` debe descartar (y auditar en `reduction.md`):

- Sondeo `GET /health` cada 15 segundos (proxy y Undertow).
- Validación periódica del pool de conexiones (`server.log`).
- `SELECT 1` de validación de conexión (`postgresql.log`).

De 14 líneas de `http.jsonl`, 18 de `server.log` y 14 de `postgresql.log` (46 en total), `pepper correlate` conserva **17 eventos**: los 14 que la salida de referencia cita como evidencia, más las dos líneas de Undertow que confirman el status de cada respuesta y el mensaje de arranque del datasource (que queda *sin asignar* a ninguna petición, no descartado). Se descartan 25: 15 sondeos de salud, 5 `SELECT 1`, 5 validaciones de pool. **Nunca** debe descartarse el WARN de rechazo ni ninguna escritura a base de datos.

Las 4 líneas `DETAIL` de PostgreSQL no cuentan como eventos: se fusionan en la sentencia a la que pertenecen, como `metadata.parameters`.

## Qué ejercita cada parte del pipeline

| Aspecto | Cómo lo prueba el fixture |
|---|---|
| Normalización | tres formatos distintos: JSONL estructurado, log de WildFly (hora local, milisegundos con coma, sin offset) y log de PostgreSQL (parámetros en línea `DETAIL` aparte) |
| Correlación | dos peticiones en la misma ventana que **no deben mezclarse**; `correlation_id` del proxy en los extremos HTTP, y `thread` / `pid` como respaldo en el medio |
| Reducción | 46 líneas crudas → 17 eventos conservados, 25 descartados con auditoría |
| Trazabilidad | toda conclusión resuelve a `archivo:línea` de `raw-evidence/` |
| Disciplina epistémica | las tres trampas: inferir lo sustentado, contradecir lo falso, declarar lo desconocido |

## Cómo se compara en los tests

La salida de un agente **no** se compara byte a byte con la referencia (dos agentes redactan distinto). Se compara estructuralmente:

1. ¿La regla del estado `ACTIVE` aparece con confianza alta y evidencia que incluye el rechazo?
2. ¿La afirmación del correo aparece en `contradictions` y **no** en `candidate_rules` ni en `dependencies`?
3. ¿La rama de nacionalidad extranjera aparece en `unknowns` y **no** entre los pasos observados?
4. ¿Los cuatro tipos de query aparecen con las tablas correctas?
5. ¿Toda referencia de evidencia resuelve?
6. ¿El JSON valida contra el schema?
