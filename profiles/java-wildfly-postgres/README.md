# Perfil: java-wildfly-postgres

Primer perfil de PEPPER. Existe para probar la tubería completa de punta a punta (secuencia, no límite: la herramienta es para cualquier legacy).

**Estado: `draft`** — los parsers están probados contra la evidencia sintética del fixture; la receta de Rehydrate no se ha ejecutado contra un legacy real.

## Contenido

| Archivo | Estado |
|---|---|
| `profile.json` | detección, receta, colectores, validaciones — valida contra `schemas/profile.schema.json` |
| `parsers/wildfly-server-log.json` | probado: `server.log` con formato por defecto, stack traces como continuación, ruido de pool |
| `parsers/postgresql-log.json` | probado: `log_statement=all`, parámetros de las líneas `DETAIL` fusionados en la sentencia |

## Para pasar a `validated`

- [ ] `compose.template.yml` (WildFly + PostgreSQL + proxy de PEPPER, versiones parametrizadas). El [entorno de referencia del fixture](../../examples/legacy-demo/expected/reference-environment/) es el punto de partida.
- [ ] Rehydrate completo del legacy-demo con las validaciones en verde.
- [ ] Capturar evidencia real del legacy-demo levantado y contrastarla con la sintética de `raw-evidence/`; corregir la sintética (o los parsers) donde difieran.
