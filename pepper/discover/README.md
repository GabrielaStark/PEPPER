# discover

Esta fase la ejecuta el agente [`descubridor-runtime`](../../.claude/agents/descubridor-runtime.md) (`/pepper-discover <session_id>`) sobre el paquete controlado, bajo la constitución [`discovery-runtime`](../../.claude/skills/discovery-runtime/SKILL.md) — que es también el `prompt.md` que `pepper package` copia dentro de cada paquete, para que cualquier agente (Claude Code, Codex) pueda hacer el discovery sin este repo.

El núcleo aporta `pepper export --check` para que el agente se auto-verifique antes de entregar.

Modo contraste: dos agentes sobre el mismo paquete, salidas comparadas por el humano. Espec: [docs/documentacion/fases/discover.md](../../docs/documentacion/fases/discover.md).
