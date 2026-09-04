---
description: Fase 5 · Discovery - el agente analiza el paquete controlado (mapa del sistema + evidencia de ejecución + artefactos) en modo solo lectura y escribe QUÉ HACE el sistema en funcional.json/md, extendiendo el documento anterior si lo hay.
argument-hint: "<session_id>"
---

Lee `docs/documentacion/PRINCIPIOS.md` y aplica sus reglas como restricciones duras antes de actuar.

Pre-condición: `pepper-out/$ARGUMENTS/package/` existe. Si no, indica `/pepper-correlate $ARGUMENTS`.

El manifest externo `pepper-out/$ARGUMENTS/package.evidence-manifest.json` queda fuera del directorio de trabajo del agente. Se usa para `export --check`, pero no se copia ni modifica durante Discover.

Use the descubridor-funcional subagent to produce `pepper-out/$ARGUMENTS/package/output/funcional.json` and `funcional.md` from that package.

**Modo contraste (opcional).** El paquete es agnóstico al agente: trae `CLAUDE.md` y `AGENTS.md` apuntando al mismo `prompt.md`. Un segundo agente puede analizarlo por su cuenta (`cd pepper-out/$ARGUMENTS/package && codex`). Si el humano lo quiere, compara las dos salidas: coincidencia por caminos distintos sube la confianza; discrepancia genera un ítem de revisión humana. No fusiones las salidas tú.

Gate humano ✋: el humano lee `funcional.md` completo. Puede pedir cambios; el agente itera sobre la evidencia, nunca "ajusta" la conclusión para que cuadre. Nada se publica sin esa lectura.

Siguiente: `/pepper-export $ARGUMENTS`.
