# Fase 2 — Correlate

## Objetivo

Convertir la evidencia cruda en una secuencia de eventos normalizados, correlacionados y reducidos, **de forma determinística** — antes de que cualquier agente la vea. El agente nunca debe buscar líneas relevantes en gigabytes de logs.

**Estado: implementada** (`pepper/correlate/`, probada contra el fixture).

```bash
python3 -m pepper correlate <evidencia>/ --out <salida>/ [--profile <id|ruta>] [--tolerance-ms 500]
```

`<evidencia>/` contiene `session.json` ([contrato](../../../schemas/session.schema.json)) y los archivos que declaran sus colectores. El perfil se toma de `environment.profile_id` de la sesión si no se indica.

## Normalización

Cada fuente se convierte al schema común de evento ([`schemas/event.schema.json`](../../../schemas/event.schema.json)):

```json
{
  "event_id": "E-004",
  "timestamp": "2026-08-25T13:20:44.517-06:00",
  "session_id": "flow-001",
  "source": "postgresql",
  "component": "database",
  "event_type": "sql",
  "operation": "SELECT",
  "correlation_id": null,
  "message": "SELECT id, nombre, status, nationality FROM citizen WHERE id = $1",
  "raw_ref": "database/postgresql.log:2",
  "severity": "info",
  "metadata": {
    "pid": "1249",
    "statement": "SELECT id, nombre, status, nationality FROM citizen WHERE id = $1",
    "table": "citizen",
    "parameters": { "$1": "1003" },
    "inferred_correlation_id": "req-8171",
    "correlation_basis": "ventana temporal"
  }
}
```

`raw_ref` apunta a la línea original: toda inferencia posterior debe poder rastrearse hasta la evidencia cruda. `correlation_id` es solo lo que la fuente emitió; lo que PEPPER infiere va en `metadata.inferred_correlation_id` con su `correlation_basis` — lo observado y lo inferido nunca se mezclan.

### Parsers: datos, no código

Quién convierte cada fuente:

- **`http-proxy`** — parser del núcleo (es el formato propio del proxy de PEPPER).
- **Cualquier otra fuente** — un parser **declarativo** que aporta el perfil: un JSON con una expresión regular con grupos nombrados, más reglas para tipo de evento, continuaciones (stack traces), fusión de líneas (los `DETAIL` de PostgreSQL con los parámetros) y ruido específico. Contrato: [`schemas/parser.schema.json`](../../../schemas/parser.schema.json); ejemplos: [`profiles/java-wildfly-postgres/parsers/`](../../../profiles/java-wildfly-postgres/parsers/).

El núcleo interpreta esas especificaciones sin saber de WildFly ni de PostgreSQL. Lo único que hace de forma genérica es dar forma al SQL (operación, tabla, secuencia), porque SQL es SQL. Un stack nuevo = un JSON nuevo, que además el agente puede redactar durante Inspect.

## Claves de correlación

En orden de preferencia (de más fuerte a más débil):

```text
correlation_id explícito          (el proxy lo inyecta cuando el legacy no lo tiene)
afinidad dentro de la ventana     (thread, pid, ... — claves que declara cada parser)
ventana temporal de la petición   (entre request y response del proxy)
```

Los legacies rara vez traen IDs propios; por eso el header inyectado por el proxy es la columna vertebral, y la ventana temporal el respaldo. Cada enlace registra **qué clave lo sustenta** para que el agente sepa qué tan firme es. Cuando dos peticiones se traslapan y la afinidad no lo resuelve, el evento queda **sin asignar con la razón** — nunca se adivina.

## Reducción

Dentro de la ventana del flujo:

- descarta ruido genérico (sondeos de salud, `SELECT 1`) y el ruido que declara cada parser (p. ej. validación periódica del pool);
- descarta líneas de log repetidas de forma consecutiva e idéntica — **solo eventos `log`**: el SQL nunca se deduplica, porque dos consultas iguales con parámetros distintos (o un patrón N+1) son evidencia;
- **nunca descarta** evidencia protegida: severidad warn/error/fatal, excepciones, escrituras a base de datos, respuestas HTTP ≥ 400. Si está fuera de la ventana, se conserva marcada con `metadata.outside_window`.

Cada descarte queda auditado en `reduction.md` con su regla y su `raw_ref`.

## Salida

```text
<salida>/
├── events.jsonl     todos los eventos conservados, en orden, con IDs E-001…
├── flow.json        eventos agrupados por petición con la base de cada enlace (schemas/flow.schema.json)
├── flow.md          lo mismo, legible
├── reduction.md     qué se descartó y por qué
├── session.json     copia
└── raw/             copia de la evidencia cruda (los raw_ref resuelven aquí)
```

Ejemplo de `flow.md`:

```text
## req-8171  POST /api/applications → 409  (537 ms)

13:20:44.118  proxy                  POST /api/applications
              E-002  ·  correlation_id
13:20:44.401  ApplicationService     Registering application for citizen 1003
              E-003  ·  ventana temporal
13:20:44.517  database               SELECT id, nombre, status, nationality FROM citizen WHERE id = $1
              E-004  ·  ventana temporal
13:20:44.602  ApplicationService     Citizen 1003 rejected, status: SUSPENDED
              E-005  ·  ventana temporal + thread default task-1
```

## Garantía

Misma evidencia cruda → mismos bytes de salida. Está cubierto por test (`tests/test_correlate.py`) y es la propiedad que protege el valor diferencial de PEPPER: la reducción es auditable y repetible, no una interpretación.

## Pendiente

- Extraer solo los tramos referenciados de la evidencia cruda en vez de copiarla completa (importa cuando los logs pesan gigabytes).
- Saneamiento de datos sensibles en cuerpos HTTP y parámetros SQL antes de empaquetar.
- Parser genérico para stdout/stderr de contenedores (escalón 2 sin perfil).
