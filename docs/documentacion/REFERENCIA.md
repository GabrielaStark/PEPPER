# Referencia de PEPPER

> **Referencia detallada por fase. Para el camino feliz, ve a [`QUICKSTART.md`](QUICKSTART.md).**

Qué esperar de cada agente, cómo validar su output y qué hace el núcleo por debajo. La arquitectura está en [`ARQUITECTURA.md`](ARQUITECTURA.md); los perfiles en [`PERFILES.md`](PERFILES.md); los contratos en `schemas/`.

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

`/pepper-init [ruta]`. Sin subagente. Verifica `python3`, `jsonschema` y Docker; prepara `legacy/`, `evidence/`, `docs/pepper/`; corre `pepper detect` sobre los artefactos y te dice el escalón y el siguiente comando. No mueve tus artefactos: tú los pones en `legacy/` — o, si PEPPER está instalado encima del repo del legacy, los artefactos son el repo mismo y se inspecciona con `/pepper-inspect .`.

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
| 3.5 Mapa del sistema | `pepper map`: rutas, jobs, pantallas, clases, tablas, catálogos, triggers → `docs/pepper/system-map.json` + `map/`. | Hojear `map/catalogs.md` y `map/screens.md`: es lo que el discovery va a usar. |
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
- [ ] El mapa dice `COMPLETO`; si no, los huecos están explicados. `map/catalogs.md` no trae datos de personas ni contraseñas.

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
| 2. Plan | `pepper-out/rehydrate/docker-compose.yml` con versiones fieles, datos, configuración real y observabilidad activada. **Verifica el aislamiento con `pepper isolate` antes de presentarlo.** | **Aprobar el plan antes de que levante nada.** ✋ |
| 3. Ejecución | `docker compose up -d`, logs de arranque, diagnóstico, y `pepper isolate --live` contra los contenedores reales. | Esperar. |
| 4. Validación | Checks del perfil + genéricos; `pass`/`fail`/`skipped` con detalle. | Revisar los `fail`. |
| 5. Estado | `environment.json` (`READY`/`PARTIAL`/`BLOCKED`/`FAILED`), `validation.md`, `missing-evidence.md`. | Decidir sobre `PARTIAL`. |
| 6. Cierre | Cómo apagar; el entorno es desechable. | Aprobar el estado. |

### Cómo validar

- [ ] Las imágenes del compose tienen las versiones que Inspect documentó (o una desviación explícita).
- [ ] Nada inventado: cada datasource, usuario y puerto viene de un artefacto.
- [ ] `READY` solo si la aplicación responde, no solo si el contenedor está `running`.
- [ ] `python3 -m pepper validate docs/pepper/environment.json` pasa.
- [ ] `python3 -m pepper isolate <compose> --hosts <hosts> --live` dice **AISLADO**. Sin eso no se observa nada: el entorno corre con las credenciales de producción del legacy.
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
python3 -m pepper package pepper-out/<session_id>/correlated --legacy legacy/ --map docs/pepper/system-map.json --previous docs/pepper/funcional.json --out pepper-out/<session_id>/package --data-mode remote
```

Salida: `events.jsonl` (eventos normalizados), `flow.json` / `flow.md` (por petición: acción del usuario, campos, y lo que disparó), `reduction.md` (qué se descartó y por qué), y el paquete: `prompt.md`, `CLAUDE.md`, `AGENTS.md`, `evidence/`, `map/` (el mapa y su versión legible), `previous/` (el documento del sistema anterior), `legacy/`, `output/`; más `package.evidence-manifest.json` fuera del paquete.

`--data-mode remote` bloquea secretos/PII y archivos no inspeccionables. `--allow-sensitive` y `--acknowledge-unscanned` requieren una autorización humana explícita; las excepciones quedan registradas. `--data-mode local` permite material sensible, pero ese paquete no puede abrirse con Claude Code/Codex remoto.

### Cómo validar

- [ ] Líneas sin parsear = 0, o explicadas (basura real). Si no: parser del perfil.
- [ ] Ningún evento sin asignar "por ambigüedad" — o repites la observación con una petición a la vez.
- [ ] `flow.md` cuenta lo que hiciste: las peticiones, en orden, con la acción y sus resultados. Si las acciones salen como "(botón sin nombre)", el perfil necesita `http.action_fields`.
- [ ] `reduction.md` no descartó nada que importe (nunca descarta errores ni escrituras; revísalo igual).

---

## 6. Fase 5: Discover

```text
Use the descubridor-funcional subagent to produce
pepper-out/<session_id>/package/output/funcional.json and funcional.md
```

### Qué esperar

| Fase del agente | Qué hace | Tu trabajo |
|---|---|---|
| 1. Lo que cubrió la sesión | Lee `flow.md`; te dice en tres líneas qué pantallas, escrituras y rechazos hubo. | Nada (no te pregunta lo que la evidencia ya dice). |
| 2. Lectura del mapa | Roles, menús por rol, estados, catálogos, triggers, pantallas, constantes. Te avisa de lo raro en el momento. | Leer lo que señale. |
| 3. Escritura | `funcional.md` con sus 12 secciones + `funcional.json`; si había documento anterior, lo extiende. | Leerlo completo. |
| 4. Auto-validación | `pepper export --check` + checklist del skill. | Aprobar explícitamente. |

### Cómo validar

- [ ] La sección 1 se entiende sin conocer el sistema; la 2 dice quién y qué puede hacer cada quien.
- [ ] Cada afirmación trae su origen ([código] [base] [datos] [observado] [config] [doc]); nada dice "observado" que ninguna ventana ejecutó.
- [ ] Las reglas se leen como las contaría alguien de la oficina; las que viven en triggers o constantes están marcadas como escondidas.
- [ ] Los estados traen cuántos registros reales hay en cada uno.
- [ ] La sección 12 tiene preguntas con a quién preguntarle o qué ventana observar. Nunca está vacía.
- [ ] Sin nombres de personas, CURP, correos ni contraseñas.

---

## 7. Fase 6: Export y entrega a stark

`/pepper-export <session_id>` corre `python3 -m pepper export … --manifest pepper-out/<session_id>/package.evidence-manifest.json --out docs/pepper/discovery/<session_id> --system-doc docs/pepper` y aplica el contrato: ambos manifests coinciden; evidencia, mapa, legacy y discovery anterior conservan sus hashes; el JSON valida contra `functional-discovery.schema.json`; toda afirmación cita fuentes declaradas; toda fuente resuelve (event_id o archivo:línea de la evidencia; `map:…` del mapa; archivo del paquete); la sesión está en `sessions`; hay desconocidos; existe el `.md`. Si algo falla, **no publica**: vuelves a Discover.

Publica: `docs/pepper/discovery/<session_id>/funcional.json|md` + `validation.md` (esta sesión) y `docs/pepper/funcional.json|md` (el documento del sistema, vigente y acumulado).

### Entrega a stark

| Cuándo | PEPPER hace |
|---|---|
| siempre | copia `docs/pepper/funcional.md` como `docs/analysis/funcional.md`: input de `arqueologo-codigo` cuando stark se instale sobre el repo |
| `docs/REGLAS_DE_NEGOCIO.md` | **no lo edita**; te lista qué llevar a su sección 11 con el mapeo de confianza |

Mapeo de confianza (skill `evidencia-runtime` §3): `confirmada` y `sustentada` → `inferida`; `inferida` → `inferida` con nota o pregunta abierta; `contradicha` → `en-duda`; `desconocida` → pregunta abierta. **Nada entra como `confirmada`**: eso exige una persona con nombre.

---

## 8. El núcleo a mano

```bash
python3 -m pepper detect <artefactos>/                       # qué perfil aplica, con qué señales
python3 -m pepper map <artefacto> --profile <id> [--dump <r>] [--evidence <e>] --out <f>  # lo que el sistema es: system-map.json + map/*.md
python3 -m pepper validate <archivo>... [--schema NOMBRE]     # contratos: profile, parser, session, environment, flow, event, system-map, functional-discovery
python3 -m pepper isolate <compose> [--hosts a,b] [--live]    # ¿el entorno rehidratado puede alcanzar algo externo?
python3 -m pepper proxy --upstream <host:puerto> [--listen h:p] [--out f]  # el ingress: inyecta correlation_id, emite http.jsonl
python3 -m pepper collect <compose> <session_id> --start <ISO> --end <ISO> [--margin 30]  # la ventana, desde los contenedores
python3 -m pepper correlate <evidencia>/ --out <dir> [--profile <id>] [--tolerance-ms 500]
python3 -m pepper package <correlated>/ --legacy <artefactos>/ --map <system-map.json> --previous <funcional.json> --out <dir> --data-mode remote
python3 -m pepper export <paquete>/ --manifest <manifest-externo> --check      # solo validar
python3 -m pepper export <paquete>/ --manifest <manifest-externo> --out <dir> --system-doc docs/pepper  # validar y publicar
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
- **Mapa del sistema**: lo que el sistema ES, sacado del artefacto y del respaldo por `pepper map`: rutas, jobs, pantallas, clases, tablas, catálogos, triggers, distribuciones. `system-map.json` + `map/*.md`.
- **Paquete controlado**: la carpeta autocontenida sobre la que trabaja el agente de discovery: mapa, evidencia correlacionada, legacy, discovery anterior, prompt, adaptadores `CLAUDE.md`/`AGENTS.md`, `output/`.
- **Documento funcional** (`funcional.md/json`): el entregable — qué hace el sistema, en 12 secciones fijas, cada afirmación con su origen. Es del sistema y se acumula sesión a sesión.
- **Origen / basis**: de dónde sale una afirmación: `observado` (se vio ejecutar), `en_codigo`, `en_base`, `en_datos`, `en_config`, `en_doc`, `humano`.
- **Confianza**: `confirmada` (observado + código/base coinciden), `sustentada` (una fuente sólida), `inferida`, `contradicha`, `desconocida`.
- **Contradicción**: dos fuentes que no cuadran; siempre para validación humana.
- **Desconocido**: lo que no se sabe, con por qué y a quién preguntarle o qué ventana observar.
- **Modo contraste**: dos agentes sobre el mismo paquete, salidas comparadas; coincidencia sube confianza, discrepancia genera revisión.
- **Procedencia (stark)**: `confirmada` / `inferida` / `en-duda`. PEPPER entrega como máximo `inferida`.
