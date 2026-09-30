# Troubleshooting de PEPPER

Problemas que salen y qué hacer. El detalle de cada fase está en [`REFERENCIA.md`](REFERENCIA.md); qué protege y qué no, en [`THREAT-MODEL.md`](THREAT-MODEL.md).

## El guardia de datos

**`guardia de datos de PEPPER: bloqueado — …`** — el agente intentó abrir algo que no puede entrar a su contexto: el respaldo, el desplegable, `evidence/*/http.jsonl`, las capturas, los logs de los contenedores, `pepper-out/*/correlated/`, el `.env` o el compose rendidos, la base desechable (`docker … exec db`, `psql`, `mysql`) o la autorización de datos. No es un fallo: es el perímetro. Lo que el agente necesita de ahí lo saca el núcleo redactado (`pepper map`, `environment.json`, `explore.jsonl`, `flow.md` del paquete). Si bloqueó algo que sí debería poder leer (un `pom.xml` limpio bajo `legacy/`), el mensaje dice por qué (trae credenciales o datos de personas, o es un formato de respaldo).

**Quiero editar `scripts/guardia_datos.py` o `.claude/settings.json` con Claude Code** — el guardia no se edita desde el agente. Lanza Claude Code con `PEPPER_GUARDIA_DEV=1 claude`: es una decisión de quien lanza el proceso, no del agente.

**Un heredoc o un `python3 -c` se bloqueó "por mencionar rutas protegidas"** — el código en línea no se puede inspeccionar; el guardia lo bloquea cuando nombra `legacy/`, `evidence/` u otra ruta protegida que exista en el workspace. Escribe el script en un archivo y córrelo por su ruta, o usa el núcleo.

## Levantar

**`rehydrate · BLOCKED · ningún perfil de configuración dentro del artefacto trae url, usuario y contraseña del datasource`** — el artefacto no dice a qué conectarse. Consigue la configuración externa del ambiente (el `application-*.yml`, el `standalone.xml` con el datasource) y ponla en `legacy/`; o escribe en `NOTAS.md` host, base y usuario y pide el perfil que los lea.

**`BLOCKED · el artefacto no trae descriptor de servidor y NOTAS.md no dice en qué corre`** o **`NOTAS.md no dice la versión de …`** — una línea en `legacy/NOTAS.md` ("producción es WildFly 21") resuelve. Por fidelidad no se adivina: si la versión no está en NOTAS.md ni en el artefacto, es BLOCKED, no "la más cercana".

**`BLOCKED · <plantilla>: el valor de \`db_user\` (…) no tiene la forma que PEPPER admite en una plantilla`** — un valor sacado del artefacto o del respaldo (usuario, nombre de base, dueño de un rol) trae caracteres que en un compose o en un shell serían código. PEPPER no lo inserta. Revisa de dónde sale; si es legítimo, es un caso que el perfil tiene que declarar de otra forma.

**`FAILED · aislamiento de db, stub según Docker, antes de arrancar`** — los contenedores se crearon y lo que Docker hizo con el compose no es lo que el compose promete (una red no interna, un montaje del host, capacidades). El entorno quedó detenido: `docker compose -f pepper-out/rehydrate/docker-compose.yml config` y compáralo con el reporte.

**`FAILED · el servidor de aplicaciones arrancó: sin señal de arranque en 300 s`** — `docker compose -f pepper-out/rehydrate/docker-compose.yml logs app` (tras un FAILED el entorno queda detenido, con sus logs). Lo típico: la imagen del servidor no es la que el WAR necesita (APIs javax vs jakarta), o el WAR espera un archivo/ruta que no existe. Corrige el perfil (no el WAR) y repite con `--wait 600`.

**`isolate · NO AISLADO` o `NO VERIFICADO`** — nada se levanta ni se explora hasta que esté en verde. Los mensajes dicen qué servicio tiene salida, a qué red se conectó, qué `dns:` o `cap_drop` falta, qué montaje sobra, o qué daemon es. Con `--live`, si Docker no está corriendo sale NO VERIFICADO: arráncalo. **`la sonda desde la red interna SÍ salió`**: el daemon no aísla esa red como el compose promete (Podman por socket compatible, `iptables: false`, un daemon que no es Docker en Linux); no hay verde posible ahí. **`un servicio del host escucha y es alcanzable desde la red interna (puertos …)`** es aviso: el host es la propia máquina, pero mientras el legacy corra nada que importe debe escuchar en `0.0.0.0` ahí (una base local, un proxy).

**`el ingress \`ingress\` no está en ejecución`** — el proxy se cayó (memoria, un error). `docker compose -f pepper-out/rehydrate/docker-compose.yml logs ingress`; tiene `restart: unless-stopped`, si no vuelve, `up -d ingress`.

**Los contenedores quedaron `Exited (255)` y `docker logs` no trae nada nuevo** — Docker murió (suspensión, actualización): el stream de logs se pierde aunque el contenedor reviva. `docker compose -f pepper-out/rehydrate/docker-compose.yml up -d --force-recreate` (los datos están en el volumen) y vuelve a verificar con `isolate --live`.

**La restauración reporta errores** — `transaction_timeout` y parecidos son un `SET` de un `pg_dump` moderno que un servidor viejo no reconoce; benignos. Errores de roles: el respaldo referencia dueños que no existen; `restore.sh` los crea sin login antes de restaurar (los saca del TOC del respaldo).

## Explorar

**`pepper explore` no entra (`login → rejected`)** — o el selector del campo/botón no es el del DOM (mira `map/screens.md`: `prependId=false` quita el prefijo del formulario), o la credencial no se fijó (`credentials.sql` falló: revisa el mensaje `credentials → error` en `explore.jsonl`; el encoder del app está en `code.md`, clase de login).

**`rol X: user_sql: la consulta no devolvió ninguna clave de usuario`** — la consulta de `roles[].user_sql` corrió en la base desechable y no devolvió filas. Revisa en `map/db.md` y `catalogs.md` la tabla de usuarios, la relación usuario-rol y la columna de activo; el agente escribe la consulta, nunca ve el valor.

**Todos los botones salen `(botón sin nombre)` en `flow.md`** — el framework les pone ids generados y el perfil no declara `http.action_fields`; el explorador igual los identifica por su texto en `explore.jsonl`.

**`filled_submit → rejected` con "el valor no es válido"** — el formulario se re-dibujó por un ajax después de llenar un select (cascadas de catálogos dependientes, o un campo que dispara una consulta). No es un fallo del sistema ni del explorador: es un rechazo de validación que queda registrado. Para un guardado coherente escribe un plan con valores del catálogo en el orden correcto.

**El explorador tarda** — cada pantalla con formulario cuesta ~1 min por rol (abrir, guardar vacío, llenar, guardar). Pon `submit: false` a los roles de consulta, deja los formularios a dos o tres roles, y acota con `--budget`.

**Necesito ver qué hace** — `--headed` abre el navegador; las capturas quedan en `evidence/<sid>/screens/` (no viajan en el paquete y el agente no las lee).

**`--observe` no abre nada / la persona cerró y no pasó nada** — `--observe` exige `--headed`. Al cerrar la ventana el núcleo espera `--settle` segundos, captura y escribe `session.json`; si la persona cerró solo una pestaña y quedó otra, la ventana sigue abierta.

## Correlacionar y descubrir

**`correlate: sin parser para las fuentes: X`** — `session.json` declara un colector cuyo `source` no tiene parser: ni es builtin (`http-proxy`, `explorer`) ni el perfil lo declara. Redacta el parser (skill `perfil-stack`) y decláralo en `profile.json`.

**`reduction.md` reporta líneas sin parsear** — la regex del parser no cubre esas líneas. Si son basura, documéntalo; si son eventos, amplía `line_pattern` o `continuation`.

**Eventos "sin asignar: ambiguo"** — peticiones concurrentes sin afinidad que las separe; el explorador va de una en una, así que esto pasa con ventanas de personas: una acción a la vez.

**`package: … fuera de lo autorizado; no se armó`** — el paquete trae algo que la autorización de datos no cubre (o no hay autorización): otra categoría, otra versión del legacy. Quedó `<paquete>.data-boundary.propuesta.json` con el alcance que haría falta. El agente se la muestra a la persona; **ella** la autoriza en su terminal: `python3 -m pepper authorize <propuesta> --by "<nombre>"` (pide escribir AUTORIZO), y el agente repite `package` con `--authorization pepper-out/data-boundary.json`.

**`pepper authorize: se corre a mano, en una terminal`** — se intentó autorizar desde un agente, un script o una tubería. La decisión es de una persona: ábrelo en tu terminal.

**El paquete no trae el respaldo / `No viaja` lista archivos** — es lo esperado: lo que el escáner no puede leer (binarios, archivos de más de 50 MB, codificación mixta) se queda en la máquina y el mapa ya lo leyó. `--include-uninspected` lo haría viajar entero y sin sustituir, con autorización por archivo; casi nunca hace falta.

**`authorize: … otro sistema o de otra versión del legacy`** — la autorización existente es de otros artefactos; no se mezcla. Si el legacy de verdad cambió, la persona borra `pepper-out/data-boundary.json` y decide de nuevo (la llave de seudónimos es por sistema: la del anterior no se reutiliza).

**`pepper explore` sale con 3 (PARCIAL) o 4 (INTERRUMPIDO)** — `session.json` → `outcome` dice por qué, y cada paso de `explore.jsonl` lleva `detail.falla`. PARCIAL: la evidencia sirve, pero ese recorrido no se describe como completo; cubre lo que faltó con otro plan. INTERRUMPIDO: repite con otro `--session` (o más `--budget`).

**`pepper explore: <plan> no se puede correr — paso N: …`** — el plan no declara lo que hace. Cada `click`/`click_at` lleva `"efecto": "modifica"` o `"consulta"`; lo que modifica lleva `"id"` y una comprobación posterior con `"comprueba": "<id>"`; `expect_rejected` dice qué rechazo esperaba. Declara por lo que el paso hace (míralo en `screens.md`: el botón y su acción), no por su texto.

**`explore: rol X: identidad no confirmada`** — el texto de `login.identity_text` no aparece tras entrar: o el sistema muestra otra cosa (el nombre y no la clave: ajusta el texto o usa `identity_route`), o entró otra identidad. No se explora con ese rol hasta que se pueda señalar quién es.

**`export · RECHAZADO`** — los errores dicen qué fuente no resuelve (`map:…` que no existe, event_id inexistente, archivo fuera del paquete), qué falta (desconocidos vacíos, sin `.md`, la sesión no está en `sessions`), o que la salida trae una credencial o un dato de persona con patrón (`output/funcional.md:LINEA`). El subagente corrige sobre la evidencia; nadie edita la salida a mano.

**El documento dice "observado" de algo que ninguna sesión ejecutó** — viola la regla 3 del skill `discovery-funcional`. Pídele al subagente que lo baje a `en_codigo`/`sustentada` y lo mande a la sección 12.

## Workspace

**`Unknown command: /pepper`** — Claude Code se abrió en otra carpeta; los comandos viven en `.claude/commands/` de la raíz del workspace.

**`pepper detect` ve `pom.xml` del fixture como si fuera del legacy** — estás encima del repo del legacy y el núcleo no reconoció la herramienta; verifica que exista `.claude/commands/pepper.md` (es el marcador).

**Playwright: `Executable doesn't exist`** — `python3 -m playwright install chromium`. Es una descarga neutral (el navegador), no toca el legacy.
