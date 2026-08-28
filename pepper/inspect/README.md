# inspect

Esta fase la ejecuta el agente [`inspector-legacy`](../../.claude/agents/inspector-legacy.md) (`/pepper-inspect`). El núcleo le aporta dos herramientas determinísticas:

- `python3 -m pepper detect <artefactos>/` — evalúa las señales de detección de cada perfil (`detection.signals`) sobre el directorio y dice cuál aplica, con qué puntaje y qué archivo disparó cada señal (`pepper/detect.py`).
- `python3 -m pepper validate <archivo>...` — valida perfiles, parsers y demás instancias contra sus contratos (`pepper/validate.py`).

Salida de la fase: `docs/pepper/stack-report.md` y, sin perfil, `profiles/<id>/` en `draft`. Espec: [docs/documentacion/fases/inspect.md](../../docs/documentacion/fases/inspect.md).
