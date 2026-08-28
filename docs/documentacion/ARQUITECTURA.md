# Arquitectura

## Invariante central

> **El núcleo de PEPPER nunca conoce la tecnología del legacy.**

Todo conocimiento específico de un stack (cómo detectarlo, cómo levantarlo, cómo leer sus logs) entra al sistema como datos: un **perfil**. Si mañana un evento viene de IIS en vez de WildFly, el correlacionador no debe enterarse.

La prueba de fuego para cualquier cambio: *¿esta línea del núcleo dejaría de funcionar con otro stack?* Si sí, pertenece a un perfil.

## Vista general

```text
                      ┌────────────────────────────┐
                      │         PERFILES           │
                      │  detección / receta de     │
                      │  rehydrate / colectores /  │
                      │  parsers / validaciones    │
                      └─────────────┬──────────────┘
                                    │ (datos, no código del núcleo)
                                    ▼
┌─────────┐   ┌───────────┐   ┌─────────┐   ┌───────────┐   ┌──────────┐   ┌──────────┐   ┌────────┐
│ INSPECT │ → │ REHYDRATE │ → │ OBSERVE │ → │ CORRELATE │ → │ PACKAGE  │ → │ DISCOVER │ → │ EXPORT │
└─────────┘   └───────────┘   └─────────┘   └───────────┘   └──────────┘   └──────────┘   └────────┘
 identifica    reconstruye     captura       normaliza a     arma carpeta   agente externo  contrato
 stack y       entorno en      evidencia     eventos y       autocontenida  (Claude Code /  estable
 evidencia     contenedores    cruda         correlaciona    para agente    Codex)          p/ STARK
 faltante
```

## Módulos del núcleo

| Módulo | Entrada | Salida | Dependencia de tecnología |
|---|---|---|---|
| `inspect` | artefactos crudos | `stack-report`, perfil sugerido o borrador, evidencia faltante | ninguna (delegada al agente + señales de perfiles) |
| `rehydrate` | artefactos + perfil | entorno corriendo + `environment.json` | vía receta del perfil |
| `observe` | entorno corriendo + ventana de flujo | evidencia cruda por fuente | colectores genéricos + colectores del perfil |
| `correlate` | evidencia cruda | `events.jsonl` (schema común) + `flow.json` | vía parsers del perfil; correlación 100% genérica |
| `package` | evidencia correlacionada + código + config | paquete controlado | ninguna |
| `discover` | paquete controlado | `runtime-discovery.json/md` (lo escribe el agente) | ninguna |
| `export` | salida del agente | artefactos validados contra schema | ninguna |

## Escalera de soporte

El flujo de decisión ante cualquier legacy:

```text
¿Hay perfil que detecte el stack?
 ├─ SÍ  → escalón 1: pipeline completo automatizado
 └─ NO  → ¿el sistema corre o puede levantarse a mano?
           ├─ SÍ  → escalón 2: solo Observe con colectores genéricos
           │        (proxy HTTP, stdout/stderr de contenedores, logs de BD)
           └─ NO  → escalón 3: Inspect produce reporte BLOCKED
                    (stack identificado + faltantes + borrador de perfil)
```

Los tres escalones producen un entregable. `BLOCKED` es un resultado, no un fracaso.

## Colectores genéricos (escalón 2)

Funcionan para cualquier tecnología y son parte del núcleo:

- **Proxy HTTP inverso** delante de la aplicación: captura toda petición/respuesta e **inyecta un header de correlación** — la fuente de `correlation_id` cuando el legacy no emite ninguno.
- **stdout/stderr de contenedores**: igual para Java, PHP, .NET o lo que sea.
- **Logs del motor de BD**: los motores comunes (PostgreSQL, MySQL, Oracle, SQL Server) pueden configurarse para registrar cada sentencia.

Aquí está la sinergia clave con Rehydrate: **como PEPPER controla el entorno reconstruido, controla la observabilidad**. Puede activar niveles de log que jamás se activarían en producción. Rehydrate no solo revive el sistema; habilita una fidelidad de observación imposible en el ambiente original.

## Motor de análisis intercambiable

Discover no invoca APIs de ningún agente: el paquete controlado es una carpeta autocontenida con la evidencia, el código disponible y el prompt. La adaptación por agente es mínima: PEPPER genera `CLAUDE.md` y `AGENTS.md` en la raíz del paquete, ambos apuntando al mismo prompt de discovery. El resultado se valida contra `schemas/runtime-discovery.schema.json` venga de donde venga, lo que permite el modo de **contraste**: correr dos agentes sobre el mismo paquete y comparar conclusiones (coincidencia sube confianza; discrepancia marca revisión humana).

Durante Discover el agente opera bajo el **principio read-only**: lee, busca, correlaciona y reporta; no modifica código, datos, configuración ni servicios.

## Estados

```text
READY / PARTIAL / BLOCKED / FAILED
```

Definidos en `schemas/environment.schema.json`. `running` de un contenedor no implica `READY`: Rehydrate valida despliegue, restauración de datos, conectividad y ausencia de errores críticos de arranque (ver [rehydrate.md](fases/rehydrate.md)).

## Los contratos son la interfaz

Todo cruce de frontera está definido por un schema en [`schemas/`](../../schemas/):

- `profile.schema.json` — perfiles ↔ núcleo
- `parser.schema.json` — parsers declarativos de perfil ↔ normalización
- `environment.schema.json` — rehydrate ↔ resto del pipeline
- `session.schema.json` — observe ↔ correlate
- `event.schema.json` — cualquier fuente de evidencia ↔ correlación
- `flow.schema.json` — correlate ↔ package/agente
- `runtime-discovery.schema.json` — agente ↔ export ↔ STARK

Cualquier pieza (perfil, colector, agente, consumidor) es reemplazable mientras respete su schema.

## Estado de implementación

| Módulo | Estado |
|---|---|
| Correlate, Package, Export | implementados en Python (`pepper/`), probados contra el fixture |
| Discover | lo ejecuta el agente externo sobre el paquete; PEPPER solo lo prepara y valida su salida |
| Inspect, Rehydrate, Observe | especificados en [`fases/`](fases/), sin implementar |

Comandos: `python3 -m pepper correlate | package | export | demo`.
