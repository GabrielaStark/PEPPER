---
name: perfil-stack
description: "Constitución de los perfiles de PEPPER: cómo se redacta un perfil (profile.json) y sus parsers declarativos para un stack tecnológico, cómo se valida contra los contratos y cómo pasa de borrador a validado. Todo el conocimiento de un stack entra como datos; el núcleo nunca aprende una tecnología."
allowed-tools: Read, Grep, Glob, Write, Bash(python3:*)
---

# Perfil de stack — la constitución de los perfiles

Un **perfil** es todo el conocimiento específico de un stack, empaquetado como datos en `profiles/<id>/`. Es lo que permite a PEPPER aspirar a cualquier legacy sin que el núcleo crezca por tecnología: un stack nuevo es un JSON nuevo, no código nuevo.

## 1. Anatomía de un perfil

```text
profiles/<id>/
├── profile.json          contrato: schemas/profile.schema.json
├── parsers/<fuente>.json un parser declarativo por fuente (schemas/parser.schema.json)
├── compose.template.yml  plantilla de orquestación de la receta (cuando exista)
└── README.md             estado, pendientes para validarse
```

`profile.json` declara cinco cosas:

| Sección | Qué contiene | Ejemplo (java-wildfly-postgres) |
|---|---|---|
| `detection.signals` | señales con peso para reconocer el stack en un directorio de artefactos: `file_exists`, `file_content`, `extension`, `directory`; `min_score` decide | `*.war` (+2), `standalone*.xml` (+3), `urn:jboss:domain` dentro (+3) |
| `rehydrate` | `required_inputs` (sin ellos → BLOCKED), `optional_inputs`, `compose_template`, `steps` en orden | WAR o código compilable; respaldo de BD; configuración de datasource |
| `collectors` | fuentes de evidencia del stack: `source`, `method`, `location`, `enable` (cómo subir el nivel antes del arranque), `parser` | `server.log` de WildFly con DEBUG para los paquetes de la app |
| `validation` | comprobaciones tras el arranque; alimentan `environment.validations` | deployment `OK`, datasource `test-connection-in-pool` |
| `status` | `draft` o `validated` | |

Los colectores genéricos (proxy HTTP, stdout/stderr de contenedores, log del motor de BD) los aporta el núcleo y no se declaran.

## 2. Anatomía de un parser

Un parser es una expresión regular con grupos nombrados más reglas. El núcleo (`PatternParser`) lo interpreta; nunca hay que programar.

```json
{
  "source": "wildfly",
  "line_pattern": "^(?P<timestamp>\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2},\\d{3})\\s+(?P<severity>[A-Z]+)\\s+\\[(?P<logger>[^\\]]+)\\]\\s+\\((?P<thread>[^)]+)\\)\\s+(?P<message>.*)$",
  "timestamp": { "format": "%Y-%m-%d %H:%M:%S,%f" },
  "fields": {
    "component": { "from": "logger", "transform": "last_segment" },
    "severity":  { "from": "severity", "map": { "INFO": "info", "WARN": "warn", "ERROR": "error" }, "default": "info" },
    "message":   { "from": "message" },
    "metadata.thread": { "from": "thread" }
  },
  "event_type": { "default": "log", "rules": [ { "when": { "field": "message", "matches": "Exception" }, "value": "exception" } ] },
  "continuation": { "pattern": "^(\\s+at |Caused by: )" },
  "affinity": ["thread"],
  "noise": [ { "id": "pool-validation", "description": "validación periódica del pool", "matches": "^Periodic validation" } ]
}
```

Las piezas y cuándo se usan:

| Pieza | Para qué |
|---|---|
| `line_pattern` | reconoce una línea; **debe** capturar el grupo del timestamp |
| `timestamp.format` | strptime; si la fuente no trae zona, se aplica la de la sesión |
| `fields` | grupos → `component`, `severity`, `message`, `operation`, `correlation_id`, `metadata.<clave>`; con `transform` (`last_segment`, `upper`, `keyed_parameters`…) y `map` |
| `event_type.rules` | primera regla que cumple gana; `sql` activa la extracción genérica de operación y tabla |
| `sql.strip_prefix` | qué quitar del mensaje para dejar la sentencia limpia |
| `continuation` | líneas que no cumplen el patrón y se anexan al evento anterior (stack traces) |
| `merge_into_previous` | líneas que sí cumplen el patrón pero complementan al evento anterior (los `DETAIL` con parámetros de PostgreSQL), fusionadas por una clave (`pid`) |
| `affinity` | claves de metadata que agrupan eventos de una misma ejecución (`thread`, `pid`); la correlación las usa para ventanas concurrentes |
| `noise` | ruido propio de la fuente; se descarta con auditoría (nunca evidencia protegida) |

## 3. Cómo se redacta un parser nuevo

1. Toma **líneas reales** de la fuente (mínimo 20, con casos raros: multilínea, errores, parámetros).
2. Escribe `line_pattern` para la línea típica; verifica que no queden líneas sin parsear salvo las que de verdad son basura.
3. Mapea severidad al vocabulario (`debug`, `info`, `warn`, `error`, `fatal`).
4. Decide `event_type`: qué es `sql`, qué es `exception`, qué es `log`.
5. Declara `affinity` si la fuente identifica ejecuciones (thread, pid, request id).
6. Declara `noise` para lo repetitivo sin contenido (heartbeats, validaciones de pool).
7. Valida: `python3 -m pepper validate profiles/<id>/parsers/<fuente>.json`.
8. Prueba contra evidencia real: `python3 -m pepper correlate <evidencia>/ --profile <id> --out /tmp/prueba` y revisa `reduction.md` → "Líneas sin parsear" debe ser 0 o explicable.

## 4. La receta de rehydrate

`rehydrate.steps` es la lista ordenada y legible de lo que hay que hacer para levantar el stack; `compose.template.yml` es su forma ejecutable con variables `{{…}}` que Rehydrate sustituye a partir de los artefactos. Reglas:

- **Fidelidad**: las versiones son las del legacy (detectadas en artefactos o notas), nunca "la última".
- **Observabilidad de antemano**: la receta activa lo que Observe necesita antes del arranque (`log_statement=all`, nivel DEBUG de la app, proxy delante del puerto).
- **Nada inventado**: lo que la receta necesita y no está en los artefactos va a `required_inputs`, y su ausencia produce BLOCKED.

## 5. Ciclo de vida

```text
Inspect encuentra un stack sin perfil
  → el agente redacta profiles/<id>/ con status "draft" (señales, receta, colectores, validaciones, parsers)
  → un humano lo revisa y lo prueba contra ese legacy (Rehydrate + Observe + Correlate)
  → si funciona, status "validated" → habilita el escalón 1 para el siguiente legacy con ese stack
```

Un perfil `draft` **nunca corre sin supervisión**. Un perfil `validated` ha demostrado levantar y observar al menos un legacy real.

## 6. Reglas

1. **Un perfil nunca modifica el núcleo.** Si un stack "necesita" tocar el correlacionador, el defecto está en el núcleo y se corrige ahí, de forma genérica.
2. **Cada señal de detección cita un artefacto real** del legacy que la motivó. Señales inventadas producen falsos positivos en el siguiente legacy.
3. **Los ids son kebab-case** (`^[a-z0-9-]+$`) y describen el stack: `java-wildfly-postgres`, `dotnet-iis-sqlserver`, `php-apache-mysql`.
4. **Todo borrador valida** contra su contrato antes de entregarse: `python3 -m pepper validate profiles/<id>/profile.json profiles/<id>/parsers/*.json`.
5. **Sin perfil no hay bloqueo**: si no hay tiempo de redactarlo, el legacy va al escalón 2 (colectores genéricos) o 3 (inspección). Redactar el perfil es la inversión que convierte ese legacy en el escalón 1 del siguiente.

## Checklist de auto-validación de un perfil

- [ ] `profile.json` valida contra `schemas/profile.schema.json`; cada parser contra `schemas/parser.schema.json`.
- [ ] `status` es `draft` (solo un humano lo cambia a `validated`, tras probarlo).
- [ ] Cada señal de detección apunta a algo que existe en los artefactos de este legacy.
- [ ] `required_inputs` lista lo que de verdad bloquea; nada de la receta asume un insumo inexistente.
- [ ] Las versiones de la receta son las del legacy, con la evidencia de dónde salieron.
- [ ] Cada colector tiene `parser`, y cada parser se probó contra líneas reales (0 líneas sin parsear, o explicadas).
- [ ] Las fuentes con thread/pid declaran `affinity`.
- [ ] El ruido declarado es repetitivo y sin contenido; ninguna regla de ruido podría tragarse un error o una escritura.
- [ ] El `README.md` del perfil dice qué falta para pasar a `validated`.

## Anti-patrones

- ❌ Modernizar versiones "de paso".
- ❌ Inventar un datasource, una URL o una contraseña porque "así suele ser".
- ❌ Reglas de ruido amplias (`.*INFO.*`) que descartan evidencia real.
- ❌ Poner lógica de un stack en el núcleo en vez de en el perfil.
- ❌ Marcar `validated` sin haber levantado y observado un legacy real con ese perfil.
