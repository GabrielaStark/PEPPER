# Troubleshooting de PEPPER

Problemas que salen y qué hacer. El detalle de cada fase está en [`REFERENCIA.md`](REFERENCIA.md).

## Levantar

**`rehydrate · BLOCKED · ningún perfil de configuración dentro del artefacto trae url, usuario y contraseña del datasource`** — el artefacto no dice a qué conectarse. Consigue la configuración externa del ambiente (el `application-*.yml`, el `standalone.xml` con el datasource) y ponla en `legacy/`; o escribe en `NOTAS.md` host, base y usuario y pide el perfil que los lea.

**`BLOCKED · el artefacto no trae descriptor de servidor y NOTAS.md no dice en qué corre`** — una línea en `legacy/NOTAS.md` ("producción es WildFly 21") resuelve. El perfil declara qué imagen corresponde a cada versión (`rehydrate.server_images`); si la versión no está, se usa la más cercana y se declara como desviación.

**`FAILED · el servidor de aplicaciones arrancó: sin señal de arranque en 300 s`** — `docker compose -f pepper-out/rehydrate/docker-compose.yml logs app`. Lo típico: la imagen del servidor no es la que el WAR necesita (APIs javax vs jakarta), o el WAR espera un archivo/ruta que no existe. Corrige el perfil (no el WAR) y repite con `--wait 600`.

**`isolate · NO AISLADO` o `NO VERIFICADO`** — nada se levanta ni se explora hasta que esté en verde. Los mensajes dicen qué servicio tiene salida, a qué red se conectó, qué `dns:` falta o qué montaje sobra. Con `--live`, si Docker no está corriendo sale NO VERIFICADO: arráncalo.

**Los contenedores quedaron `Exited (255)` y `docker logs` no trae nada nuevo** — Docker murió (suspensión, actualización): el stream de logs se pierde aunque el contenedor reviva. `docker compose -f pepper-out/rehydrate/docker-compose.yml up -d --force-recreate` (los datos están en el volumen) y vuelve a verificar con `isolate --live`.

**La restauración reporta errores** — `transaction_timeout` y parecidos son un `SET` de un `pg_dump` moderno que un servidor viejo no reconoce; benignos. Errores de roles: el respaldo referencia dueños que no existen; `restore.sh` los crea sin login antes de restaurar (los saca del TOC del respaldo).

## Explorar

**`pepper explore` no entra (`login → rejected`)** — o el selector del campo/botón no es el del DOM (mira `map/screens.md`: `prependId=false` quita el prefijo del formulario), o la credencial no se fijó (`credentials.sql` falló: revisa el mensaje `credentials → error` en `explore.jsonl`; el encoder del app está en `code.md`, clase de login).

**Todos los botones salen `(botón sin nombre)` en `flow.md`** — el framework les pone ids generados y el perfil no declara `http.action_fields`; el explorador igual los identifica por su texto en `explore.jsonl`.

**`filled_submit → rejected` con "el valor no es válido"** — el formulario se re-dibujó por un ajax después de llenar un select (cascadas estado → municipio → colonia, o una CURP que dispara una consulta). No es un fallo del sistema ni del explorador: es un rechazo de validación que queda registrado. Para un guardado coherente escribe un plan con valores del catálogo en el orden correcto.

**El explorador tarda** — cada pantalla con formulario cuesta ~1 min por rol (abrir, guardar vacío, llenar, guardar). Pon `submit: false` a los roles de consulta y deja los formularios a dos o tres roles.

**Necesito ver qué hace** — `--headed` abre el navegador; las capturas quedan en `evidence/<sid>/screens/`.

## Correlacionar y descubrir

**`correlate: sin parser para las fuentes: X`** — `session.json` declara un colector cuyo `source` no tiene parser: ni es builtin (`http-proxy`, `explorer`) ni el perfil lo declara. Redacta el parser (skill `perfil-stack`) y decláralo en `profile.json`.

**`reduction.md` reporta líneas sin parsear** — la regex del parser no cubre esas líneas. Si son basura, documéntalo; si son eventos, amplía `line_pattern` o `continuation`.

**Eventos "sin asignar: ambiguo"** — peticiones concurrentes sin afinidad que las separe; el explorador va de una en una, así que esto pasa con ventanas de personas: una acción a la vez.

**`package: … fuera de lo autorizado; no se armó`** — el paquete trae algo que la autorización de datos no cubre (o no hay autorización): otra categoría, un archivo no inspeccionable nuevo o cambiado, otra versión del legacy. Quedó `<paquete>.data-boundary.propuesta.json` con el alcance que haría falta. Se la muestras a la persona; solo con su sí y su nombre: `pepper authorize <propuesta> --by "<nombre>"` y repites con `--authorization pepper-out/data-boundary.json`. No la escribas a mano.

**`authorize: … otro sistema o de otra versión del legacy`** — la autorización existente es de otros artefactos; no se mezcla. Si el legacy de verdad cambió, la persona borra `pepper-out/data-boundary.json` y decide de nuevo.

**`pepper explore` sale con 3 (PARCIAL) o 4 (INTERRUMPIDO)** — `session.json` → `outcome` dice por qué, y cada paso de `explore.jsonl` lleva `detail.falla`. PARCIAL: la evidencia sirve, pero ese recorrido no se describe como completo; cubre lo que faltó con otro plan. INTERRUMPIDO: repite con otro `--session` (o más `--budget`).

**`explore: rol X: identidad no confirmada`** — el texto de `login.identity_text` no aparece tras entrar: o el sistema muestra otra cosa (el nombre y no la clave: ajusta el texto o usa `identity_route`), o entró otra identidad. No se explora con ese rol hasta que se pueda señalar quién es.

**`export · RECHAZADO`** — los errores dicen qué fuente no resuelve (`map:…` que no existe, event_id inexistente, archivo fuera del paquete) o qué falta (desconocidos vacíos, sin `.md`, la sesión no está en `sessions`). El subagente corrige sobre la evidencia; nadie edita la salida a mano.

**El documento dice "observado" de algo que ninguna sesión ejecutó** — viola la regla 3 del skill `discovery-funcional`. Pídele al subagente que lo baje a `en_codigo`/`sustentada` y lo mande a la sección 12.

## Workspace

**`Unknown command: /pepper`** — Claude Code se abrió en otra carpeta; los comandos viven en `.claude/commands/` de la raíz del workspace.

**`pepper detect` ve `pom.xml` del fixture como si fuera del legacy** — estás encima del repo del legacy y el núcleo no reconoció la herramienta; verifica que exista `.claude/commands/pepper.md` (es el marcador).

**Playwright: `Executable doesn't exist`** — `python3 -m playwright install chromium`. Es una descarga neutral (el navegador), no toca el legacy.
