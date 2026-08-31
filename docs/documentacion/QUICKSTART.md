# QUICKSTART de PEPPER

El camino feliz, de principio a fin. ¿El porqué de cada decisión? → [`PRINCIPIOS.md`](PRINCIPIOS.md) y [`DECISIONES.md`](DECISIONES.md). ¿El detalle de cada fase (qué esperar del agente, cómo validar)? → [`REFERENCIA.md`](REFERENCIA.md). ¿Algo se trabó? → [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md).

---

## Arranca

Dos modos de instalación (comandos exactos en el [README](../../README.md#instalar)): **workspace** — clonas PEPPER como carpeta del legacy y pones los artefactos en `legacy/` — o **encima del repo** del legacy, copiando la herramienta gitignoreada, igual que stark en mantenimiento; ahí los artefactos son el repo mismo (`/pepper-inspect .`) y, al terminar, borras la herramienta e instalas stark, que encuentra el discovery en `docs/analysis/`.

Grábate **la regla de oro**: lo que PEPPER te entrega es la herramienta y se ignora en git; lo que PEPPER produce (`docs/pepper/`, `docs/analysis/runtime-discovery-*.md`) es el producto y se commitea; `legacy/`, `evidence/` y `pepper-out/` nunca se commitean — son datos ajenos.

Un solo riel: los comandos `/pepper-*` desde Claude Code (con Codex u otro agente: cada comando es un archivo de instrucciones en `.claude/commands/`, ver [`AGENTS.md`](../../AGENTS.md)).

```text
/pepper-init
```

Te pregunta tu situación, prepara el terreno y te dice **textualmente** el siguiente comando. De ahí avanzas comando por comando, aprobando cada gate humano ✋.

---

## 1. Elige tu escalón

| Tu situación | Escalón | Empiezas con |
|---|---|---|
| Tienes artefactos (código, WAR, dump, configs) y el sistema no corre en ningún lado | **1** si hay perfil para su stack, **3** si no | artefactos en `legacy/` → `/pepper-inspect` |
| El sistema ya corre en un ambiente accesible | **2** — se observa con colectores genéricos | `/pepper-observe <flujo>` |
| No tienes ni artefactos ni sistema corriendo | — | consigue algo primero; `/pepper-init` te dice el mínimo |

`/pepper-init` hace esta pregunta y corre `pepper detect` sobre tus artefactos para darte la primera pista.

---

## 2. Tu pista lineal

Una sola secuencia, de arriba a abajo. ✋ = gate humano.

### Escalón 1 · con perfil (o 3 · sin perfil, con inspección)

1. `/pepper-init` — verifica herramientas, prepara `legacy/`, `evidence/`, `docs/pepper/`.
2. **Pon tus artefactos** en `legacy/` — lo que tengas, como esté. Ordenar el desorden es trabajo de PEPPER. Y **llena `legacy/NOTAS.md`** (init lo deja listo desde `templates/NOTAS-LEGACY.md`) con lo que sepas: servidor y versión de producción, base, cómo arranca, flujos que importan. Una línea tuya ahorra horas.
3. `/pepper-inspect` — stack con evidencia, dependencias, faltantes, perfil (o borrador) → `docs/pepper/stack-report.md`. ✋
4. `/pepper-rehydrate` — plan de reconstrucción ✋ → **aislamiento verificado** (`pepper isolate`) → contenedores → validación → `docs/pepper/environment.json`. ✋ (`BLOCKED` y `FAILED` son entregables: paras ahí, con la lista de qué falta.)
5. `/pepper-observe <flujo>` — colectores listos → **tú ejecutas el flujo** → `evidence/<session_id>/`. ✋
6. `/pepper-correlate <session_id>` — el núcleo normaliza, reduce y correlaciona; arma el paquete. Revisas `flow.md`. ✋
7. `/pepper-discover <session_id>` — el agente analiza el paquete → `runtime-discovery.json/md`. Lo lees completo. ✋
8. `/pepper-export <session_id>` — validación contra el contrato → `docs/pepper/discovery/<session_id>/` → entrega a stark si aplica. ✋

Repite 5–8 por cada flujo que quieras entender.

### Escalón 2 · el sistema ya corre

Igual, sin 3 y 4: `/pepper-init` → `/pepper-observe <flujo>` → `/pepper-correlate` → `/pepper-discover` → `/pepper-export`. Sin proxy delante no habrá `correlation_id`: la correlación va por afinidad y ventana temporal, y el discovery fija confianzas más bajas. Si quieres parsers para sus logs, `/pepper-inspect` sobre lo que tengas del sistema te deja un borrador de perfil.

---

## 3. Prueba en 5 minutos, sin legacy

El repo trae un legacy de juguete con evidencia ya capturada y su clave de respuestas:

```bash
python3 -m pepper demo                        # correlate + package sobre examples/legacy-demo
```

Después `/pepper-discover legacy-demo` (el paquete quedó en `pepper-out/legacy-demo/package/`) y `/pepper-export legacy-demo`. Califica al agente contra [`examples/legacy-demo/expected/notes.md`](../../examples/legacy-demo/expected/notes.md): tres trampas sembradas — una regla escondida, una contradicción y un desconocido — y tiene que encontrarlas donde deben quedar.

---

## 4. Cheat sheet

| Fase | Comando | Produce |
|---|---|---|
| 0. Init | `/pepper-init` | workspace listo, escalón |
| 1. Inspect | `/pepper-inspect` | `docs/pepper/stack-report.md`, borrador de perfil |
| 2. Rehydrate | `/pepper-rehydrate` | `docs/pepper/environment.json`, `docs/pepper/isolation.md` |
| 3. Observe | `/pepper-observe <flujo>` | `evidence/<session_id>/` |
| 4. Correlate | `/pepper-correlate <session_id>` | `pepper-out/<session_id>/{correlated,package}` |
| 5. Discover | `/pepper-discover <session_id>` | `…/package/output/runtime-discovery.*` |
| 6. Export | `/pepper-export <session_id>` | `docs/pepper/discovery/<session_id>/` |

Bajo los comandos está el núcleo, usable a mano: `python3 -m pepper {detect,validate,isolate,correlate,package,export,demo}`.

---

## 5. Los gates humanos

✋ **Una fase a la vez. Tú apruebas entre cada una.**

- Tras **inspect**: confirmas stack, escalón y veredicto.
- Tras el **plan de rehydrate**: apruebas antes de que se levante un solo contenedor. Tras la ejecución: solo `READY` (o `PARTIAL` con tu acuerdo) sigue.
- En **observe**: el flujo lo ejecutas tú; el agente solo captura.
- Tras **correlate**: confirmas que `flow.md` es lo que hiciste.
- Tras **discover**: lees el discovery completo; el agente itera sobre la evidencia, nunca "ajusta" conclusiones.
- En **export**: la máquina valida que toda conclusión resuelve a evidencia; tú decides qué se convierte en conocimiento. Nada de PEPPER entra a stark como `confirmada`: eso lo hace una persona con nombre.

Una fase a la vez, un flujo por sesión, tú apruebas.
