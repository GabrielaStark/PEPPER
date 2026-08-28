# observe

Esta fase la ejecuta el agente [`observador-runtime`](../../.claude/agents/observador-runtime.md) (`/pepper-observe <flujo>`): prepara los colectores, delimita la ventana mientras el humano ejecuta el flujo y deja `evidence/<session_id>/` con `session.json` (contrato: [schemas/session.schema.json](../../schemas/session.schema.json)).

Pendiente en el núcleo — los colectores genéricos: el **proxy HTTP inverso** que inyecta `correlation_id` (hoy, sin proxy, la correlación va por afinidad y ventana temporal) y el parser de stdout/stderr de contenedores. Los logs de aplicación los cubren los parsers del perfil; el log del motor de BD, el parser que el perfil declare.

Espec: [docs/documentacion/fases/observe.md](../../docs/documentacion/fases/observe.md).
