---
description: Fase 3 · Observa una ejecución real de un flujo funcional - prepara los colectores, delimita la ventana mientras el humano opera la aplicación y captura la evidencia cruda en evidence/<session_id>/.
argument-hint: "<nombre-del-flujo>"
---

Lee `docs/documentacion/PRINCIPIOS.md` y aplica sus reglas como restricciones duras antes de actuar.

Pre-condición: un sistema corriendo — el reconstruido por PEPPER (`docs/pepper/environment.json` en `READY` o `PARTIAL`) o uno accesible en otro ambiente (escalón 2). Si no hay ninguno, detente e indica `/pepper-rehydrate`.

Use the observador-runtime subagent to capture the flow "$ARGUMENTS" into `evidence/<session_id>/` (`session.json` más la evidencia cruda de cada colector).

**El flujo lo ejecuta el humano** ✋: el agente prepara y verifica los colectores antes de la ventana, marca el inicio, espera a que el humano opere la aplicación, marca el fin y captura. El agente nunca opera la aplicación ni ejecuta el flujo por su cuenta.

**Dile al humano exactamente qué se espera de él**, antes de abrir la ventana y otra vez al abrirla — es el único paso donde opera el sistema, y si no lo sabe la evidencia sale pobre:

1. **Por dónde entra y con qué usuario** (URL publicada por el ingress y credencial disponible).
2. **Un flujo a la vez**: nada de abrir otras pantallas en paralelo mientras la ventana esté abierta.
3. **Que provoque al menos un rechazo** — un campo obligatorio vacío, un dato imposible, un duplicado. El rechazo dice qué condición exige el sistema, y esa es una regla de negocio. Un flujo perfecto a la primera enseña la mitad.
4. **Que avise al terminar** con una frase de qué hizo, incluidos los tropiezos y lo que no pudo completar: eso es `operator_note` y orienta todo el discovery.
5. **Que no apunte nada** mientras opera: la captura es completa.
6. **Que una pantalla en blanco o un botón mudo no es un fallo suyo**: el ingress bloquea en el navegador todo lo que el legacy intente cargar de un servidor real (D25) y lo registra como dependencia externa. Que lo mencione en su nota.

Mientras la ventana esté abierta, **no generes tráfico**: ni comprobaciones, ni peticiones de cortesía. Cualquier verificación va antes de abrirla o después de cerrarla.

Gate humano ✋: el humano confirma que la ventana cubre el flujo completo (incluidos los intentos fallidos, que son la evidencia más valiosa) y que la evidencia capturada es la esperada. Sin evidencia, Correlate no tiene material.

Siguiente: `/pepper-correlate <session_id>`.
