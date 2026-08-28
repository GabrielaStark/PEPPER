---
description: Fase 0 · Arranca un workspace PEPPER - verifica herramientas, prepara carpetas, determina el escalón de soporte del legacy y enruta al siguiente comando.
argument-hint: "[ruta-a-los-artefactos-del-legacy]"
---

Lee `docs/documentacion/PRINCIPIOS.md` y `.claude/skills/evidencia-runtime/SKILL.md` y aplica sus reglas como restricciones duras durante toda la sesión.

Este comando arranca PEPPER sobre un legacy. NO analiza nada: orquesta el setup, determina en qué escalón de soporte cae el legacy y deja claro el siguiente paso.

## 1. Verifica las herramientas

- `python3 --version` (≥ 3.9) y `python3 -m pepper --version` — obligatorio: es el núcleo determinístico.
- `python3 -c "import jsonschema"` — opcional; sin él no se validan los contratos. Si falta, recomienda `pip install -r requirements-dev.txt`.
- `docker --version` y `docker compose version` — opcionales; sin Docker no hay Rehydrate (escalón 1), pero sí Observe sobre un sistema que ya corre (escalón 2).

Reporta qué hay y qué falta. No instales nada tú.

## 2. Prepara el workspace

Determina el modo: si la raíz contiene código de un proyecto ajeno a PEPPER (PEPPER está instalado **encima del repo** del legacy), los artefactos son el repo mismo y NO se crea `legacy/`; si no (PEPPER es el **workspace**), asegura `legacy/`. En ambos, asegura `evidence/` y `docs/pepper/`. Si no existe `legacy/NOTAS.md`, cópialo desde `templates/NOTAS-LEGACY.md` y pídele **explícitamente al humano** que lo llene con lo que sepa (servidor y versión de producción, base, cómo arranca, servicios externos, cómo se entra, flujos que importan, quién sabe del sistema): los agentes lo leen primero y lo citan como evidencia. Una línea suya vale horas de inferencia. No muevas ni copies artefactos tú: en modo workspace, si `$ARGUMENTS` trae una ruta, indícale **explícitamente al humano** que copie (o enlace con `ln -s`) sus artefactos dentro de `legacy/`. Recuérdale la regla de oro: `legacy/`, `evidence/` y `pepper-out/` no se versionan (contienen datos ajenos); `docs/pepper/` y `docs/analysis/` sí; la herramienta se ignora.

## 3. Determina el escalón

Pregunta al humano (o dedúcelo de `$ARGUMENTS` y de lo que haya en `legacy/`):

1. ¿Hay artefactos del legacy (código, WAR/JAR, dist, respaldos de BD, configuración, notas)?
2. ¿El sistema ya corre en algún ambiente accesible donde se pueda observar?

| Situación | Escalón | Siguiente comando |
|---|---|---|
| Hay artefactos y no corre | 1 (hay perfil) o 3 (no lo hay) — lo decide Inspect | `/pepper-inspect` |
| Ya corre y es accesible | 2 — observación con colectores genéricos (Inspect es opcional, para tener perfil) | `/pepper-observe <nombre-del-flujo>` |
| Ni artefactos ni sistema corriendo | nada que hacer todavía | lista mínima de qué conseguir; detente |

Si hay artefactos, corre `python3 -m pepper detect legacy/` (o `python3 -m pepper detect .` encima del repo; el núcleo excluye su propia herramienta) y muestra el resultado: es la primera pista del escalón.

## 4. Explica la secuencia

```text
init → inspect → rehydrate → observe → correlate → discover → export → (stark)
```

Di qué produce cada fase y dónde: `docs/pepper/stack-report.md` (inspect), `docs/pepper/environment.json` (rehydrate), `evidence/<session_id>/` (observe), `pepper-out/<session_id>/` (correlate), `pepper-out/<session_id>/package/output/` (discover), `docs/pepper/discovery/<session_id>/` (export). Cada fase termina en un gate humano ✋.

## 5. Cierra

Confirma qué quedó preparado y enuncia textualmente el siguiente comando a invocar.
