# correlate — implementado

Entrada: directorio de evidencia con `session.json` y los archivos de sus colectores.

```bash
python3 -m pepper correlate <evidencia>/ --out <salida>/ [--profile <id|ruta>]
```

| Archivo | Qué hace |
|---|---|
| `events.py` | el evento normalizado (`Event`) y su serialización a `events.jsonl` |
| `parsers.py` | `PatternParser` (intérprete de specs declarativas de perfil) y `HttpProxyParser` (formato del núcleo) |
| `sql.py` | forma genérica de una sentencia: operación, tabla, secuencia |
| `reduce.py` | ruido genérico + ruido de perfil + deduplicación de logs, con auditoría; evidencia protegida intocable |
| `correlate.py` | agrupa por petición: correlation_id > afinidad (thread/pid) > ventana temporal; lo ambiguo queda sin asignar |
| `render.py` | `flow.md` y `reduction.md` |
| `__init__.py` | `run()`: orquesta parse → reduce → IDs → correlate → escribir |

**100% genérico**: el conocimiento de cada stack llega en el parser JSON del perfil ([contrato](../../schemas/parser.schema.json)). Este módulo jamás menciona uno. Espec: [fases/correlate.md](../../docs/documentacion/fases/correlate.md).
