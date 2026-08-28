# legacy-demo

Legacy de juguete para probar PEPPER de punta a punta. Es un sistema **ficticio** de registro de solicitudes de trámite: Java EE sobre WildFly con PostgreSQL, con el estilo (y los vicios) de un legacy real sin documentación confiable.

Sirve como test de integración canónico: **sabemos de antemano lo que PEPPER debe encontrar**, así que podemos medir si lo encuentra.

## Estructura

```text
artifacts/       lo que "llega" del legacy — la entrada de PEPPER
├── source/        código Java (Maven, WAR)
├── database/      esquema y datos de prueba
├── configuration/ properties y fragmento de standalone.xml
└── docs/          el manual técnico heredado (con una afirmación falsa)

raw-evidence/    evidencia de una ejecución del flujo "Registrar solicitud"
├── session.json      ventana observada y colectores
├── http.jsonl        proxy de PEPPER (con correlation_id inyectado)
├── application/      log de WildFly
└── database/         log de sentencias de PostgreSQL

expected/        la clave de respuestas
├── notes.md                  qué debe encontrar PEPPER y cómo se evalúa
├── runtime-discovery.json    salida de referencia (golden file)
└── reference-environment/    lo que Rehydrate debería generar
```

> La evidencia de `raw-evidence/` es **sintética**: construida a mano para reproducir lo que este legacy emitiría, con el formato real de cada fuente. Está marcada como tal en `session.json`. Cuando el entorno de referencia se levante por primera vez, hay que contrastarla con la captura real y corregirla si difiere.

## El flujo observado

Un operador de ventanilla registró una solicitud. Lo intentó dos veces:

```text
13:20:44  POST /api/applications  ciudadano 1003  →  HTTP 409  (rechazado)
13:21:01  POST /api/applications  ciudadano 1001  →  HTTP 201  SOL-2026-000042
```

Ese rechazo no es un accidente del fixture: es la prueba en runtime de una regla que nadie documentó.

## Lo que PEPPER debe encontrar

1. **Una regla escondida** — solo se registran solicitudes de ciudadanos en estado `ACTIVE`.
2. **Una contradicción** — el manual afirma que se envía un correo de confirmación; no ocurre, y el código que lo haría nunca se invoca.
3. **Un desconocido** — existe una rama para ciudadanos extranjeros que el flujo no ejercitó; debe declararse, no describirse como observada.

Más ruido deliberado (sondeos de salud, validaciones de pool, `SELECT 1`) que la fase Correlate descarta: de 46 líneas crudas sobreviven 17 eventos, de los que 14 se citan como evidencia en la salida de referencia.

Los detalles, los criterios de aceptación y cómo se comparan las salidas están en [expected/notes.md](expected/notes.md).

## Probarlo

```bash
python3 -m pepper demo
```

Corre Correlate y Package sobre este fixture y deja el paquete listo para el agente. Paso a paso en [QUICKSTART.md](../../docs/documentacion/QUICKSTART.md).

## Dos niveles de uso

| Nivel | Necesitas | Ejercita |
|---|---|---|
| Evidencia pre-capturada (`raw-evidence/`) | Python 3.9+ y un agente | Correlate → Package → Discover → Export — **funciona hoy** |
| Legacy corriendo | Docker, Maven, JDK 8 | el pipeline completo, incluido Rehydrate ([entorno de referencia](expected/reference-environment/), sin verificar) |
