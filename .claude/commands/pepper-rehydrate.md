---
description: Fase 2 · Reconstruye un entorno ejecutable y desechable del legacy en contenedores, fiel al stack original y con la observabilidad activada de antemano. Produce docs/pepper/environment.json.
---

Lee `docs/documentacion/PRINCIPIOS.md` y aplica sus reglas como restricciones duras antes de actuar.

Pre-condición: `docs/pepper/stack-report.md` existe y el humano lo confirmó. Si no existe, detente e indica `/pepper-inspect`.

Use the rehidratador-legacy subagent to produce `docs/pepper/environment.json`, `docs/pepper/validation.md` y, cuando falten insumos, `docs/pepper/missing-evidence.md`, a partir de `legacy/`, `docs/pepper/stack-report.md` y el perfil que el reporte indica. Los archivos generados para levantar el entorno (compose, configuración, scripts) van a `pepper-out/rehydrate/`.

Dos gates humanos ✋:

1. **El plan antes de ejecutar.** El agente presenta el plan de reconstrucción (contenedores, imágenes y versiones, restauración de datos, datasources, puertos, qué observabilidad se activa) y NO levanta ningún contenedor hasta que el humano lo apruebe.
2. **El estado final.** Solo `READY` — o `PARTIAL` con acuerdo explícito del humano sobre qué falta y qué se podrá observar — habilita la siguiente fase. `BLOCKED` (falta evidencia) y `FAILED` (era viable pero falló técnicamente) son entregables válidos: se documentan y se para.

Siguiente: `/pepper-observe <nombre-del-flujo>`.
