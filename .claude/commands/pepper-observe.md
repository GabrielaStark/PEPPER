---
description: Observa a una persona usando el sistema levantado por PEPPER - abre una ventana, la persona opera, se captura todo y la sesión entra al discovery como una más. Úsalo cuando haya quien conozca un flujo; si no, /pepper lo explora solo.
argument-hint: "<nombre-del-flujo>"
---

Lee `docs/documentacion/PRINCIPIOS.md` y `.claude/skills/evidencia-runtime/SKILL.md`: reglas duras.

Pre-condición: `docs/pepper/environment.json` en `READY`/`PARTIAL` (si no, `/pepper levantar`). Verifica antes de abrir la ventana:

```bash
python3 -m pepper isolate pepper-out/rehydrate/docker-compose.yml --hosts "<hosts externos>" --live
```

`NO AISLADO` o `NO VERIFICADO` → detente.

## La ventana

1. Dile a la persona por dónde entrar (`http://127.0.0.1:18080`, con qué usuario: `docs/pepper/explore.json` trae uno por rol con la contraseña de prueba) y tres cosas: un flujo a la vez; que provoque al menos un rechazo (un campo vacío, un dato imposible); que diga "terminé" y nada más. Una pantalla en blanco es un recurso externo bloqueado por el ingress: es hallazgo, no fallo.
2. Marca el inicio (`date` con zona). No generes tráfico mientras la ventana esté abierta.
3. Cuando diga que terminó, espera a que el stdout del ingress lleve ~10 s sin peticiones y marca el fin.
4. Captura:

```bash
python3 -m pepper collect pepper-out/rehydrate/docker-compose.yml <session_id> --start <ISO con zona> --end <ISO con zona>
```

5. Escribe `evidence/<session_id>/session.json` (contrato `schemas/session.schema.json`; un colector por archivo capturado con el `source` del parser del perfil; `http.jsonl` es `http-proxy`) y valida con `python3 -m pepper validate`. La `operator_note` la redactas **tú desde la evidencia** (rutas, escrituras, rechazos, bloqueos) y se la muestras en tres líneas; solo si quiere añade el caso de negocio. **No le preguntes nada que la evidencia ya responda.**

## Después

`/pepper descubrir` con esa sesión: correlate → package (con `--previous`) → discovery → export. El documento del sistema se extiende con lo que la persona hizo.
