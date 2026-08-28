# package — implementado

Entrada: salida de `correlate` + (opcional) directorio con los artefactos del legacy.

```bash
python3 -m pepper package <correlated>/ --legacy <artefactos>/ --out <paquete>/
```

Arma el **paquete controlado**: carpeta autocontenida con la evidencia (normalizada, correlacionada y cruda), el código y configuración del legacy, `session.json`, el schema de salida, `prompt.md` (la skill `discovery-runtime` sin frontmatter, para que cualquier agente trabaje sin este repo) y los adaptadores por agente (`CLAUDE.md` y `AGENTS.md`, ambos apuntando a `prompt.md`). Layout: [fases/discover.md](../../docs/documentacion/fases/discover.md).

Se niega a sobrescribir un directorio con contenido. Al copiar el legacy ignora `target/`, `.git/`, `node_modules/` y `__pycache__/`.

Meta de tamaño: que un agente pueda leer el paquete completo sin truncamiento. Si no cabe, el defecto se corrige en la reducción de `correlate`, no pidiéndole al agente que busque en bruto.
