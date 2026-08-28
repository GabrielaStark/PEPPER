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

- **Decisión**: PEPPER se clona como workspace de un legacy. `legacy/` (artefactos) y `evidence/` (capturas) nunca se versionan; `pepper-out/` tampoco; `docs/pepper/` y los perfiles nuevos sí. Igual que stark, la herramienta (`.claude/`, `pepper/`, `schemas/`, `docs/documentacion/`, `examples/`, `tests/`) se gitignorea en el workspace.
- **Por qué**: los artefactos y la evidencia son datos ajenos, a menudo sensibles; el producto es el conocimiento estructurado. Y actualizar PEPPER es recopiar la herramienta encima.
- **Consecuencia aceptada**: un perfil redactado en un workspace hay que llevarlo a mano al repo de PEPPER para que sirva al siguiente legacy. Es una copia de carpeta.

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
