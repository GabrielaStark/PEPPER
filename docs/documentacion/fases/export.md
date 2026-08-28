# Fase 4 — Export y contrato de salida

## Objetivo

Validar la salida del agente contra el contrato y entregarla en un formato estable, reutilizable y consumible por humanos y por otras herramientas (STARK entre ellas).

## Salida mínima

```text
runtime-discovery.md      (lectura humana)
runtime-discovery.json    (contrato: schemas/runtime-discovery.schema.json)
```

Artefactos adicionales opcionales, derivados del mismo JSON:

```text
flows.json / candidate-rules.json / contradictions.json
unknowns.json / dependencies.json / evidence-map.json
```

## Reglas del contrato

1. **Toda conclusión referencia evidencia.** Cada regla, paso, query o contradicción apunta a IDs de evidencia, y cada evidencia apunta a un evento (`events.jsonl`) o a una línea cruda (`raw_ref`). Una conclusión sin evidencia no pasa la validación.
2. **Confianza explícita y acotada**: `confirmada | fuertemente_sustentada | candidata | desconocida | contradicha`.
3. **Los desconocidos son parte de la salida.** Lo que no pudo determinarse se declara en `unknowns`, no se omite.
4. **La salida es independiente del motor.** El JSON no cambia según qué agente lo generó; el campo `engine` solo registra quién fue.
5. **El schema se versiona.** Cambios incompatibles suben versión mayor; STARK y cualquier consumidor se acoplan a la versión, no al archivo.

## Validación en Export

**Estado: implementada** (`pepper/export/`).

```bash
python3 -m pepper export <paquete>/ --out <publicación>/
```

```text
output/runtime-discovery.json del paquete
→ ¿valida contra runtime-discovery.schema.json?
→ ¿toda conclusión referencia evidencia declarada?
→ ¿toda evidencia resuelve a un event_id de events.jsonl o a un raw_ref real (archivo:línea)?
→ ¿confianzas dentro del vocabulario?  (lo impone el schema)
→ ¿la sesión declarada es la del paquete?
→ publicar
```

Si la validación falla, Export escribe el detalle en `output/validation.md`, termina con código 1 y **no publica** — la salida inválida no se corrige en silencio.

Lo publicado: `runtime-discovery.json/.md`, `validation.md`, los derivados (`flows.json`, `candidate-rules.json`, `contradictions.json`, `unknowns.json`, `evidence-map.json`) y una copia de `evidence/events.jsonl` y `evidence/flow.json`, para que las referencias de evidencia resuelvan sin necesitar el paquete.

## Consumo por STARK

```text
STARK  descubrimiento estático   (código, docs, configuración)
PEPPER descubrimiento dinámico   (comportamiento real observado)
        ↓
   comparación estático ↔ dinámico → contradicciones → decisión humana
```

Mapeo de confianzas PEPPER → procedencia de stark (`REGLAS_DE_NEGOCIO.md`, B-D11). En stark, `confirmada` exige que una persona con nombre responda por la regla; PEPPER aporta evidencia, no personas, así que **nada entra como `confirmada`**:

```text
confirmada / fuertemente_sustentada → inferida  (código Y runtime la respaldan)
candidata                           → inferida con nota de confianza baja, o pregunta abierta (sección 11)
contradicha                         → en-duda + contradicción en la sección 11
desconocida                         → pregunta abierta en la sección 11
```

Dónde aterriza: `runtime-discovery.md` se copia a `docs/analysis/runtime-discovery-<session_id>.md` (el input de `arqueologo-codigo` en reingeniería); para `REGLAS_DE_NEGOCIO.md`, PEPPER lista qué llevar a la sección 11 y el humano lo incorpora — el archivo no se edita automáticamente.

PEPPER no reemplaza el onboarding de stark: lo complementa con la fuente que el análisis estático no puede ver — la ejecución real.
