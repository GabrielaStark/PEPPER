---
name: perfil-stack
description: "Constitución de los perfiles de PEPPER: cómo se redacta un perfil (profile.json) y sus parsers declarativos para un stack tecnológico, cómo se valida contra los contratos y cómo pasa de borrador a validado. Todo el conocimiento de un stack entra como datos; el núcleo conoce formatos, no sistemas."
allowed-tools: Read, Grep, Glob, Write, Bash(python3:*)
---

# Perfil de stack — la constitución de los perfiles

Un **perfil** es todo el conocimiento específico de un stack, empaquetado como datos en `profiles/<id>/`. El núcleo conoce **formatos** (bytecode JVM, Groovy compilado, YAML de Spring, `pg_dump` custom, SQL en texto, plantillas y configuración como texto, `.env`/JSON/XML) y un perfil parametriza esos lectores: un sistema nuevo de un formato conocido es un perfil nuevo, sin código; un formato que el núcleo no lee es un lector genérico nuevo en el núcleo (con pruebas y schema) **y** el perfil que lo usa. Cuánto cuesta cada caso está medido en `docs/documentacion/PERFILES.md`.

## 1. Anatomía de un perfil

```text
profiles/<id>/
├── profile.json            contrato: schemas/profile.schema.json
├── extractors.json         qué lectores del mapa corren y con qué patrones (schemas/extractors.schema.json)
├── parsers/<fuente>.json   un parser declarativo por fuente (schemas/parser.schema.json)
├── compose.template.yml    plantilla de orquestación de la receta
├── restore.template.sh     cómo entra el respaldo a la base del contenedor
├── fixtures/               logs, configuración y respaldo sintéticos + expected.json (tests/test_perfiles.py los corre en CI)
└── README.md               qué cubre, qué decide la receta, qué falta para validarse
```

`profile.json` declara cuatro cosas:

| Sección | Qué contiene | Ejemplo (java-wildfly-postgres) |
|---|---|---|
| `detection.signals` | señales con peso para reconocer el stack en un directorio de artefactos: `file_exists`, `file_content`, `extension`, `directory`; `min_score` decide | `*.war` (+2), `standalone*.xml` (+3), `urn:jboss:domain` dentro (+3) |
| `rehydrate` | `required_inputs` (sin ellos → BLOCKED), `optional_inputs`, `compose_template`, `restore_template`, `datasource`, `database`, `server_images`, `descriptors` — la receta ejecutable; los pasos en prosa van al README del perfil | WAR o código compilable; respaldo de BD; configuración de datasource |
| `collectors` | fuentes de evidencia del stack: `source`, `file` (el archivo que `pepper collect` captura bajo `evidence/<sid>/`, p. ej. `containers/app.log`) y `parser`; cómo se activa cada fuente vive en el compose y se explica en el README | `containers/app.log` de WildFly → `parsers/wildfly-server.json` |
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

La receta es ejecutable, no prosa: `compose.template.yml` y `restore.template.sh` con variables `{{…}}` que Rehydrate sustituye —una sola pasada, cada valor validado por su forma— a partir de lo que el núcleo leyó del artefacto (`datasource`), del respaldo (`database`) y de `NOTAS.md` (`server_images`). Lo que una persona necesita saber de la receta (qué decide, qué desviaciones declara, qué le falta) va al `README.md` del perfil; hasta la auditoría 2026-09-29 existía `rehydrate.steps`, una lista en prosa que ningún código leía, y se quitó del contrato. Reglas:

- **Fidelidad**: las versiones son las del legacy (detectadas en artefactos o notas), nunca "la última".
- **Observabilidad de antemano**: la receta activa lo que Observe necesita antes del arranque (`log_statement=all`, nivel DEBUG de la app, proxy delante del puerto).
- **Nada inventado**: lo que la receta necesita y no está en los artefactos va a `required_inputs`, y su ausencia produce BLOCKED.

## 5. Ciclo de vida

```text
Inspect encuentra un stack sin perfil
  → el agente redacta profiles/<id>/ con status "draft" (señales, extractores, receta, colectores, parsers, fixtures)
  → un humano lo revisa y lo prueba contra ese legacy (Rehydrate + Observe + Correlate)
  → si funciona, status "validated" → habilita el escalón 1 para el siguiente legacy con ese stack
```

Un perfil `draft` corre, y el resultado lo declara como borrador. Un perfil `validated` ha demostrado levantar y explorar al menos un legacy real y una persona lo marcó.

## 6. Reglas

1. **Un perfil no lleva Python ni conocimiento de un sistema.** Si un stack necesita un lector que el núcleo no tiene (otro formato de respaldo, otro bytecode), el lector entra al núcleo como formato genérico —con pruebas, su entrada en el schema y su fila en PERFILES.md— y el perfil lo parametriza. Ningún nombre de cliente, host, paquete raíz ni dominio de negocio entra al núcleo; lo específico de un sistema es dato del perfil, y lo específico de una instalación no va ni al perfil.
2. **Cada señal de detección cita un artefacto real** del legacy que la motivó. Señales inventadas producen falsos positivos en el siguiente legacy.
3. **Los ids son kebab-case** (`^[a-z0-9-]+$`) y describen el stack: `java-wildfly-postgres`, `dotnet-iis-sqlserver`, `php-apache-mysql`.
4. **Todo borrador valida** contra sus contratos antes de entregarse: `python3 -m pepper validate profiles/<id>/profile.json profiles/<id>/extractors.json profiles/<id>/parsers/*.json`, y trae `fixtures/` que `python3 -m unittest tests.test_perfiles` ejecuta (contrato en `profiles/README.md`).
5. **Sin perfil no hay bloqueo**: si no hay tiempo de redactarlo, el legacy va al escalón 2 (colectores genéricos) o 3 (inspección). Redactar el perfil es la inversión que convierte ese legacy en el escalón 1 del siguiente.

## Checklist de auto-validación de un perfil

- [ ] `profile.json` valida contra `schemas/profile.schema.json`; `extractors.json` contra `schemas/extractors.schema.json`; cada parser contra `schemas/parser.schema.json`.
- [ ] `fixtures/expected.json` existe y `tests.test_perfiles` pasa para este perfil: logs sin líneas sin parsear (o las declaradas), datasource leído de `fixtures/config/`, respaldo sintético leído, y —si el fuente viaja en el desplegable— `map` con las rutas, jobs, hosts y pantallas que el perfil promete.
- [ ] Nada del fixture es real: ni filas, ni hosts, ni credenciales; `expected.json › notes` lo dice.
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
- ❌ Poner lógica de un sistema en el núcleo en vez de en el perfil (un lector genérico de un formato sí va al núcleo; un nombre de paquete, un host o una regla de negocio, jamás).
- ❌ Un perfil sin fixtures, o un fixture copiado de un legacy real sin anonimizar.
- ❌ Marcar `validated` sin haber levantado y observado un legacy real con ese perfil.
