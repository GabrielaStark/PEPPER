# Fase 3 — Discover

## Objetivo

Transformar la evidencia correlacionada en conocimiento: flujos observados, reglas de negocio **candidatas**, dependencias, contradicciones y desconocidos — cada conclusión respaldada por evidencia.

## El paquete controlado

Discover no opera sobre el sistema vivo sino sobre una carpeta autocontenida que arma `pepper package`:

```text
<paquete>/
├── README.md                   qué hay, por dónde empezar, qué se espera
├── CLAUDE.md                   → apunta al prompt (lo lee Claude Code)
├── AGENTS.md                   → apunta al prompt (lo lee Codex)
├── prompt.md                   la skill discovery-runtime sin frontmatter (incluye la comparación runtime ↔ código)
├── session.json
├── evidence/
│   ├── flow.md                 la secuencia correlacionada, legible — punto de partida
│   ├── flow.json               lo mismo, estructurado (schemas/flow.schema.json)
│   ├── events.jsonl            los eventos normalizados (schemas/event.schema.json)
│   ├── reduction.md            qué se descartó y por qué
│   └── raw/                    evidencia cruda; los raw_ref resuelven aquí
├── legacy/                     source/, configuration/, docs/, ... según lo que exista
├── schemas/
│   └── runtime-discovery.schema.json
└── output/                     aquí escribe el agente
```

## Motor intercambiable

El agente se ejecuta **dentro del paquete** ("aquí está la carpeta, corre tu agente"). PEPPER no invoca APIs específicas: la adaptación por agente son dos archivos de texto (`CLAUDE.md`, `AGENTS.md`). La salida se valida contra el schema venga de quien venga.

**Modo contraste (opcional):** correr dos agentes sobre el mismo paquete y comparar salidas. Coincidencia por caminos distintos sube la confianza de una regla; discrepancia genera un ítem de revisión humana.

## Principio read-only

Durante Discover el agente puede: leer, buscar, inspeccionar, correlacionar, analizar, reportar.

No debe: modificar código, cambiar datos, tocar configuración, reiniciar servicios, desplegar, corregir defectos, hacer commit o push. **PEPPER descubre; no repara.**

## Qué debe responder

¿Qué flujo ocurrió? ¿Qué componentes participaron y en qué secuencia? ¿Qué validaciones parecen existir? ¿Qué datos se consultaron y qué tablas cambiaron? ¿Qué bifurcaciones y dependencias aparecen? ¿Qué reglas de negocio son candidatas y con qué evidencia? ¿Qué no puede determinarse? ¿Qué contradice al código o a la documentación?

## Modelo de confianza

Cada conclusión lleva confianza explícita:

```text
confirmada / fuertemente_sustentada / candidata / desconocida / contradicha
```

Ejemplo de regla candidata:

```text
Regla candidata: el sistema parece validar el estado del ciudadano
antes de guardar una solicitud.
Evidencia: E-034 validateCitizen · E-037 SELECT citizen.status · E-041 INSERT application
Confianza: fuertemente_sustentada
```

## Límite del discovery

PEPPER nunca afirma "descubrí todas las reglas de negocio". Una regla solo puede descubrirse dinámicamente **si deja rastro observable**. La salida correcta es: *flujos observados y reglas candidatas respaldadas por evidencia de ejecución*. Lo demás va a `unknowns`. **El humano decide qué se convierte en conocimiento.**

## Comparación runtime vs código

Cuando hay código fuente, el agente contrasta la ruta estática esperada contra la observada (sección «Comparación runtime ↔ código» de la skill [`discovery-runtime`](../../../.claude/skills/discovery-runtime/SKILL.md)). Toda divergencia se reporta como contradicción con causas posibles (configuración, condición no documentada, código muerto, dependencia del ambiente, documentación desactualizada, análisis estático incompleto) y queda marcada para validación humana.
