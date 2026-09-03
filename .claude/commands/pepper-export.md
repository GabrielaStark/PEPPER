---
description: Fase 6 · Valida la salida del discovery contra el contrato y la publica en docs/pepper/discovery/<session_id>/. Si el workspace es de stark, la entrega en docs/analysis/.
argument-hint: "<session_id>"
---

Lee `docs/documentacion/PRINCIPIOS.md` y aplica sus reglas como restricciones duras antes de actuar.

## 1. Valida y publica

```bash
python3 -m pepper export pepper-out/$ARGUMENTS/package --manifest pepper-out/$ARGUMENTS/package.evidence-manifest.json --out docs/pepper/discovery/$ARGUMENTS
```

El manifest externo es obligatorio. Si falta o está dentro del paquete, no sustituyas su función con el manifest interno: vuelve a Package para crear una raíz de confianza fuera del alcance normal de Discover.

Si el resultado es **RECHAZADO**, muestra los errores tal cual y NO corrijas la salida tú: vuelve a `/pepper-discover $ARGUMENTS` para que el agente la corrija sobre la evidencia. Una salida que no valida no se publica — ni "a mano", ni "solo esta vez".

## 2. Entrega a stark

- Copia `docs/pepper/discovery/$ARGUMENTS/runtime-discovery.md` como `docs/analysis/runtime-discovery-$ARGUMENTS.md` (crea `docs/analysis/` si no existe): es el análisis arqueológico que `arqueologo-codigo` lee al levantar requirements cuando stark se instale sobre este repo. Se commitea junto con `docs/pepper/`.
- Si existe `docs/REGLAS_DE_NEGOCIO.md`, **no lo edites**. Lista para el humano qué reglas candidatas, contradicciones y desconocidos convendría llevar a su sección 11 (Descubrimiento), con el mapeo de confianza del skill `evidencia-runtime` §3: lo más alto que PEPPER entrega es `inferida`; `contradicha` → `en-duda`; `desconocida` → pregunta abierta. Nada entra como `confirmada`.
- Aunque stark no vaya a usarse, `docs/pepper/discovery/$ARGUMENTS/` y `docs/analysis/` son el entregable final: es lo único que queda en el repo cuando se borra la herramienta.

## 3. Gate humano final ✋

El humano decide qué se convierte en conocimiento. Recuérdale, sin adornos: PEPPER produce evidencia de ejecución; solo una persona con nombre promueve una regla a `confirmada`.

Cierra confirmando qué se publicó y dónde, y qué sigue: otro flujo (`/pepper-observe <nombre-del-flujo>`) o el pipeline de stark.
