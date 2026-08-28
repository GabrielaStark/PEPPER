---
description: Fase 3 · Observa una ejecución real de un flujo funcional - prepara los colectores, delimita la ventana mientras el humano opera la aplicación y captura la evidencia cruda en evidence/<session_id>/.
argument-hint: "<nombre-del-flujo>"
---

Lee `docs/documentacion/PRINCIPIOS.md` y aplica sus reglas como restricciones duras antes de actuar.

Pre-condición: un sistema corriendo — el reconstruido por PEPPER (`docs/pepper/environment.json` en `READY` o `PARTIAL`) o uno accesible en otro ambiente (escalón 2). Si no hay ninguno, detente e indica `/pepper-rehydrate`.

Use the observador-runtime subagent to capture the flow "$ARGUMENTS" into `evidence/<session_id>/` (`session.json` más la evidencia cruda de cada colector).

**El flujo lo ejecuta el humano** ✋: el agente prepara y verifica los colectores antes de la ventana, marca el inicio, espera a que el humano opere la aplicación, marca el fin y captura. El agente nunca opera la aplicación ni ejecuta el flujo por su cuenta.

Gate humano ✋: el humano confirma que la ventana cubre el flujo completo (incluidos los intentos fallidos, que son la evidencia más valiosa) y que la evidencia capturada es la esperada. Sin evidencia, Correlate no tiene material.

Siguiente: `/pepper-correlate <session_id>`.
