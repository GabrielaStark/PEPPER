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

```bash
python3 -m pepper package pepper-out/$ARGUMENTS/correlated --legacy legacy/ --out pepper-out/$ARGUMENTS/package
```

Usa `--legacy .` si PEPPER está instalado encima del repo del legacy (el núcleo excluye su propia herramienta); omite `--legacy` si no hay artefactos. Si el paquete ya existe, pregunta al humano antes de borrarlo — puede contener un `output/` de un discovery anterior.

## 3. Presenta

Muestra al humano, sin interpretar: los conteos (líneas crudas → parseadas → conservadas; sin parsear; peticiones; sin asignar), el contenido de `evidence/flow.md` completo y el resumen de `evidence/reduction.md`.

Señales que exigen decisión humana antes de seguir:

- **líneas sin parsear > 0** → el parser del perfil no cubre la fuente; corregirlo y repetir esta fase.
- **eventos sin asignar por ambigüedad** → hubo peticiones concurrentes sin afinidad que las separe; considerar repetir la observación con una petición a la vez.
- **0 peticiones** → no hubo colector HTTP con `correlation_id`; la correlación se hizo solo por afinidad y ventana temporal. Dilo explícitamente: el discovery deberá fijar confianzas más bajas.

Gate humano ✋: el humano confirma que la secuencia de `flow.md` corresponde a lo que hizo durante la ventana. Siguiente: `/pepper-discover $ARGUMENTS`.
