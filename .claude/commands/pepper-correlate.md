---
description: Fase 4 · Normaliza, reduce y correlaciona la evidencia de una sesión con el núcleo determinístico y arma el paquete controlado para el agente. Sin subagente - lo hace el código.
argument-hint: "<session_id>"
---

Lee `docs/documentacion/PRINCIPIOS.md` y aplica sus reglas como restricciones duras antes de actuar.

Esta fase NO usa subagente: la ejecuta el núcleo de PEPPER, de forma determinística (misma evidencia → mismos bytes). Tú corres los comandos y presentas el resultado sin interpretarlo — la interpretación es de la fase siguiente.

## 1. Correlate

```bash
python3 -m pepper correlate evidence/$ARGUMENTS --out pepper-out/$ARGUMENTS/correlated
```

Agrega `--profile <id>` si `session.json` no declara `environment.profile_id`. Si falla con "sin parser para las fuentes …", el perfil no cubre alguna fuente: la solución es un parser declarativo (skill `perfil-stack`), no editar la evidencia.

## 2. Package

Pregunta al humano dónde se ejecutará Discover:

- `remote` — Claude Code/Codex con modelo remoto. Es el default y activa el gate de secretos/PII.
- `local` — modelo íntegramente local; el paquete queda marcado para que no se abra por accidente con un agente remoto.

```bash
python3 -m pepper package pepper-out/$ARGUMENTS/correlated --legacy legacy/ --map docs/pepper/system-map.json --previous docs/pepper/funcional.json --out pepper-out/$ARGUMENTS/package --data-mode remote
```

- `--map`: el mapa de `pepper map` (Inspect). Sin él el agente solo ve la ejecución y no puede escribir roles, permisos, estados ni catálogos: si no existe, córrelo primero (`python3 -m pepper map <artefacto> --profile <id> --dump <respaldo>`).
- `--previous`: el documento del sistema publicado por un discovery anterior (`docs/pepper/funcional.json`). Omítelo solo en el primer flujo: el discovery es acumulativo.
- Usa `--legacy .` si PEPPER está instalado encima del repo del legacy (el núcleo excluye su propia herramienta); omite `--legacy` si no hay artefactos. Si el paquete ya existe, pregunta al humano antes de borrarlo — puede contener un `output/` de un discovery anterior.

En modo remoto, si el gate detecta secretos/PII o archivos que no pudo inspeccionar, muestra las **ubicaciones, nunca los valores**, y detente. No agregues por tu cuenta `--allow-sensitive` ni `--acknowledge-unscanned`: cada excepción requiere autorización explícita del humano responsable del dato. Package registra esas excepciones en el manifest.

Package crea `pepper-out/$ARGUMENTS/package.evidence-manifest.json` fuera del paquete. No lo copies al paquete ni lo edites: Export lo exige como raíz de confianza.

## 3. Presenta

Muestra al humano, sin interpretar: los conteos (líneas crudas → parseadas → conservadas; sin parsear; peticiones; sin asignar) y un resumen de `evidence/flow.md` en tres líneas — qué pantallas y acciones aparecen, qué se escribió en la base, qué se rechazó. No le pidas que confirme lo que hizo: la evidencia lo dice.

Señales que exigen decisión humana antes de seguir:

- **líneas sin parsear > 0** → el parser del perfil no cubre la fuente; corregirlo y repetir esta fase.
- **eventos sin asignar por ambigüedad** → hubo peticiones concurrentes sin afinidad que las separe; considerar repetir la observación con una petición a la vez.
- **0 peticiones** → no hubo colector HTTP con `correlation_id`; la correlación se hizo solo por afinidad y ventana temporal. Dilo explícitamente: el discovery deberá fijar confianzas más bajas.

Gate humano ✋: el humano ve el resumen y puede corregir o añadir el caso de negocio si quiere; si no dice nada, queda así. Siguiente: `/pepper-discover $ARGUMENTS`.
