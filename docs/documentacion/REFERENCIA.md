# Referencia de PEPPER

> **Referencia detallada por fase. Para el camino feliz, ve a [`QUICKSTART.md`](QUICKSTART.md).**

Qué esperar de cada agente, cómo validar su output y qué hace el núcleo por debajo. La especificación técnica de cada fase (entradas, salidas, contratos) está en [`fases/`](fases/); la arquitectura en [`ARQUITECTURA.md`](ARQUITECTURA.md); los perfiles en [`PERFILES.md`](PERFILES.md).

## Índice

1. [Fase 0: Init](#1-fase-0-init)
2. [Fase 1: Inspect](#2-fase-1-inspect)
3. [Fase 2: Rehydrate](#3-fase-2-rehydrate)
4. [Fase 3: Observe](#4-fase-3-observe)
5. [Fase 4: Correlate](#5-fase-4-correlate)
6. [Fase 5: Discover](#6-fase-5-discover)
7. [Fase 6: Export y entrega a stark](#7-fase-6-export-y-entrega-a-stark)
8. [El núcleo a mano](#8-el-núcleo-a-mano)
9. [Glosario](#9-glosario)

### Tiempo estimado por fase

| Fase | Tiempo |
|---|---|
| Init | 5 minutos |
| Inspect | 20–60 minutos (más si hay que redactar perfil) |
| Rehydrate | 30 minutos a varias horas; depende de lo que falte |
| Observe | 10–20 minutos por flujo |
| Correlate | segundos (es código) |
| Discover | 20–60 minutos por flujo |
| Export | minutos |

---

## 1. Fase 0: Init

`/pepper-init [ruta]`. Sin subagente. Verifica `python3`, `jsonschema` y Docker; prepara `legacy/`, `evidence/`, `docs/pepper/`; corre `pepper detect` sobre los artefactos y te dice el escalón y el siguiente comando. No mueve tus artefactos: tú los pones en `legacy/`.

---

## 2. Fase 1: Inspect

```text
Use the inspector-legacy subagent to produce docs/pepper/stack-report.md
from the artifacts in legacy/
```

### Qué esperar

| Fase del agente | Qué hace | Tu trabajo |
|---|---|---|
| 1. Inventario | Lista y clasifica todo; mira dentro de WAR/JAR con `unzip -l` sin extraer. | Confirmar el alcance. |
| 2. Detección de perfil | `pepper detect`: ¿hay perfil validado aplicable? | Nada. |
| 3. Stack con evidencia | Lenguaje, frameworks, servidor, BD, versiones — cada uno con el archivo que lo prueba. | Corregir si sabes algo que los artefactos no dicen. |
| 4. Dependencias y faltantes | Datasources, servicios externos, certificados, variables; qué falta y qué lo resolvería. | Conseguir lo que falte, o decidir seguir sin ello. |
| 5. Borrador de perfil | Solo sin perfil: `profiles/<id>/` en `draft`, validado contra los schemas. | Revisar señales y receta. |
| 6. Reporte | `docs/pepper/stack-report.md` con veredicto. | Leerlo. |
| 7. Auto-validación y cierre | Checklists ✅/❌. | Aprobar explícitamente. |

### Cómo validar

- [ ] Cada versión del stack cita un archivo; ninguna dice "probablemente".
- [ ] Los faltantes dicen qué artefacto los resolvería y si bloquean.
- [ ] El veredicto es coherente: `BLOCKED` si falta algo de `required_inputs`.
- [ ] Si hay borrador de perfil, `python3 -m pepper validate profiles/<id>/profile.json` pasa y las señales de detección existen en tus artefactos.
- [ ] Ninguna credencial copiada; los hallazgos de seguridad van por ubicación.

---

## 3. Fase 2: Rehydrate

```text
Use the rehidratador-legacy subagent to produce docs/pepper/environment.json
from legacy/, docs/pepper/stack-report.md and the profile it names
```

### Qué esperar

| Fase del agente | Qué hace | Tu trabajo |
|---|---|---|
| 1. Lectura | Reporte + perfil; insumos presentes y ausentes. Si falta un `required_input` → `BLOCKED` directo. | Confirmar. |
| 2. Plan | `pepper-out/rehydrate/docker-compose.yml` con versiones fieles, datos, configuración real y observabilidad activada. | **Aprobar el plan antes de que levante nada.** ✋ |
| 3. Ejecución | `docker compose up -d`, logs de arranque, diagnóstico. | Esperar. |
| 4. Validación | Checks del perfil + genéricos; `pass`/`fail`/`skipped` con detalle. | Revisar los `fail`. |
| 5. Estado | `environment.json` (`READY`/`PARTIAL`/`BLOCKED`/`FAILED`), `validation.md`, `missing-evidence.md`. | Decidir sobre `PARTIAL`. |
| 6. Cierre | Cómo apagar; el entorno es desechable. | Aprobar el estado. |

### Cómo validar

- [ ] Las imágenes del compose tienen las versiones que Inspect documentó (o una desviación explícita).
- [ ] Nada inventado: cada datasource, usuario y puerto viene de un artefacto.
- [ ] `READY` solo si la aplicación responde, no solo si el contenedor está `running`.
- [ ] `python3 -m pepper validate docs/pepper/environment.json` pasa.
- [ ] Sin credenciales en `docs/pepper/`.

---

## 4. Fase 3: Observe

```text
Use the observador-runtime subagent to capture the flow "Registrar solicitud"
into evidence/<session_id>/
```

### Qué esperar

| Fase del agente | Qué hace | Tu trabajo |
|---|---|---|
| 1. Colectores | Inventario de fuentes: logs del perfil, BD, contenedores, proxy si lo hay. Dice si NO habrá `correlation_id`. | Confirmar. |
| 2. Preparación | Verifica que cada fuente emite ahora; define `session_id`. | Aprobar reconfiguraciones. ✋ |
| 3. Ventana | Marca inicio → **tú ejecutas el flujo** → marca fin; te pide qué hiciste. | Ejecutar el flujo, incluidos intentos fallidos. Describirlo. |
| 4. Captura | Copia el tramo de la ventana de cada fuente, sin filtrar. | Nada. |
| 5. `session.json` | Con zona horaria, colectores y `operator_note`; validado. | Nada. |
| 6. Cierre | Conteos por fuente. | Confirmar que la ventana cubrió el flujo. |

### Cómo validar

- [ ] `observed_start`/`observed_end` traen zona; `timezone` es la de las fuentes sin zona.
- [ ] Cada `collectors[].source` tiene parser (builtin `http-proxy` o del perfil).
- [ ] Cada archivo capturado tiene líneas dentro de la ventana.
- [ ] `operator_note` cuenta lo que hiciste, con los intentos fallidos.

---

## 5. Fase 4: Correlate

Sin subagente. `/pepper-correlate <session_id>` corre:

```bash
python3 -m pepper correlate evidence/<session_id> --out pepper-out/<session_id>/correlated
python3 -m pepper package pepper-out/<session_id>/correlated --legacy legacy/ --out pepper-out/<session_id>/package
```

Salida: `events.jsonl` (eventos normalizados), `flow.json` / `flow.md` (por petición, con la base de cada enlace), `reduction.md` (qué se descartó y por qué), y el paquete con `prompt.md`, `CLAUDE.md`, `AGENTS.md`, evidencia, legacy y `output/`.

### Cómo validar

- [ ] Líneas sin parsear = 0, o explicadas (basura real). Si no: parser del perfil.
- [ ] Ningún evento sin asignar "por ambigüedad" — o repites la observación con una petición a la vez.
- [ ] `flow.md` cuenta lo que hiciste: las peticiones, en orden, con sus resultados.
- [ ] `reduction.md` no descartó nada que importe (nunca descarta errores ni escrituras; revísalo igual).

---

## 6. Fase 5: Discover

```text
Use the descubridor-runtime subagent to produce
pepper-out/<session_id>/package/output/runtime-discovery.json and .md
```

### Qué esperar

| Fase del agente | Qué hace | Tu trabajo |
|---|---|---|
| 1. Lectura | `flow.md`, `events.jsonl`; reporta peticiones, sin asignar, si hay código. | Confirmar que es tu ventana. |
| 2–6. Análisis | Secuencia → componentes/datos/dependencias → reglas candidatas → runtime ↔ código → errores y desconocidos. Te avisa de lo importante en el momento. | Responder preguntas numeradas. |
| 7. Escritura | `runtime-discovery.md` sección por sección; itera contigo. | Revisar cada regla con su evidencia. |
| 8. Auto-validación | `pepper export --check` + checklist del skill. | Aprobar explícitamente. |

### Cómo validar

- [ ] Cada regla dice "parece", tiene confianza del vocabulario y evidencia; si hay código, `code_refs`.
- [ ] Ninguna dependencia listada sin evidencia de ejecución (el SMTP de la configuración no cuenta).
- [ ] Lo que la documentación afirma y el runtime no muestra está en contradicciones, no en reglas.
- [ ] Las ramas del código que el flujo no ejercitó están en desconocidos.
- [ ] Hay desconocidos (en un legacy siempre hay), redactados como "cuando pasa X, el sistema hace Y — ¿es a propósito?".

---

## 7. Fase 6: Export y entrega a stark

`/pepper-export <session_id>` corre `python3 -m pepper export … --out docs/pepper/discovery/<session_id>` y aplica las reglas del contrato: el JSON valida contra el schema; toda conclusión referencia evidencia declarada; toda evidencia resuelve a un `event_id` o a un `raw_ref` real; las confianzas están en el vocabulario; la sesión es la del paquete. Si algo falla, **no publica**: vuelves a Discover.

Publica: `runtime-discovery.json/.md`, `validation.md`, derivados (`flows.json`, `candidate-rules.json`, `contradictions.json`, `unknowns.json`, `evidence-map.json`) y copia de `events.jsonl` y `flow.json`.

### Entrega a stark

| Si existe | PEPPER hace |
|---|---|
| `docs/analysis/` | copia `runtime-discovery.md` como `docs/analysis/runtime-discovery-<session_id>.md`: input de `arqueologo-codigo` en reingeniería |
| `docs/REGLAS_DE_NEGOCIO.md` | **no lo edita**; te lista qué llevar a su sección 11 con el mapeo de confianza |

Mapeo de confianza (skill `evidencia-runtime` §3): `confirmada` y `fuertemente_sustentada` → `inferida` (código **y** runtime la respaldan); `candidata` → `inferida` con nota o pregunta abierta; `contradicha` → `en-duda`; `desconocida` → pregunta abierta. **Nada entra como `confirmada`**: eso exige una persona con nombre.

---

## 8. El núcleo a mano

```bash
python3 -m pepper detect <artefactos>/                       # qué perfil aplica, con qué señales
python3 -m pepper validate <archivo>... [--schema NOMBRE]     # contratos: profile, parser, session, environment, flow, event, runtime-discovery
python3 -m pepper correlate <evidencia>/ --out <dir> [--profile <id>] [--tolerance-ms 500]
python3 -m pepper package <correlated>/ --legacy <artefactos>/ --out <dir>
python3 -m pepper export <paquete>/ --check                   # solo validar
python3 -m pepper export <paquete>/ --out <dir>               # validar y publicar
python3 -m pepper demo                                        # correlate + package sobre examples/legacy-demo
python3 -m unittest discover -s tests                         # la suite
python3 scripts/verificar.py                                  # auto-verificación del framework
```

---

## 9. Glosario

- **Escalón de soporte**: 1 = hay perfil validado → pipeline completo; 2 = sin perfil pero el sistema corre → colectores genéricos; 3 = ni corre → inspección, faltantes y borrador de perfil.
- **Perfil**: todo el conocimiento de un stack como datos (`profiles/<id>/`): detección, receta de rehydrate, colectores, validaciones, parsers. `draft` o `validated`.
- **Parser declarativo**: JSON con una regex de grupos nombrados y reglas (tipo de evento, continuaciones, fusiones, ruido, afinidad). El núcleo lo interpreta; no hay código por stack.
- **Rehydrate**: reconstruir un entorno ejecutable y desechable del legacy en contenedores, fiel al original. Estados `READY`, `PARTIAL`, `BLOCKED`, `FAILED`.
- **Ventana**: el intervalo entre "inicio" y "fin" que el humano marca mientras ejecuta un flujo. Todo evento capturado se etiqueta con su `session_id`.
- **Evento normalizado**: la forma común de toda evidencia (`schemas/event.schema.json`): timestamp con zona, fuente, componente, tipo, operación, mensaje, `raw_ref` a la línea cruda, `correlation_id` **observado** y, aparte, el inferido con su base.
- **Base de correlación**: qué sustenta que un evento pertenezca a una petición: `correlation_id` explícito (el proxy lo inyecta) > afinidad (`thread`, `pid`) > ventana temporal.
- **Evidencia protegida**: lo que la reducción nunca descarta: errores, excepciones, escrituras a BD, respuestas HTTP ≥ 400.
- **Paquete controlado**: la carpeta autocontenida sobre la que trabaja el agente de discovery: evidencia correlacionada, legacy, prompt, adaptadores `CLAUDE.md`/`AGENTS.md`, `output/`.
- **Regla candidata**: una regla de negocio formulada con "parece", con confianza (`confirmada`, `fuertemente_sustentada`, `candidata`, `desconocida`, `contradicha`) y evidencia.
- **Contradicción**: lo que el código o la documentación afirman y el runtime desmiente (o viceversa), con causas posibles; siempre para validación humana.
- **Desconocido**: lo que la evidencia no alcanza a determinar, con por qué y qué observar.
- **Modo contraste**: dos agentes sobre el mismo paquete, salidas comparadas; coincidencia sube confianza, discrepancia genera revisión.
- **Procedencia (stark)**: `confirmada` / `inferida` / `en-duda`. PEPPER entrega como máximo `inferida`.
