---
description: Fase 1 · Inspecciona los artefactos del legacy - identifica el stack con evidencia, detecta dependencias y faltantes, elige perfil o redacta un borrador. Produce docs/pepper/stack-report.md.
argument-hint: "[ruta-a-los-artefactos: legacy/ en el workspace, . encima del repo del legacy]"
---

Lee `docs/documentacion/PRINCIPIOS.md` y aplica sus reglas como restricciones duras antes de actuar.

Use the inspector-legacy subagent to produce `docs/pepper/stack-report.md` and `docs/pepper/system-map.json` (+ `docs/pepper/map/`) from the artifacts in `$ARGUMENTS` (`legacy/` en un workspace; `.` cuando PEPPER está instalado encima del repo del legacy) y, si ningún perfil validado aplica, un borrador de perfil en `profiles/<id>/` con `status: draft`.

Gate humano ✋: antes de avanzar, el humano confirma tres cosas: (1) el stack identificado y sus versiones, con la evidencia citada; (2) el escalón y el veredicto (`READY-candidato` / `PARTIAL` / `BLOCKED`); (3) el borrador de perfil, si lo hubo — que las señales de detección y la receta correspondan a **este** legacy y no a "lo común". Nada avanza sin esa confirmación explícita.

Siguiente comando según el veredicto:

- viable (`READY-candidato` / `PARTIAL`) → `/pepper-rehydrate`
- `BLOCKED` → el humano consigue la evidencia faltante que lista el reporte y repite `/pepper-inspect`. BLOCKED es un entregable, no un fracaso.
- el sistema ya corre en un ambiente accesible → `/pepper-observe <nombre-del-flujo>` (escalón 2; el perfil sirve igual para los parsers)
