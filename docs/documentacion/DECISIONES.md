# Decisiones de diseño de PEPPER

> ADR que congela las decisiones detrás de PEPPER. Fuente de verdad sobre el **porqué** de la arquitectura, los agentes y el núcleo. Si un artefacto diverge de este documento, este documento manda hasta que se actualice. Formato: **Decisión · Por qué · Consecuencia aceptada**.

La visión original completa está en [`VISION.md`](VISION.md); este documento registra lo que se decidió al construirla y dónde se corrigió.

## D1. Dos capas: agentes por fase + núcleo determinístico

- **Decisión**: PEPPER tiene la forma de stark — comandos `/pepper-*`, subagentes y skills en `.claude/` — y además un núcleo en Python (`pepper/`) que hace lo mecánico: parsear, reducir, correlacionar, empaquetar, validar. Los agentes llaman al núcleo cuando algo debe ser repetible.
- **Por qué**: un agente leyendo gigabytes de logs "a ojo" es lento, caro y no reproducible. La visión lo pedía explícitamente: "primero debe existir una capa determinística de reducción y correlación". Y la forma de stark es lo que hace que el flujo sea ejecutable fase por fase con gates humanos.
- **Consecuencia aceptada**: dos cosas que mantener (markdown de agentes y código). Se mitiga con contratos JSON Schema entre ambas y con `scripts/verificar.py` + tests en CI.

## D2. El núcleo no conoce tecnologías; los perfiles son datos

- **Decisión**: todo lo específico de un stack vive en `profiles/<id>/` como JSON validado contra `profile.schema.json`. Los parsers de logs son declarativos (`parser.schema.json`): una regex con grupos nombrados más reglas, interpretadas por `PatternParser`. El núcleo solo sabe SQL (porque SQL es SQL).
- **Por qué**: el requisito duro es "llegan legacies de todo". Con plugins en Python, cada stack sería código nuevo en el núcleo; con datos, un stack nuevo es un archivo que además el agente puede redactar durante Inspect.
- **Consecuencia aceptada**: el DSL de parsers tiene un techo. Formatos verdaderamente exóticos (binarios, multilínea irregular) necesitarán extender el DSL de forma genérica — nunca un `if wildfly` en el núcleo.

## D3. Escalera de soporte en vez de "soportado / no soportado"

- **Decisión**: tres escalones: 1 = perfil validado → pipeline completo; 2 = sin perfil pero el sistema corre → colectores genéricos; 3 = ni corre → inspección con faltantes y borrador de perfil. Cada legacy nuevo sin perfil produce un borrador que, validado, sube al siguiente legacy de ese stack al escalón 1.
- **Por qué**: acotar la herramienta a un stack la mataba en el primer legacy raro. Acotar solo lo que se automatiza primero — y convertir la diversidad en el mecanismo de crecimiento — no.
- **Consecuencia aceptada**: el escalón 2 da menos profundidad (sin logs de aplicación parseados, sin `correlation_id` si no hay proxy). Se dice explícitamente en `session.json` y en las confianzas del discovery.

## D4. Rehydrate existe porque habilita la observabilidad, no solo por revivir

- **Decisión**: la receta de rehydrate activa la observabilidad **antes del arranque**: `log_statement=all`, niveles DEBUG, proxy delante del puerto.
- **Por qué**: en un entorno reconstruido y desechable se puede observar con una fidelidad imposible en producción. Esa es la justificación técnica de la fase más cara del proyecto, y la razón por la que Rehydrate y Observe se potencian.
- **Consecuencia aceptada**: la evidencia de un entorno rehidratado es más rica que la de producción; comparar ambas exige tenerlo presente.

## D5. Lo observado y lo inferido nunca se mezclan

- **Decisión**: `event.correlation_id` es solo lo que la fuente emitió. Lo que el correlacionador infiere va en `metadata.inferred_correlation_id` con `metadata.correlation_basis` (`correlation_id` explícito > afinidad > ventana temporal). Lo ambiguo queda `unassigned` con la razón.
- **Por qué**: el agente debe saber qué tan firme es cada enlace para fijar confianzas; un `correlation_id` "rellenado" mentiría.
- **Consecuencia aceptada**: los eventos son más verbosos y el agente tiene que leer dos campos. Es el precio de no adivinar.

## D6. El SQL nunca se deduplica

- **Decisión**: la reducción deduplica solo eventos `log` idénticos y consecutivos en la misma fuente, comparando metadata completa. Nunca SQL, nunca evidencia protegida.
- **Por qué**: la primera corrida sobre el fixture eliminó el segundo `SELECT … WHERE id = $1` porque el texto era idéntico al primero — con parámetros distintos (1003 vs 1001). Dos consultas iguales con parámetros distintos, o un patrón N+1, son evidencia, no ruido. Quedó como test de regresión.
- **Consecuencia aceptada**: un flujo con miles de SQL idénticos produce miles de eventos. Preferible a esconder un N+1.

## D7. Evidencia protegida

- **Decisión**: la reducción nunca descarta: severidad `warn`/`error`/`fatal`, excepciones, escrituras a base de datos, respuestas HTTP ≥ 400. Fuera de la ventana, se conservan marcadas con `outside_window`.
- **Por qué**: el rechazo de una petición es la evidencia más valiosa de una regla de negocio (dice qué condición se exige). Una regla de ruido demasiado amplia no puede tener la oportunidad de tragárselo.
- **Consecuencia aceptada**: algo de ruido con severidad alta sobrevive (warnings repetitivos del framework). El agente lo reconoce; `reduction.md` lo hace visible.

## D8. Reducción determinística y auditada

- **Decisión**: misma evidencia cruda → mismos bytes de salida (test). Cada descarte se registra en `reduction.md` con su regla y su `raw_ref`.
- **Por qué**: la reducción es la parte con más riesgo de esconder evidencia. Si no es repetible y auditable, el discovery hereda un sesgo invisible.
- **Consecuencia aceptada**: sin timestamps de generación ni aleatoriedad en las salidas; los cambios al núcleo cambian bytes y rompen el test de determinismo a propósito.

## D9. El agente es intercambiable; el paquete es la interfaz

- **Decisión**: Discover trabaja sobre una carpeta autocontenida con `prompt.md` (el skill `discovery-runtime` sin frontmatter), `CLAUDE.md` y `AGENTS.md` apuntando a él, evidencia, legacy, schema y `output/`. Sin APIs por agente. La salida valida contra el mismo schema venga de Claude Code o de Codex.
- **Por qué**: el requisito de usar ambos agentes, y el bonus del modo contraste: dos análisis independientes sobre la misma evidencia.
- **Consecuencia aceptada**: el paquete copia el legacy (código, configuración, docs) y la evidencia cruda completa. Extraer solo los tramos referenciados queda pendiente para cuando los logs pesen.

## D10. Export es un gate de máquina; no publica lo inválido

- **Decisión**: `pepper export` valida el JSON contra el schema y las reglas de PEPPER (toda conclusión con evidencia declarada; toda evidencia resuelve a `event_id` o `raw_ref` real; sesión correcta). Si falla, escribe `validation.md` y no publica. Nunca corrige la salida.
- **Por qué**: "cada inferencia apunta a evidencia" es el criterio de éxito del MVP; si es una intención y no un check, se erosiona en el tercer flujo.
- **Consecuencia aceptada**: un discovery con un solo ID mal escrito se rechaza entero. El agente lo corrige con `--check` antes de entregar.

## D11. PEPPER entrega a stark como `inferida`, nunca `confirmada` (corrección a la visión)

- **Decisión**: el mapeo de confianzas a la procedencia de stark es: `confirmada` y `fuertemente_sustentada` → `inferida`; `candidata` → `inferida` con nota o pregunta abierta; `contradicha` → `en-duda`; `desconocida` → pregunta abierta (sección 11 de `REGLAS_DE_NEGOCIO.md`). La visión original (§25) proponía `confirmada` → `confirmada`.
- **Por qué**: en stark, `confirmada` significa que una persona con nombre respondió por la regla. PEPPER aporta evidencia de ejecución, no personas. Mapearlo a `confirmada` habría hecho que un bug fosilizado que "corre así" entrara al Regression Shield con el peso de una regla de verdad — exactamente lo que stark B-D11 existe para evitar.
- **Consecuencia aceptada**: el valor de PEPPER en stark es hacer más fuerte lo `inferida` (código **y** runtime) y mejores las preguntas abiertas (el runtime es literalmente "cuando pasa X, el sistema hace Y"). La promoción sigue siendo humana.

## D12. Un workspace por legacy; la herramienta se ignora, el producto se commitea

- **Decisión**: PEPPER se instala de dos formas, igual que stark: como **workspace** (clon con los artefactos en `legacy/`) o **encima del repo** del legacy (la herramienta copiada y gitignoreada; los artefactos son el repo mismo). En ambas, la herramienta (`.claude/`, `pepper/`, `schemas/`, `profiles/`, `docs/documentacion/`, `CLAUDE.md`, `AGENTS.md`) se ignora en git; el producto (`docs/pepper/`, `docs/analysis/runtime-discovery-*.md`) se commitea; `legacy/`, `evidence/` y `pepper-out/` nunca. `pepper detect` y `pepper package` reconocen cuándo están encima de un repo (marcador `.claude/commands/pepper-init.md`) y excluyen su propia herramienta. Al terminar, se borra PEPPER, se instala stark y `arqueologo-codigo` encuentra el discovery en `docs/analysis/`.
- **Por qué**: el flujo real es secuencial — PEPPER primero, stark después, sobre el mismo repo — y el repo del proyecto debe quedar solo con lo que se produjo, sin herramienta. Los artefactos y la evidencia son datos ajenos, a menudo sensibles; el producto es el conocimiento estructurado.
- **Consecuencia aceptada**: un perfil redactado en un proyecto queda del lado de la herramienta; hay que llevarlo a mano al repo de PEPPER para que sirva al siguiente legacy. Es una copia de carpeta.

## D13. PEPPER no sella; stark sella

- **Decisión**: PEPPER no implementa receipts ni tags. Sus gates son humanos (✋ en cada fase) y uno de máquina (Export). La aprobación con sello ocurre en stark, cuando el conocimiento entra a specs.
- **Por qué**: los sellos de stark existen para demostrar que lo validado es lo entregado en código. PEPPER entrega evidencia estructurada para que alguien valide; duplicar la mecánica sería sobre-ingeniería (Principio 1).
- **Consecuencia aceptada**: la trazabilidad de PEPPER es interna al artefacto (IDs de evidencia → líneas crudas), no criptográfica.

## D14. La observación es manual y de un flujo a la vez

- **Decisión**: el humano marca inicio y fin y ejecuta el flujo; el agente prepara colectores y captura. Un flujo por sesión. Automatizar la ejecución del flujo está fuera del MVP.
- **Por qué**: la unidad de conocimiento es un flujo funcional; ejecutarlo requiere criterio de negocio (qué datos, qué caso); y dos peticiones concurrentes degradan la correlación cuando la fuente no tiene afinidad.
- **Consecuencia aceptada**: entender un sistema grande son muchas sesiones. Es el mismo trade-off que un lote por sesión en stark.

## D15. Evidencia sintética en el fixture, marcada como tal

- **Decisión**: `examples/legacy-demo/raw-evidence/` es evidencia construida a mano imitando el formato real de cada fuente, marcada `synthetic: true` en `session.json` y en el README del paquete. El entorno de referencia (Docker) que la produciría de verdad está escrito pero sin verificar.
- **Por qué**: permitió probar Correlate, Package, Export y Discover de punta a punta sin Docker ni red. Ocultar que es sintética habría sido la primera violación del principio 2.
- **Consecuencia aceptada**: la primera captura real puede diferir en forma; cuando ocurra, la sintética (o los parsers) se corrigen para imitar a la real, no al revés.

## D16. Python 3.9, biblioteca estándar, `jsonschema` opcional

- **Decisión**: el núcleo corre con el Python que trae macOS, sin dependencias; `jsonschema` (opcional) habilita la validación de contratos; `pyyaml` solo para `verificar.py`.
- **Por qué**: "sin instalar nada" es lo que hace que otro dev lo pruebe en cinco minutos. Y stark ya exige Python 3.9+.
- **Consecuencia aceptada**: sin `jsonschema`, Export avisa que no validó la forma del JSON. Se instala con `pip install -r requirements-dev.txt`.

## D17. El artefacto dicta el ambiente (corrección tras el primer legacy real)

- **Decisión**: Rehydrate fabrica el ambiente que el artefacto espera a partir de lo que trae hardcodeado (perfiles de configuración embebidos, descriptores, MANIFEST): red con el subnet de las IPs, base en esa IP con ese nombre y ese rol, respaldo restaurado ahí, artefacto arrancado con el perfil completo. Los hosts externos se resuelven a un stub que registra peticiones. `BLOCKED` queda solo para "sin artefacto", "sin respaldo restaurable" o "el artefacto no dice a qué conectarse".
- **Por qué**: en el primer legacy real (un WAR de Spring Boot y un dump, sin más) el inspector encontró seis perfiles de configuración dentro del WAR — uno completo, con host, base, usuario y contraseña — y aun así declaró `BLOCKED` porque "faltaba la configuración de producción". La regla "no inventar insumos" se había aplicado a *reproducir lo que el artefacto ya dice*. Ese caso — WAR + dump y nada más — es el propósito de PEPPER, no su excepción.
- **Consecuencia aceptada**: el entorno reproduce la configuración de un perfil que puede no ser el de producción (p. ej. `genesi` en vez de `prod`); se declara cuál se usó y qué difiere. Y el stub convierte cada llamada externa en un error rápido: los flujos que dependen de esos servicios se observan solo hasta ese punto.

## D18. La nota del humano es evidencia de primera clase

- **Decisión**: `/pepper-init` deja `legacy/NOTAS.md` (desde `templates/NOTAS-LEGACY.md`) para que el humano escriba lo que sabe del sistema: servidor y versión de producción, base, cómo arranca, servicios externos, cómo se entra, flujos que importan, quién sabe. El inspector lo lee **primero** y lo cita como cualquier fuente (`NOTAS.md:12`); si contradice a los artefactos, reporta la discrepancia.
- **Por qué**: en el primer legacy real, el WAR era un ejecutable de Spring Boot y media hora se fue en hacerlo arrancar con `java -jar` — un modo que producción nunca usó. "Producción es WildFly 21" en una línea habría evitado todo. Los artefactos lo sugerían (`jboss-web.xml`), pero la nota lo decide.
- **Consecuencia aceptada**: la nota puede estar equivocada (la del primer legacy dijo PostgreSQL 16; el respaldo declara servidor 10.6). Por eso es evidencia citable, no verdad revelada: las discrepancias quedan escritas, no resueltas.

## D19. El entorno rehidratado no tiene salida: red interna, stub y servidores foráneos re-apuntados

- **Decisión**: la red del compose es `internal: true`; el puerto del app se publica mediante un proxy de entrada (`ingress`, donde vivirá el proxy HTTP de PEPPER). Todo host externo que el artefacto invoque se resuelve por alias DNS al stub. La receta de restauración re-apunta al stub cada servidor de `pg_foreign_server` (`dblink`, `postgres_fdw`).
- **Por qué**: en el primer legacy real, una vista con `dblink` consultó la base **de producción** a través de la VPN de la máquina, con la contraseña que viajaba en el `USER MAPPING` del respaldo. Una lectura, pero producción. "Nunca llamar servicios reales" no puede depender de que alguien recuerde cada alias: tiene que ser imposible por construcción.
- **Consecuencia aceptada**: nada en el entorno puede alcanzar internet (ni un `apt-get` en un contenedor). Las imágenes se bajan por el daemon, no por la red interna, así que Rehydrate sigue funcionando.

## D20. El contenedor que los descriptores indican, no el que el MANIFEST permite

- **Decisión**: si el artefacto trae descriptores de un servidor de aplicaciones (`jboss-web.xml`, `weblogic.xml`, `ibm-web-bnd.xml`, `context.xml`), Rehydrate lo despliega en ese servidor aunque el MANIFEST declare un `Main-Class` ejecutable. `java -jar` se usa solo cuando no hay descriptores o la nota lo confirma.
- **Por qué**: el primer legacy real era un WAR de Spring Boot con Tomcat embebido que **no arranca** con `java -jar` (JoinFaces no escanea `war:file:`; el Tomcat 8.5.11 embebido tiene un NPE en JASPIC; dos jars de API sin código sombrean a Mojarra según el orden del zip). En WildFly 21 arrancó limpio y sin modificar el WAR, porque el servidor pone sus APIs por encima de `WEB-INF/lib`.
- **Consecuencia aceptada**: hay que tener imágenes de esos servidores en las versiones del legacy, y sus logs tienen otro formato (`server.log` de WildFly vs Spring Boot puro): el perfil declara ambos parsers.
