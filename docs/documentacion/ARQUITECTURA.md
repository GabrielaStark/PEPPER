# Arquitectura

## Invariante central

> **El núcleo de PEPPER conoce formatos, no sistemas.**

El núcleo trae lectores de formatos (bytecode de la JVM vía `javap`, Groovy compilado, configuración de Spring Boot, `pg_dump -Fc`, SQL en texto, logs de texto, HTML) y mecanismos genéricos (regex sobre miembros del artefacto, lectores de configuración clave-valor/JSON/XML). Un **perfil** parametriza esos lectores con datos para un sistema concreto. La prueba de fuego para cualquier cambio: *¿esta línea conoce un sistema (un nombre, una ruta, un dominio de negocio)?* Si sí, no va al núcleo. *¿Conoce un formato nuevo?* Entonces es un lector en `pepper/inspect/readers/`, y así se declara: no se disfraza de "mecanismo agnóstico".

## Vista general

```text
                      ┌──────────────────────────────────────────┐
                      │                PERFILES                  │
                      │ detección · extractores del mapa ·       │
                      │ receta de rehydrate · parsers ·          │
                      │ lectura de formularios · fixtures        │
                      └──────────────────┬───────────────────────┘
                                         │ (datos que parametrizan lectores del núcleo)
                                         ▼
┌─────────┐   ┌───────────┐   ┌─────────┐   ┌───────────┐   ┌──────────┐   ┌──────────┐   ┌────────┐
│   MAP   │ → │ REHYDRATE │ → │ EXPLORE │ → │ CORRELATE │ → │ PACKAGE  │ → │ DISCOVER │ → │ EXPORT │
└─────────┘   └───────────┘   └─────────┘   └───────────┘   └──────────┘   └──────────┘   └────────┘
 lo que el     entorno en      el sistema    eventos y       carpeta        el agente       contrato
 sistema ES    contenedores    se recorre    peticiones      autocontenida  escribe QUÉ     validado,
 (redactado)   sin salida,     solo (o una   con acción      inspeccionada  HACE el sistema acumulado;
               verificados     persona, en   y SQL           y sustituida;                  sin datos
               antes de        el navegador                  lo ilegible                    de personas
               arrancar        de PEPPER)                    no viaja
```

Dos fuentes, no una. **El mapa** (`pepper map`, Inspect) dice lo que el sistema *es*: rutas, jobs, pantallas con campos y botones, clases con constantes y mensajes, tablas con conteo, triggers y funciones con cuerpo, catálogos completos y distribuciones reales, todo pasado por el redactor. **La evidencia** (Observe → Correlate) dice lo que el sistema *hace* cuando alguien lo opera. Discover cruza las dos y escribe el documento funcional; Export lo valida y lo acumula.

Y un tercer actor que no está en la línea: **el agente que orquesta es un modelo remoto**. Lo que lee sale de la máquina, así que lo que puede leer lo acota un guardia técnico (abajo), no una instrucción.

## Módulos del núcleo

| Módulo | Entrada | Salida | Qué conoce |
|---|---|---|---|
| `inspect` | artefacto + respaldo + perfil | `system-map.json` + `map/*.md` (`pepper map`); `pepper detect` | lectores de formato en `inspect/readers/` (registro único de mecanismos); patrones del perfil en `extractors.json` (con contrato propio) |
| `rehydrate` | artefacto + respaldo + perfil | `pepper rehydrate --up`: la red que el artefacto espera, restauración, `create` → inspección según Docker → `start`, `isolate` en vivo → `environment.json` (`READY`/`PARTIAL`/`BLOCKED`/`FAILED`; tras FAILED el entorno queda detenido) | plantillas e imágenes del perfil; lectores de configuración embebida (Spring, Groovy compilado, clave-valor, JSON, XML) y `url_pattern` del perfil; valores de plantilla validados por su forma |
| `explore` | entorno corriendo + mapa + `explore.json` | un Chromium local con el resolver cerrado, por el ingress; un usuario por rol (clave resuelta dentro del contenedor con `user_sql`); cada pantalla; rechazos; llenado; planes; `--observe` para una persona → `evidence/<sid>/` | ninguna (selectores y roles vienen de `explore.json`, que el agente escribe desde el mapa) |
| `observe` | entorno corriendo | `pepper collect`: la ventana desde los contenedores | colectores genéricos |
| `correlate` | evidencia cruda | `events.jsonl` + `flow.json/md` (petición → acción → SQL/log) | parsers declarativos del perfil; SQL con dialectos (backticks, comillas, corchetes) |
| `package` | correlated + mapa + legacy + discovery anterior | paquete controlado + manifest externo; gate de datos por sistema y categoría; lo no inspeccionable no viaja | ninguna |
| `discover` | paquete controlado | `funcional.json/md` (lo escribe el agente) | ninguna |
| `export` | salida del agente | validada contra el contrato, sin credenciales ni datos con patrón; publicada por sesión y como documento del sistema | ninguna |
| `isolate` | compose (resuelto) y contenedores | VERIFICADO / NO AISLADO / NO VERIFICADO; en vivo, con una sonda desde la red interna | Docker en Linux |
| `proxy` (ingress), `stub` | — | `http.jsonl`; registro de toda conexión externa | HTTP; TLS y protocolos con banner solo se registran |
| guardia (`scripts/guardia_datos.py`) | cada llamada a herramienta del agente | bloqueo con motivo | rutas del workspace |

## `pepper map`: lo que el sistema es

Lectores del núcleo (por formato), patrones del perfil:

| Mecanismo | Qué saca | Cómo | Conoce |
|---|---|---|---|
| `jvm_route_annotations` | rutas HTTP y jobs con su cron | `javap -v` sobre las clases que el perfil señala | anotaciones de Spring |
| `jvm_class_inventory` | por clase: métodos públicos, constantes, cadenas | `javap -c -constants` por lotes; solo clases propias | bytecode JVM |
| `groovy_config_values`, `groovy_controller_actions`, `groovy_url_mappings` | jobs con cron, rutas por convención y declaradas | `Config`/`DataSource`/`UrlMappings` reconstruidos del bytecode de Groovy 1.7–2.x | Groovy compilado |
| `view_templates` | pantallas: campos, botones→acción, mensajes, condiciones por rol | regex del perfil sobre las vistas; bundle i18n resuelto | ninguno (patrones) |
| `regex_extractor` | rutas, jobs u hosts desde código fuente en el artefacto | regex con grupos nombrados del perfil sobre miembros (PHP, Django, Rails, Node con fuente) | ninguno (patrones) |
| `pg_dump_custom` | tablas con conteo, triggers y funciones, catálogos, distribuciones | lector propio del formato custom de `pg_dump`, sin PostgreSQL | `PGDMP` |
| `sql_dump` | lo mismo para SQL en texto (mysqldump, mariadb-dump, pg_dump plano) | lector en una pasada, sin motor | dialectos MySQL y PostgreSQL |
| `config_hosts`, `archive_url_scan` | hosts externos | configuración y URLs incrustadas | ninguno (patrones) |

Fail-honest: si falta `javap`, el respaldo no es del formato declarado, o ningún extractor cubre una superficie, el mapa sale `complete: false` con `coverage_gaps`. Sin datos personales ni secretos: las tablas de personas se cuentan pero no se vuelcan; columnas y renglones con pinta de credencial o de dato personal se redactan (por nombre, en español e inglés, y por patrón de valor); las cadenas del bytecode que parezcan credenciales o cuentas se omiten; los cuerpos de vistas, funciones y triggers pasan por el redactor.

## El perímetro

Tres capas, cada una verificada por el núcleo, nunca por el agente:

1. **La red de contenedores.** Red `internal: true`, todo host externo con alias al stub, un sumidero DNS por servicio, `cap_drop: NET_RAW` y `no-new-privileges` en cada servicio, sin socket ni privilegios; el ingress con identidad verificada (imagen, sin entrypoint, argv exacto, un montaje `:ro` con el hash de `proxy.py`) y una sola red de publicación en `127.0.0.1`. `isolate` lo comprueba en el compose resuelto, en los contenedores creados **antes de arrancar**, y en vivo; y en vivo una **sonda** corre desde la misma red intentando salir a internet y al host: lo que el compose promete se prueba, no se supone. `docker info` es precondición: Docker en Linux; lo demás no da verde.
2. **El navegador.** El ingress impone CSP con todo destino `'self'` y `webrtc 'block'`, inyecta un guardián, reescribe `Location` y `meta refresh` (leído con un parser), quita precargas y prefetch. El explorador es un Chromium propio con el resolver cerrado (`--host-resolver-rules`), sin service workers ni prefetch; `/pepper-observe` abre ese mismo navegador para la persona. Una prueba hermética con Chromium real corre en CI.
3. **El agente.** El guardia de datos (hook `PreToolUse`) bloquea leer `legacy/`, la evidencia cruda, lo correlacionado antes de sustituir, el `.env` y el compose rendidos, la base desechable y la autorización; y escribir en `legacy/`. Las claves de usuario por rol se resuelven dentro del contenedor. Lo que el agente sí lee está en `THREAT-MODEL.md`.

## Escalera de soporte

```text
¿Hay perfil que detecte el stack?
 ├─ SÍ  → escalón 1: pipeline completo (mapa + rehydrate + explore)
 └─ NO  → ¿el sistema corre o puede levantarse a mano?
           ├─ SÍ  → escalón 2: Observe con colectores genéricos (sin mapa, o con uno parcial)
           └─ NO  → escalón 3: Inspect produce BLOCKED (stack + faltantes + borrador de perfil)
```

Los tres escalones producen un entregable. `BLOCKED` es un resultado, no un fracaso. Y un perfil nuevo dentro de una familia conocida (JVM con PostgreSQL o MySQL; código fuente en el artefacto con `regex_extractor`) es datos; una familia nueva (IL de .NET, un `.bak`, escritorio, 3270) es un lector nuevo en el núcleo, y así se dice en PERFILES.md.

## Motor de análisis intercambiable

Discover no invoca APIs de ningún agente: el paquete controlado es una carpeta autocontenida con el mapa, la evidencia, lo inspeccionado del legacy, el discovery anterior y el prompt (`CLAUDE.md` y `AGENTS.md` apuntan a él). La salida se valida contra `schemas/functional-discovery.schema.json` venga de donde venga. El agente opera en **solo lectura**; el manifest raíz queda fuera de su directorio; Package bloquea symlinks, deja fuera lo que no puede inspeccionar y aplica el gate de datos.

## El discovery es acumulativo

El documento es del **sistema**, no de una ventana. Cada sesión recibe `previous/funcional.json`, lo extiende y lo corrige; Export publica la salida de la sesión en `docs/pepper/discovery/<sid>/` y el vigente en `docs/pepper/funcional.md|json`. Export comprueba trazabilidad (cada fuente existe) y ausencia de datos con patrón; que una fuente sostenga la afirmación lo comprueba una persona. Los desconocidos de la sección 12 dicen qué ventana observar después.

## Los contratos son la interfaz

- `profile.schema.json` — perfiles ↔ núcleo (incluye `http`: cómo leer formularios; `rehydrate.datasource` con sus mecanismos)
- `extractors.schema.json` — extractores del mapa ↔ registro de mecanismos
- `parser.schema.json` — parsers declarativos ↔ normalización
- `system-map.schema.json` — `pepper map` ↔ paquete ↔ agente
- `environment.schema.json` — rehydrate ↔ resto
- `session.schema.json` — observe ↔ correlate
- `event.schema.json`, `flow.schema.json` — correlate ↔ paquete
- `functional-discovery.schema.json` — agente ↔ export ↔ stark

Cualquier pieza es reemplazable mientras respete su schema. Y `scripts/verificar.py` comprueba en CI que la documentación cite comandos y banderas que existen, que la tabla de perfiles sea la de disco, y que las rutas canónicas no se contradigan.
