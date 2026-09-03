# Núcleo de PEPPER

Paquete Python (3.9+), solo biblioteca estándar. **Invariante: ninguna línea del núcleo conoce una tecnología concreta** — todo lo específico de un stack entra como perfil ([ARQUITECTURA.md](../docs/documentacion/ARQUITECTURA.md)).

```bash
python3 -m pepper --help
python3 -m pepper demo                      # correlate + package sobre examples/legacy-demo
python3 -m unittest discover -s tests       # la suite
```

| Módulo | Quién lo ejecuta | Qué aporta el núcleo | Estado |
|---|---|---|---|
| [inspect/](inspect/) | agente `inspector-legacy` | `pepper detect` (señales de perfil), `pepper validate` | herramientas listas |
| [rehydrate/](rehydrate/) | agente `rehidratador-legacy` | `pepper isolate` (verifica que el entorno no alcance nada externo), `pepper validate` para `environment.json` | **isolate implementado**; runner de recetas pendiente |
| [observe/](observe/) | agente `observador-runtime` | `pepper proxy` ([proxy.py](proxy.py)): el ingress que inyecta `correlation_id` y emite `http.jsonl`; `pepper collect` ([observe/collect.py](observe/collect.py)): la ventana desde los contenedores | **implementados**; las fuentes del perfil las copia el agente |
| [correlate/](correlate/) | el núcleo | parsers declarativos, reducción auditada, correlación → `events.jsonl`, `flow.json` | **implementado** |
| [package/](package/) | el núcleo | el paquete controlado con `prompt.md`, `CLAUDE.md`, `AGENTS.md` | **implementado** |
| [discover/](discover/) | agente `descubridor-runtime` | `pepper export --manifest <externo> --check` para auto-verificarse | lo hace el agente |
| [export/](export/) | el núcleo | validación contra el contrato + publicación | **implementado** |

Archivos transversales: `cli.py` (comandos), `session.py` (session.json), `profiles.py` (localizar y cargar perfiles), `detect.py`, `validate.py`, `isolate.py`.

`jsonschema` es obligatorio para `pepper export`: sin validación de forma no se publica.
