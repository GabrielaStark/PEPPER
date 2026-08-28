# rehydrate

Esta fase la ejecuta el agente [`rehidratador-legacy`](../../.claude/agents/rehidratador-legacy.md) (`/pepper-rehydrate`): plan de reconstrucción desde la receta del perfil → aprobación humana → contenedores → validaciones → `docs/pepper/environment.json` (contrato: [schemas/environment.schema.json](../../schemas/environment.schema.json)).

El núcleo aporta `pepper validate` para el `environment.json`. Un runner determinístico de recetas (`compose.template.yml` → compose concreto) es trabajo pendiente de este módulo; hoy lo hace el agente con supervisión.

Espec: [docs/documentacion/fases/rehydrate.md](../../docs/documentacion/fases/rehydrate.md).
