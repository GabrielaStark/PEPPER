# Tests

```bash
python3 -m unittest discover -s tests          # todo
python3 -m unittest discover -s tests -v       # con detalle
```

Solo biblioteca estándar (`unittest`); `jsonschema` y `pyyaml` habilitan las comprobaciones de forma contra los contratos y del compose rendido (sin ellos, esos tests se saltan); la prueba hermética del navegador necesita Chromium de Playwright. En CI (`PEPPER_CI=1`) nada se salta: sin la herramienta, falla.

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
- la salida de referencia (`expected/funcional.json` + `funcional.md`) es aceptada y publicada, por sesión y como documento del sistema;
- se rechazan: referencias a evidencia inexistente, `raw_ref` fuera de rango, conclusiones sin evidencia, confianzas fuera del vocabulario, sesión equivocada, salida ausente — y una salida rechazada **no se publica**.

**`test_isolate.py`** — el entorno rehidratado no alcanza nada externo:

- los casos de fuga reales del primer legacy (red sin `internal`, `network_mode: host`, red no declarada, servicio sin `networks`, `extra_hosts` externo, host del artefacto sin alias al stub);
- ningún contenedor del legacy tiene salida; el ingress verificado por hash tiene exactamente una red de publicación que nadie más usa; cada servicio fija un sumidero DNS dentro de la subred; los servicios bajo `profiles:` también se verifican;
- el ingress rechaza `entrypoint`, comandos shell, upstreams que no correspondan a una dependencia interna y cualquier montaje distinto al proxy verificado (`:ro`); en vivo se comprueba que solo hable con el app y que imponga al navegador la política de PEPPER.

**`test_sensitive.py`** — el gate previo a agentes remotos: bloquea credenciales y CURP sin imprimir valores; exige reconocer binarios no inspeccionados; registra toda excepción en el manifest; marca el modo local para impedir que Claude Code lo abra por accidente.

**`test_proxy.py`** — el proxy HTTP del núcleo (todo en `127.0.0.1`, nada sale de la máquina):

- reenvía intacto (Host del cliente, redirects, HTML) e inyecta `X-Pepper-Correlation-Id` con el mismo id en petición, respuesta y app;
- captura cuerpos JSON y formularios, **redacta credenciales** (campos password/clave/secret/token) y jamás registra headers Authorization/Cookie;
- impone al navegador una CSP con todo destino `'self'`, inyecta el guardián de navegación, reescribe un `Location` externo y quita un `<meta refresh>` externo, registra los bloqueos como `direction: blocked`, descomprime HTML para inyectar y rechaza cuerpos gigantes;
- upstream caído → 502 registrado con nota;
- el `http.jsonl` que emite lo lee el `HttpProxyParser` del núcleo sin una línea sin parsear.

**`test_manifest.py`** — la integridad de la evidencia: manifest interno y externo, evento fabricado, archivo colado, artefacto alterado, symlink anidado, notas con credenciales redactadas.

**`test_systemmap.py`** — el mapa: lector propio del formato custom de `pg_dump` (cabecera, TOC, filas), catálogos con secretos y datos de personas redactados, distribuciones, mapa completo que valida contra el contrato.

**`test_parsers_reales.py`** — los parsers de los perfiles contra líneas de log reales de WildFly y PostgreSQL.

**`test_rehydrate.py`** — del artefacto sintético y el respaldo escrito por el test al plan y al compose; BLOCKED cuando falta algo; los hallazgos de la auditoría (restore con estado, hash del respaldo en el proyecto, `.env` escapado, perfil por `active` y multi-documento, servidor solo entre servidores de aplicación, alias y gateway, `environment.json` válido en todos los estados). Sin Docker.

**`test_explore.py`** — valores plausibles, la nota de sesión, el parser de `explore.jsonl`, la validación de `explore.json`, las credenciales (setup_sql, fallo fatal, sin dejar la clave en el log) y la salida honesta (exit ≠ 0 con cero trabajo). Sin navegador.

**`test_stub.py`** — el stub registra cada llamada externa con el query redactado.

**`test_tools.py`** — `pepper detect` y `pepper validate`: señales dentro de WARs **y de tarballs** (la herramienta no asume stack), modo encima-del-repo, errores claros.

**`test_init.py`** — `pepper init`: el workspace aparte del clon (enlace `pepper/` válido, `.pepper-home`, la herramienta copiada, `legacy/NOTAS.md`, el `.gitignore` del workspace, sin `.git`); se niega sobre un directorio con contenido o dentro del clon; `--force` recopia la herramienta sin tocar `legacy/`, `docs/pepper/`, `pepper-out/` ni `evidence/`. Desde el workspace, por subprocess: `--version` y `REPO_ROOT` resuelven a la instalación, `detect` evalúa sus perfiles, `demo` deja su salida en el workspace, el guardia copiado bloquea igual. La herramienta copiada no cuenta como legacy para `detect .` ni para `package --legacy .`. Y `rehydrate` planea con `legacy/` fuera de `REPO_ROOT`: las rutas de los volúmenes quedan relativas al compose y `check_static` las acepta.

**`test_collect.py`** — el colector genérico de contenedores, con un `docker` fingido que devuelve logs enlatados y anota sus invocaciones:

- layout de salida (`http.jsonl` del ingress sin contaminar por su stderr; `containers/*.log` y `*.err.log`);
- `--since`/`--until` llevan el margen declarado; `container_name` explícito gana al nombre `<proyecto>-<servicio>-1`;
- servicios bajo demanda (`profiles`) y contenedores inexistentes quedan **saltados con razón**, nunca en silencio;
- no sobrescribe una sesión; ventana sin zona horaria falla claro.

## Lo que falta probar (cuando exista)

- Parsers de más stacks: cada perfil nuevo trae sus líneas de log como fixture.
- Ventanas concurrentes: dos peticiones traslapadas resueltas por afinidad, y el caso ambiguo que debe quedar sin asignar.
- La integración completa contra el legacy-demo **levantado de verdad**, comparando evidencia real contra la sintética.

**`test_rehydrate_directory.py`** — un desplegable que es una carpeta (perfil `php-apache-mysql`): la carpeta bajo `legacy/` es el desplegable y los archivos sueltos no; el servidor sale de `NOTAS.md` con la versión completa (`php:7.4-apache`); el datasource se lee del `.env`; `render` empaca la carpeta como `legacy.tar` (0600, sin `.git`, sin seguir enlaces) y el compose resultante pasa `check_static` en VERIFIED.

**`test_perfiles.py`** — cada perfil de `profiles/` se demuestra con sus fixtures, sin legacy real: contratos, parsers sobre `fixtures/logs/` (0 líneas sin parsear o las declaradas), `discover_datasource` sobre `fixtures/config/`, el lector del respaldo sintético, y `expected.json › map` (rutas, jobs, hosts y pantallas que el perfil promete sobre un desplegable sintético).

**`test_readers.py`**, **`test_regex_extractor.py`**, **`test_datasource.py`**, **`test_sql_shape.py`**, **`test_extractors_schema.py`**, **`test_perfil_contrato.py`**, **`test_wildfly_extractors.py`** — el núcleo que conoce formatos (D44): el registro único de lectores; `regex_extractor` con Laravel, Django y Rails en la misma prueba y cada hueco declarado; `key_value`/`json`/`xml` y `url_pattern` fail-closed con el archivo y la clave que faltó; `sql_shape` con backticks, comillas, corchetes, esquema calificado y `EXEC`; `extractors.json` con contrato y `pepper map` que lo exige; el perfil sin campos muertos y el casado colector ↔ archivo; `java-wildfly-postgres` con extractores que terminan honestos.

**`test_guardia.py`** — el guardia de datos del agente (`scripts/guardia_datos.py`): bloquea `cat`/`python3 -c`/heredocs/clientes de base sobre `legacy/` y la evidencia cruda, deja pasar al núcleo y los listados, fail-closed ante cualquier error.

**`test_boundary.py`** — la frontera de datos: propuesta con alcance, llave HMAC por sistema (huella del legacy), lo no inspeccionable fuera del paquete salvo decisión explícita.

**`test_groovy.py`** — Groovy compilado: `groovyconfig` y los mecanismos Grails del mapa.

**`test_sqldump.py`** — el lector de SQL en texto (mysqldump, mariadb-dump, pg_dump plano): cabecera, tablas, filas, esquema de sistema detectado.
