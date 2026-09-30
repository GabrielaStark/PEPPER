# Cambios

PEPPER se versiona desde la auditoría del 2026-09-29. Cada entrada dice qué cambió y qué hallazgo de [`docs/documentacion/AUDITORIA-2026-09-29.md`](docs/documentacion/AUDITORIA-2026-09-29.md) cierra, para que quien audite después pueda comprobar el cierre en vez de creerlo. Las decisiones de diseño que cada cambio trajo están en [`DECISIONES.md`](docs/documentacion/DECISIONES.md) (D38–D45).

## 0.2.0 — 2026-09-30

La versión que responde a la auditoría 2026-09-29. Antes de ella PEPPER era `0.1.0` sin cambios registrados: 37 decisiones, 379 pruebas y ningún CHANGELOG.

### Las promesas dicen lo que el código garantiza (auditoría §2, §5.1–5.4)

- **Modelo de amenazas** en [`THREAT-MODEL.md`](docs/documentacion/THREAT-MODEL.md): adversarios, receptores (el modelo remoto, con nombre), qué garantiza cada capa y qué no. `README`, `CLAUDE.md`, `AGENTS.md` y `PRINCIPIOS.md` dicen lo mismo: nada del **entorno reconstruido** sale por la red de contenedores; el agente que orquesta **es un modelo remoto** (Principio 10) y lee el mapa y la evidencia; el paquete del discovery es la única de esas salidas que pasa por un gate (D24).
- **Principio 4** reformulado: el núcleo conoce **formatos**, no sistemas; los lectores existentes se listan en `PERFILES.md`; un formato nuevo es Python en `pepper/inspect/readers/`, un sistema nuevo del mismo formato es solo datos.
- **Export** dice lo que verifica: trazabilidad (cada afirmación señala una fuente que existe), no verdad; la prosa la revisa una persona (Principio 2).
- **Requisitos explícitos** en `README`/`QUICKSTART`: Claude Code con un modelo de pago, plataformas probadas y no probadas, acceso al registro de imágenes, JDK 8 para Groovy viejo.
- `SECURITY.md` (reporte privado por aviso de seguridad de GitHub), `CONTRIBUTING.md` (cómo se contribuye un perfil sin filtrar el legacy y cómo se prueba sin uno).
- **`verificar.py` contrasta la documentación con el código** (§1.c): los subcomandos y banderas del CLI contra `REFERENCIA.md`, la tabla de perfiles contra `profiles/`, las rutas canónicas citadas en `CLAUDE.md`/`AGENTS.md`/comandos entre sí, y `profiles/*/extractors.json` contra su contrato. Las siete contradicciones doc↔código que la auditoría listó están corregidas y ahora romperían CI.
- Fuera el entorno de referencia sin verificar (`examples/legacy-demo/expected/reference-environment/`) y el rastro del primer legacy en comandos, referencia y skills.

### La frontera de datos con controles técnicos (§2.3, §5.6)

- **Guardia de datos para el agente**: `.claude/settings.json` niega al orquestador leer `legacy/**`, la evidencia cruda, `pepper-out/rehydrate/.env`, el compose y las llaves; el hook `PreToolUse` (`scripts/guardia_datos.py`) bloquea `cat`/`python3 -c`/heredocs/clientes de base sobre esas rutas, fail-closed (salida 2 ante cualquier duda). Se desarrolla con `PEPPER_GUARDIA_DEV=1`.
- **`pepper authorize` exige terminal** (`isatty`) y la palabra `AUTORIZO` escrita; no puede correrlo un agente.
- **Lo no inspeccionable no viaja**: binarios, archivos > 50 MB, indecodificables y enlaces quedan fuera del paquete (`excluded_uninspected` en el manifiesto) salvo `--include-uninspected` explícito; el README del paquete dice qué **no** viaja (capturas, respaldo, contenedores).
- Llave HMAC de seudónimos **por sistema** (huella del legacy), no una para todos; el nombre de quien autoriza ya no viaja en el paquete.
- Export corre el escáner de datos sensibles sobre `output/` (el documento del agente) antes de dar el paquete por bueno.
- Redactor bilingüe y por contenido (no solo por nombre de columna): tarjetas, RFC/CURP/SSN, cuentas; las definiciones SQL (vistas, funciones, triggers) pasan por el redactor antes de entrar al mapa.
- `explore` resuelve las credenciales de prueba con `roles[].user_sql` dentro del contenedor: el `explore.json` no lleva llaves de usuario.

### Aislamiento por construcción (§1.a, §5.7, §5.8)

- **Sonda de salida desde dentro** de la red interna en `isolate --live`: un contenedor efímero intenta salir (TCP, UDP, DNS) y el veredicto exige que no pueda. El host alcanzable en su puerta de enlace es aviso con puertos, no fuga (es la máquina misma).
- **`compose create` → inspección según Docker → `start`**: los contenedores se inspeccionan **antes** de arrancar; en `FAILED` se apagan. `docker info` es precondición declarada (iptables, ip6tables, rootless): lo desconocido es `UNKNOWN`, nunca `VERIFIED`.
- Plantillas de todos los perfiles con `cap_drop: [NET_RAW]`, `no-new-privileges`, `mem_limit` y `restart` en el ingress; **una sola pasada de sustitución validada** por variable (identificador, IP, imagen, ruta sin caracteres de shell): un valor con forma extraña no se inserta.
- Un puerto publicado en `0.0.0.0` es error; el DNS en vivo solo acepta el sumidero de la subred interna; IPv6 también se lee (`/proc/net/tcp6`).
- **Navegador hermético también en producción**, no solo en su prueba: `--host-resolver-rules` que solo resuelve el ingress, `service_workers="block"`, CSP con `webrtc 'block'`, `X-DNS-Prefetch-Control`, COOP; el guardián se inserta tras el `<head>` real, `meta refresh` se lee con un parser de HTML, y HTML sin `Content-Type` se detecta por contenido. `/pepper-observe` corre sobre `explore --observe --headed`, no sobre el navegador de la persona.
- El stub registra **toda** conexión (HTTP, TLS, cliente que no habla, otro) y cierra con tiempo límite.
- CI: la prueba hermética con Chromium y el E2E con Docker **fallan** (no se saltan) cuando falta la herramienta (`PEPPER_CI=1`).

### El núcleo conoce formatos (§2.2, §5.10)

- **Registro de lectores** (`pepper/inspect/readers/`): una tabla dice cómo corre cada mecanismo, qué superficies alimenta, si necesita el respaldo o `javap`. Sustituye a tres tablas hermanas y una función cableada.
- **`regex_extractor`**: un stack con fuente en texto (PHP, Django, Rails, Node) declara rutas, jobs y hosts externos con una regex de grupos nombrados sobre miembros del artefacto; los grupos se mapean al contrato del mapa; lo que no cumple es hueco declarado.
- **`rehydrate.datasource` lee formatos**: `key_value` (`.env`, `.properties`, `.ini`, YAML plano), `json` (claves con puntos) y `xml` (ruta con predicado), con `datasource.files` y `datasource.keys`; `datasource.url_pattern` reemplaza a la URL JDBC cuando el perfil lo declara. Fail-closed con el archivo y la clave que faltó.
- `sql_shape` entiende los identificadores de cada dialecto: backticks, comillas dobles, corchetes, esquema calificado, `FROM ONLY`, `EXEC`.
- **`schemas/extractors.schema.json`**: `extractors.json` tiene contrato; `pepper map` lo exige antes de correr y dice qué clave está mal.
- Fuera del contrato del perfil lo que nadie leía (`collectors.method/location/enable`, `validation`, `rehydrate.steps`); un desplegable que no es zip es BLOCKED con el porqué; `java-wildfly-postgres` trae su `extractors.json`.
- **Fixtures por perfil** (`profiles/<id>/fixtures/`) y `tests/test_perfiles.py` parametrizado en CI: un tercero demuestra un `draft` sin un legacy real.

### La herramienta y el workspace se separan (§3, §5.5)

- **`pepper init <dir>`** crea el workspace fuera del clon, sin repositorio ni remoto: enlaces al paquete, la documentación, los perfiles y los contratos de la instalación; copias de lo que Claude Code carga de la raíz; `legacy/`, `docs/pepper/`, `pepper-out/`, `evidence/` con `.gitignore`. Desaparece `git clone … && rm -rf .git` (D43). El clon queda versionable (`pepper --version`, tags, `git pull`) y un perfil redactado en el workspace cae en `profiles/` del clon, listo para contribuirse.

### Primera familia que no es JVM (§2.2)

- Perfil **`php-apache-mysql`** (`draft`): PHP 7/8 con Apache y MySQL, datasource en `.env`, todo con lectores genéricos parametrizados (ni una línea de Python). Redactado sin legacy real, con fixtures sintéticos que la suite ejecuta en CI. El núcleo acepta un desplegable que es una **carpeta** (`rehydrate.artifact_kind = directory`, empacada como `legacy.tar`: D45) y elige la imagen del servidor por versión completa (`php:7.4-apache`), no solo por la mayor.
- **E2E de CI para la familia PHP** (`scripts/e2e_docker.py --profile php-apache-mysql`, job `e2e-php`): una ventanilla sintética en PHP clásico (`examples/e2e-php/`) con su respaldo mysqldump se levanta con Docker en cada cambio: carpeta → `legacy.tar`, MySQL restaurado y el usuario del `.env` creado, `pdo_mysql` compilado al arrancar, Apache servido por el ingress en loopback, aislamiento verificado en vivo.

### Bugs puntuales cerrados (§5, último bloque)

`meta refresh` sin `url=`/con entidades/sin `Content-Type`; DNS en vivo; puerto en `0.0.0.0`; stub; IPv6; redactor bilingüe; definiciones SQL; Export sobre `output/`; llave HMAC por sistema; autorizante fuera del paquete; capturas declaradas como no viajan; `BadZipFile` → BLOCKED; campos muertos del schema; E2E y Chromium obligatorios en CI; `sql_shape` con dialectos; contradicciones doc↔código; rastro del primer legacy; `extractors.json` para `java-wildfly-postgres`; `SECURITY.md`, `CONTRIBUTING.md`, versión.

### Lo que esta versión no hace (y lo dice)

- Ningún perfil nuevo corrió contra un legacy real en esta versión: `php-apache-mysql` es `draft` con fixtures sintéticos.
- El orquestador sigue siendo un modelo remoto: el guardia acota lo que lee, no cambia quién lee (Principio 10, THREAT-MODEL).
- Windows sin WSL2, Podman y OrbStack siguen sin probar.

## 0.1.0 — 2026-09-02 → 2026-09-28

El desarrollo inicial: cuatro perfiles (tres JVM/PostgreSQL, uno Grails/MySQL validado contra un legacy real), el ciclo completo mapa → levantar → explorar → correlacionar → empaquetar → descubrir → exportar, 37 decisiones y las auditorías previas cuyos cierres registra `DECISIONES.md` (D22, D24, D30, D35–D37).
