# export — implementado

Entrada: paquete controlado con `output/runtime-discovery.json` escrito por el agente.

```bash
python3 -m pepper export <paquete>/ --check              # solo validar (el agente se auto-verifica)
python3 -m pepper export <paquete>/ --out <publicación>/  # validar y publicar
```

Valida contra [schemas/runtime-discovery.schema.json](../../schemas/runtime-discovery.schema.json) y aplica las reglas del contrato:

- toda conclusión referencia evidencia declarada;
- toda evidencia resuelve a un `event_id` de `evidence/events.jsonl` o a un `raw_ref` real (archivo:línea dentro de `evidence/raw/`);
- las confianzas están dentro del vocabulario;
- la sesión declarada es la del paquete.

Si la validación falla, reporta en `output/validation.md` y **no publica** — nunca corrige la salida en silencio. Código de salida 1.

Publica (en un workspace, `/pepper-export` usa `docs/pepper/discovery/<session_id>/`): `runtime-discovery.json/.md`, `validation.md`, los derivados `flows.json`, `candidate-rules.json`, `contradictions.json`, `unknowns.json`, `evidence-map.json`, y una copia de `evidence/events.jsonl` y `evidence/flow.json` para que las referencias resuelvan fuera del paquete. Espec: [fases/export.md](../../docs/documentacion/fases/export.md).
