# Troubleshooting de PEPPER

Problemas que salen y qué hacer. Si algo no está aquí, el detalle de cada fase está en [`REFERENCIA.md`](REFERENCIA.md).

## Núcleo

**`pepper correlate: sin parser para las fuentes: X`** — `session.json` declara un colector cuyo `source` no tiene parser: ni es el builtin `http-proxy` ni el perfil lo declara con `parser`. Redacta el parser declarativo (skill `perfil-stack`), declara el colector en `profile.json` y repite. No edites la evidencia para "quitar" la fuente.

**`reduction.md` reporta líneas sin parsear** — la regex del parser no cubre esas líneas. Míralas: si son basura (banners, líneas vacías con formato raro), documéntalo; si son eventos reales, amplía `line_pattern` o agrega `continuation`. Objetivo: 0 o explicadas.

**Eventos "sin asignar: ambiguo, N peticiones concurrentes"** — dos peticiones se traslaparon y la fuente no tiene afinidad (`thread`, `pid`) que las separe. Opciones: declarar `affinity` en el parser si la fuente sí trae un identificador; o repetir la observación ejecutando una acción a la vez. Nunca asignes a mano.

**0 peticiones en `flow.json`** — no hubo colector HTTP con `correlation_id`. La correlación se hizo solo por afinidad y ventana temporal; es válida pero más débil, y el discovery debe reflejarlo en confianzas más bajas. La solución de fondo es un proxy delante de la aplicación (pendiente en el núcleo; mientras tanto, un `http.jsonl` generado desde el access log del servidor con un parser).

**Las fuentes no se alinean en el tiempo** — una fuente trae hora local sin zona y `session.json` declara otra `timezone`. Corrige `timezone` (es la que se aplica a las fuentes que no traen zona) y repite Correlate. Si dos fuentes están en zonas distintas, la que difiere necesita `%z` en su `timestamp.format`.

**`pepper export: RECHAZADO · referencia a evidencia inexistente`** — el agente citó un ID que no declaró en `evidence`, o un `raw_ref` que no existe. Vuelve a `/pepper-discover`: el agente corrige; tú no editas el JSON.

**`pepper export: RECHAZADO · candidate_rules/0/evidence: [] is too short`** — una conclusión sin evidencia. Misma respuesta: al agente. Si no puede señalar evidencia, la conclusión va a desconocidos.

**`falta jsonschema`** — `pip install -r requirements-dev.txt`. Sin él todo corre, pero nada valida contra los contratos; Export lo avisa.

**`pepper package: el directorio del paquete ya existe y no está vacío`** — hay un discovery anterior en `output/`. Muévelo o bórralo a propósito; PEPPER no pisa paquetes.

## Agentes

**Inspect dice "desconocida" en todas las versiones** — los artefactos no las contienen. Es correcto. Consigue notas del servidor original, el `MANIFEST.MF`, un `pg_dump` con cabecera, o pregúntale a quien lo operaba — y dáselo al agente.

**Rehydrate termina en `BLOCKED`** — es un entregable: `docs/pepper/missing-evidence.md` dice qué falta y qué artefacto lo resolvería. Consíguelo y repite. No le pidas al agente que "invente algo para que arranque".

**Rehydrate termina en `FAILED`** — era viable pero algo falló técnicamente (imagen inexistente para esa versión, script de restauración roto). El agente debe decir qué. Frecuente: la versión exacta no existe como imagen — decide con el humano la más cercana y regístrala como desviación.

**`ClassFormatError: Absent Code attribute in method that is not native or abstract`** — el artefacto trae en `WEB-INF/lib` un jar de API de compilación sin código (`javaee-api-*.jar`, `jsf-api-*.jar`) que sombrea a la implementación real; cuál gana depende del orden del zip. En un servidor de aplicaciones real no importa (sus APIs ganan): despliega ahí (D20). Si de verdad corre con `java -jar` en producción, la copia de trabajo del WAR — nunca `legacy/` — se reordena o se le quitan los stubs, y se documenta como hallazgo del legacy.

**`ReflectionsException: could not create Vfs.Dir from url … [war:file:…]`** — JoinFaces (o cualquier escáner basado en Reflections) no sabe leer un WAR ejecutable con `java -jar`. Ese artefacto se despliega en su servidor (D20).

**`isolate · NO AISLADO`** — el reporte dice qué servicio y por qué red. Lo típico: una red sin `internal: true`, un servicio sin `networks:` (cae en `default`, que tiene salida), un `extra_hosts` apuntando a una IP real, o un host externo del artefacto sin alias al stub. Corrige el compose y repite; no levantes nada mientras esté en rojo.

**`isolate` no puede resolver el compose** — necesita `docker compose config` (o `pyyaml` como respaldo). Verifica que Docker esté instalado; el daemon no hace falta para la comprobación estática, solo para `--live`.

**Una vista o función alcanzó una base de producción** — el respaldo trae `pg_foreign_server` / `USER MAPPING` con host y contraseña reales, y la máquina tiene VPN. La receta re-apunta los servidores foráneos al stub y la red es `internal` (D19); si lo ves en un compose viejo, `docker compose down` y regenera.

**Contenedor `running`, aplicación sin responder** — por eso `READY` exige validaciones, no solo `docker ps`. Mira los logs de arranque: deployment fallido, datasource sin driver, puerto equivocado.

**El agente de discovery listó el SMTP como dependencia** — lo leyó en la configuración, no lo vio ejecutarse. Es la trampa clásica y viola el skill `discovery-runtime` regla 6. Pídele que lo mueva a contradicción (si la documentación lo afirma) o a desconocido.

**El agente describió una rama del código como observada** — el flujo no la ejercitó. Va a desconocidos, con la recomendación de qué observar.

**El agente "corrigió" una contradicción** — las contradicciones no se resuelven solas: se reportan con causas posibles y las decide el humano.

## Workspace

**¿Puedo observar producción directamente (escalón 2)?** — Técnicamente sí, si tienes acceso a sus logs. Dos límites: no podrás activar la observabilidad agresiva (`log_statement=all`, DEBUG) que un entorno rehidratado permite, y la evidencia traerá datos reales de personas — `evidence/` no se versiona y se trata como sensible. Si puedes rehidratar, rehidrata.

**Tengo un WAR pero no el código fuente** — Inspect y Rehydrate funcionan con el WAR. Discover trabajará solo con evidencia y documentación: sin `code_refs` ni comparación runtime ↔ código, pero con reglas candidatas y contradicciones contra la documentación.

**Tengo un stack sin perfil y sin tiempo de redactarlo** — escalón 2 o 3. Los colectores genéricos (logs de contenedores, log de BD) funcionan sin perfil; lo que pierdes es el parser de los logs de aplicación. Redactar el parser (una regex) suele tardar menos que pelear sin él.

**¿Puedo poner PEPPER encima del repo del legacy en vez de clonar un workspace?** — Sí; es el modo pensado para seguir con stark en el mismo repo: copias la herramienta gitignoreada (comando en el README), inspeccionas con `/pepper-inspect .`, y al terminar borras la herramienta e instalas stark. `pepper detect .` y `pepper package --legacy .` excluyen la herramienta solos.

**¿Se commitea `pepper-out/`?** — No. Ni `legacy/` ni `evidence/`. Solo `docs/pepper/` y los perfiles nuevos. `.gitignore` ya lo trae.

**`verificar.py` marca "nombre citado inexistente"** — escribiste un nombre de comando, agente o skill que no existe en disco (o con errata). Es a propósito: los nombres que se citan en la documentación deben existir.
