# Clave de respuestas del fixture

Qué debe encontrar PEPPER al procesar este legacy. Sirve como criterio de aceptación del pipeline: no basta con que produzca *algo*, tiene que producir **esto**.

`funcional.json` y `funcional.md` de esta carpeta son la salida de referencia. Están escritos a mano (`engine.agent: "golden-fixture"`), validan contra `schemas/functional-discovery.schema.json`, sus fuentes `observado` apuntan a líneas reales de `raw-evidence/` (o a event_id que `pepper correlate` asigna) y sus fuentes `en_codigo`/`en_doc`/`en_config` a archivos de `artifacts/`. `pepper export` los acepta tal cual (es uno de los tests). Este fixture no trae mapa (`pepper map`): no hay WAR ni respaldo, solo código; por eso las fuentes de código son rutas de archivo.

## Las tres trampas sembradas

### 1. Regla de negocio escondida — debe encontrarla

El sistema exige que el ciudadano esté en estado `ACTIVE` para registrar una solicitud. Un ciudadano `SUSPENDED` se rechaza con HTTP 409 **antes de cualquier escritura**.

No está documentada en ningún lado; al contrario, el manual dice que el campo es estadístico. La ventana observada la prueba dos veces: el intento rechazado (ciudadano 1003) y el exitoso (ciudadano 1001).

→ Debe aparecer como regla (`kind: validacion`, `basis: observado`, `confidence: confirmada`), con fuentes observadas (el `SELECT` de `citizen`, el WARN de rechazo, el 409) y una fuente `en_codigo` a `ApplicationService.java:35`.

### 2. Contradicción — debe reportarla, no ignorarla

`manual-tecnico.md` §3 paso 5 afirma que se envía un correo de confirmación al ciudadano, y `application.properties` tiene `notificaciones.habilitado=true`. En la ejecución **no hay rastro de envío de correo**: el flujo pasa del INSERT de historial directo al HTTP 201. En el código, `NotificationService` está inyectado en `ApplicationService` pero `sendRegistrationEmail` nunca se invoca — código muerto.

→ Debe aparecer en `contradictions`, y el SMTP en `integrations` con `observed: false`. El error clásico sería marcarlo observado por leerlo en la configuración: no hay evidencia de ejecución que lo respalde.

La segunda contradicción es más sutil: el manual §4 dice que el estado del ciudadano se usa "con fines estadísticos", cuando en realidad condiciona el registro. Es la documentación contradiciendo a la regla #1.

### 3. Desconocido — debe declararlo, no inventarlo

`ApplicationService.java:40-42` bifurca a `processForeignApplication` cuando la nacionalidad no es `MX` (agrega un historial `PENDING_CONSULAR_REVIEW`). El flujo observado nunca ejercitó esa rama: ambos intentos fueron de ciudadanos mexicanos.

→ Debe aparecer en `unknowns` (con `ask`: observar una ejecución con el ciudadano 1005) y, si se enuncia como regla, con `basis: en_codigo` y `confidence: sustentada`. **No** debe describirse como observado: el código prueba que la rama existe, no que se ejecutó.

Lo mismo aplica al camino de ciudadano inexistente (404), que tampoco se ejercitó.

## Las trampas accidentales (encontradas, no sembradas)

Al escribir el fixture se colaron tres incoherencias que resultaron ser **legacy realista**. La primera corrida de un agente real (Claude Code / Opus, 2026-08-28) las encontró todas, así que se documentan como parte del fixture: un discovery a fondo debería llegar a ellas.

1. **El folio "consecutivo por año" no se reinicia.** `manual-tecnico.md` §1 afirma que el sistema "asigna un folio consecutivo por año", pero `01-schema.sql:4` crea `folio_seq` como secuencia global sin reinicio; el año solo se interpola del reloj del servidor (`ApplicationDao.java:27`). → contradicción, más pregunta abierta sobre qué pasa en enero.

2. **`application.properties` no lo lee nadie.** El archivo declara `folio.prefijo`, el SMTP y la URL del datasource, pero el prefijo está en duro en `ApplicationDao.java:28` y el datasource se resuelve por JNDI. Configuración fósil. → contradicción; refuerza la del correo, porque `notificaciones.habilitado=true` tampoco significa nada.

3. **El folio va en el consecutivo 42 y la solicitud creada tiene id 87.** Incoherencia de la evidencia sintética, no del código. → debe quedar como **pregunta** (reinicio anual, carga fuera del flujo, migración), nunca como conclusión.

## Cómo se ve una buena corrida

Referencia de la primera corrida real, para calibrar — no es un mínimo obligatorio, es lo que alcanzó un agente aplicando la skill a fondo:

```text
17 pasos · 9 reglas candidatas · 4 contradicciones · 11 desconocidos · 19 evidencias · 1 dependencia
```

Señales de calidad que se vieron, más allá de las tres trampas:

- La única dependencia observada fue PostgreSQL. El SMTP se quedó fuera pese a estar en configuración y manual.
- `confirmada` se usó solo donde runtime, código **y** manual coinciden (consulta al padrón; las dos escrituras). Donde el manual contradecía, la confianza **no** subió: R-001 se quedó en `fuertemente_sustentada` y la divergencia se fue a contradicción.
- Un desconocido cuestionó la propia base de correlación: *"¿Los pasos internos que se atribuyen a cada petición pertenecen realmente a ella?"* — correcto, porque solo los 4 eventos del proxy traen `correlation_id` y el salto WildFly→PostgreSQL es puramente temporal.
- Los desconocidos están redactados en comportamiento observable de negocio, contestables por alguien que no lee código.

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
