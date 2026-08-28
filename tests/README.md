# Tests

```bash
python3 -m unittest discover -s tests          # todo
python3 -m unittest discover -s tests -v       # con detalle
```

Solo biblioteca estándar (`unittest`); `jsonschema` habilita las comprobaciones de forma contra los contratos (sin él, esos tests se saltan).

## Qué cubren

**`test_correlate.py`** — Correlate contra el fixture `examples/legacy-demo`:

- las cuentas de la clave de respuestas: 46 líneas crudas, 0 sin parsear, 17 eventos conservados, 2 peticiones, 1 sin asignar;
- el ruido desaparece (sondeos de salud, `SELECT 1`, validación de pool);
- la evidencia protegida sobrevive: el WARN de rechazo, los dos INSERT, la respuesta 409;
- **dos SQL idénticos con parámetros distintos no se deduplican** (regresión de un bug real encontrado al escribirlo);
- `events.jsonl` y `flow.json` validan contra sus schemas;
- las dos peticiones de la ventana no se mezclan, y la base de cada enlace es explícita;
- el evento de arranque queda como *sin asignar*, no descartado;
- todo evento resuelve a una línea cruda existente;
- **determinismo**: dos corridas producen bytes idénticos;
- una fuente sin parser falla con un mensaje claro.

**`test_export.py`** — Package y Export:

- el paquete tiene todo lo que el agente necesita y se niega a sobrescribir;
- la salida de referencia (`expected/runtime-discovery.json`) es aceptada y publicada con sus derivados;
- se rechazan: referencias a evidencia inexistente, `raw_ref` fuera de rango, conclusiones sin evidencia, confianzas fuera del vocabulario, sesión equivocada, salida ausente — y una salida rechazada **no se publica**.

## Lo que falta probar (cuando exista)

- Parsers de más stacks: cada perfil nuevo trae sus líneas de log como fixture.
- Ventanas concurrentes: dos peticiones traslapadas resueltas por afinidad, y el caso ambiguo que debe quedar sin asignar.
- Inspect, Rehydrate y Observe, cuando se implementen.
- La integración completa contra el legacy-demo **levantado de verdad**, comparando evidencia real contra la sintética.
