---
description: Fase 6 · Valida la salida del discovery contra el contrato y la publica - la de esta sesión en docs/pepper/discovery/<session_id>/ y el documento del sistema en docs/pepper/funcional.md. Si el workspace es de stark, lo entrega en docs/analysis/.
argument-hint: "<session_id>"
---

Lee `docs/documentacion/PRINCIPIOS.md` y aplica sus reglas como restricciones duras antes de actuar.

## 1. Valida y publica

```bash
python3 -m pepper export pepper-out/$ARGUMENTS/package --manifest pepper-out/$ARGUMENTS/package.evidence-manifest.json --out docs/pepper/discovery/$ARGUMENTS --system-doc docs/pepper
```

El manifest externo es obligatorio. Si falta o está dentro del paquete, no sustituyas su función con el manifest interno: vuelve a Package para crear una raíz de confianza fuera del alcance normal de Discover.

Si el resultado es **RECHAZADO**, muestra los errores tal cual y NO corrijas la salida tú: vuelve a `/pepper-discover $ARGUMENTS` para que el agente la corrija sobre la evidencia. Una salida que no valida no se publica — ni "a mano", ni "solo esta vez".

Publica dos cosas: la salida de esta sesión (`docs/pepper/discovery/$ARGUMENTS/funcional.md|json` + `validation.md`) y **el documento del sistema** (`docs/pepper/funcional.md|json`), que es el vigente: el discovery es acumulativo y la siguiente sesión lo recibe como `previous/`.

## 2. Entrega a stark

- Copia `docs/pepper/funcional.md` como `docs/analysis/funcional.md` (crea `docs/analysis/` si no existe): es el análisis que `arqueologo-codigo` lee al levantar requirements cuando stark se instale sobre este repo. Se commitea junto con `docs/pepper/`.
- Si existe `docs/REGLAS_DE_NEGOCIO.md`, **no lo edites**. Lista para el humano qué reglas, contradicciones y desconocidos convendría llevar a su sección 11, con el mapeo de confianza del skill `evidencia-runtime` §3: lo más alto que PEPPER entrega es `inferida`; `contradicha` → `en-duda`; `desconocida` → pregunta abierta. Nada entra como `confirmada`.
- Aunque stark no vaya a usarse, `docs/pepper/funcional.md` es el entregable final: es lo único que queda en el repo cuando se borra la herramienta.

## 3. Gate humano final ✋

El humano decide qué se convierte en conocimiento. Recuérdale, sin adornos: PEPPER produce evidencia; solo una persona con nombre promueve una regla a `confirmada`.

Cierra confirmando qué se publicó y dónde, y qué sigue: otro flujo (`/pepper-observe <nombre-del-flujo>`) — los desconocidos de la sección 12 dicen cuál — o el pipeline de stark.
