# Núcleo de PEPPER

Paquete Python (3.9+), biblioteca estándar más `jsonschema` (obligatorio para publicar). **Invariante: ninguna línea del núcleo conoce una tecnología concreta** — todo lo específico de un stack entra como perfil ([ARQUITECTURA.md](../docs/documentacion/ARQUITECTURA.md)).

```bash
python3 -m pepper --help
python3 -m pepper demo                      # correlate + package sobre examples/legacy-demo
python3 -m unittest discover -s tests       # la suite
```

| Comando | Módulo | Qué hace |
|---|---|---|
| `detect` | `detect.py` | qué perfil aplica a unos artefactos, con qué señales |
| `map` | `inspect/systemmap.py`, `inspect/pgdump.py` | todo lo que el sistema ES: rutas, jobs, pantallas, clases, tablas, catálogos, triggers → `system-map.json` + `map/*.md` |
| `validate` | `validate.py` | instancias contra los contratos de `schemas/` |
| `isolate` | `isolate.py` | el entorno rehidratado no alcanza nada externo (compose y, con `--live`, contenedores) |
| `proxy` | `proxy.py` | el ingress: reenvía, inyecta `correlation_id`, emite `http.jsonl`, aísla al navegador; autocontenido (se monta solo en el contenedor) |
| `collect` | `observe/collect.py` | copia la ventana observada desde los contenedores a `evidence/<sid>/` |
| `correlate` | `correlate/` | parsers declarativos → reducción auditada → correlación → `events.jsonl`, `flow.json/md` |
| `package` | `package/` | el paquete controlado: evidencia + mapa + legacy + discovery anterior + prompt; gate de datos; manifest externo |
| `export` | `export/` | valida `funcional.json/md` contra el contrato y lo publica (por sesión y como documento del sistema) |

Transversales: `cli.py`, `session.py` (session.json), `profiles.py`, `manifest.py` (hashes de evidencia), `sensitive.py` (gate de datos), `stub.py` (el sumidero HTTP de los hosts externos), `workspace.py`.

Discover no está en el núcleo: lo hace el agente sobre el paquete, bajo la skill `discovery-funcional`, y el núcleo solo lo valida.
