---
description: Observa a una persona usando el sistema levantado por PEPPER - se abre el navegador hermético de PEPPER, la persona opera, se captura todo y la sesión entra al discovery como una más. Úsalo cuando haya quien conozca un flujo; si no, /pepper lo explora solo.
argument-hint: "<nombre-del-flujo>"
---

Lee `docs/documentacion/PRINCIPIOS.md` y `.claude/skills/evidencia-runtime/SKILL.md`: reglas duras.

Pre-condición: `docs/pepper/environment.json` en `READY`/`PARTIAL` (si no, `/pepper levantar`) y `pepper-out/explore.json` con `login`, `credentials` y al menos un rol (si no existe, escríbelo como dice `/pepper` paso 3). Verifica el aislamiento antes de abrir nada:

```bash
python3 -m pepper isolate pepper-out/rehydrate/docker-compose.yml --hosts "<hosts externos>" --live
```

`NO AISLADO` o `NO VERIFICADO` → detente.

## La ventana

La persona **no usa su navegador**: un navegador de siempre sale por fuera de los contenedores (un `location.href=` por script, un `preconnect`, el historial sincronizado) sin que nada lo vea. PEPPER abre su propio Chromium, con el resolver cerrado y todo por el ingress, y la persona opera ahí:

```bash
python3 -m pepper explore pepper-out/rehydrate/docker-compose.yml --config pepper-out/explore.json --session <sid> --profile <id> --hosts "<hosts externos>" --observe --headed --flow-name "$ARGUMENTS"
```

Antes de correrlo, dile tres cosas: un flujo a la vez; que provoque al menos un rechazo (un campo vacío, un dato imposible); y que **cierre la ventana** cuando termine, sin decir nada más. Los usuarios y la contraseña de prueba son los de `explore.json` (el núcleo los fija en la base desechable antes de abrir). Una pantalla en blanco es un recurso externo bloqueado por el ingress: es hallazgo, no fallo. No generes tráfico mientras la ventana esté abierta.

Al cerrarse la ventana, el núcleo espera a que el sistema termine lo que la última acción disparó, captura la ventana desde el ingress y los contenedores, y escribe `evidence/<sid>/session.json` con el veredicto. Tú no lees `http.jsonl` ni los logs (el guardia lo impide): lo que la persona hizo lo verás en `flow.md` **dentro del paquete**, después de correlate y package.

## Después

`/pepper descubrir` con esa sesión: correlate → package (con `--previous`) → discovery → export. Al presentar el resultado, resume en tres líneas lo que la ventana cubrió (pantallas, escrituras, rechazos, bloqueos) leyéndolo de `flow.md` del paquete; solo si la persona quiere, añade su caso de negocio como fuente `humano`. **No le preguntes nada que la evidencia ya responda.** El documento del sistema se extiende con lo que la persona hizo.
