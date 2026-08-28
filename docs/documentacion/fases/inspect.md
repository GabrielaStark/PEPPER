# Fase 1 — Inspect

## Objetivo

Antes de intentar levantar nada: mirar los artefactos del legacy y decir, con evidencia, qué stack es, qué necesita para correr, qué falta y en qué escalón de soporte cae.

**Estado**: la ejecuta el agente [`inspector-legacy`](../../../.claude/agents/inspector-legacy.md) (`/pepper-inspect`); el núcleo aporta `pepper detect` (evaluación determinística de las señales de los perfiles) y `pepper validate` (contratos).

## Entrada

Un directorio con lo que haya del legacy — código, WAR/JAR/EAR, dist, respaldos de base de datos, configuración, scripts, certificados, notas. Sin estructura obligatoria: ordenar el desorden es el trabajo de esta fase.

## Flujo

```text
inventario (Glob, unzip -l sin extraer)
→ detección de perfil (pepper detect: señales con peso, min_score)
→ identificación del stack con evidencia por archivo
→ dependencias y faltantes (contra required_inputs del perfil)
→ borrador de perfil, solo si no hay perfil (skill perfil-stack)
→ docs/pepper/stack-report.md con veredicto
```

## `pepper detect`

Evalúa las `detection.signals` de cada perfil sobre el directorio: `file_exists`, `extension`, `directory` (globs sobre nombre o ruta relativa) y `file_content` (regex dentro de archivos que cumplen un glob). Cada señal que acierta suma su peso; `applicable` si el puntaje alcanza `min_score`. Reporta qué archivo disparó cada señal, para que el humano pueda auditar la detección.

```text
detect · legacy/ · 1 perfil(es) evaluados
  ✓ java-wildfly-postgres (draft) — puntaje 9 / mínimo 4
      + file_exists 'pom.xml' → source/pom.xml  (+1)
      + file_exists 'standalone*.xml' → configuration/standalone-fragment.xml  (+3)
      + file_content 'urn:jboss:domain' → configuration/standalone-fragment.xml  (+3)
      + file_content 'jdbc:postgresql' → configuration/application.properties  (+2)
```

## Veredicto

| Veredicto | Significa |
|---|---|
| `READY-candidato` | los `required_inputs` están; la reconstrucción parece viable |
| `PARTIAL` | falta algo que degrada (servicio externo, certificado) pero el núcleo puede levantarse |
| `BLOCKED` | falta algo sin lo cual no arranca; el reporte dice qué y qué artefacto lo resolvería |

`BLOCKED` es un entregable de primera clase, no un fracaso.

## Salida

`docs/pepper/stack-report.md` (inventario, stack con evidencia, dependencias, faltantes, perfil, escalón y veredicto, hallazgos de seguridad por ubicación, preguntas numeradas) y, cuando no hay perfil, `profiles/<id>/` en `draft` validado contra los schemas.

## Reglas

- Cada versión cita el archivo que la evidencia; "desconocida" cuando no hay, nunca "probablemente".
- Nada se extrae, modifica ni ejecuta dentro de los artefactos.
- Nada se inventa: un datasource que no aparece es un faltante.
- Una credencial hallada se reporta por ubicación, sin su valor.
