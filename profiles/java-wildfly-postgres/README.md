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

## Lo que antes estaba en `profile.json` y el núcleo no leía

Movido aquí el 2026-09-30 (auditoría 2026-09-29): `rehydrate.steps`, `validation[]` y `collectors[].method|location|enable` eran documentación disfrazada de contrato — ningún código los leía como datos. El contenido se conserva tal cual, como prosa.

### Receta de rehydrate, paso a paso

1. determinar versiones de Java, WildFly y PostgreSQL a partir de los artefactos (fidelidad: reproducir, no modernizar)
2. levantar contenedor PostgreSQL con la versión detectada y log_statement=all
3. restaurar el respaldo o ejecutar los scripts de esquema
4. levantar contenedor WildFly con la versión detectada y el driver JDBC de PostgreSQL
5. configurar el datasource según standalone.xml/properties
6. desplegar el WAR en el directorio deployments
7. levantar el proxy HTTP de PEPPER delante del puerto de la aplicación
8. ejecutar validaciones

### Colectores: de dónde sale cada fuente y cómo se activa

| fuente | método | ubicación | cómo se activa | parser |
|---|---|---|---|---|
| `wildfly` | log_file | /opt/jboss/wildfly/standalone/log/server.log | subir nivel de log a DEBUG para los paquetes de la aplicación en standalone.xml antes del arranque | `parsers/wildfly-server-log.json` |
| `postgresql` | log_file | /var/lib/postgresql/data/log/ | log_statement=all y log_line_prefix con timestamp, pid y aplicación, configurados en el compose antes del arranque | `parsers/postgresql-log.json` |

### Qué se comprueba tras el arranque

- contenedores de WildFly y PostgreSQL en ejecución
- deployment del WAR en estado OK (sin *.failed en deployments)
- base de datos restaurada y aceptando conexiones
- datasource operativo (test-connection-in-pool exitoso)
- endpoint raíz de la aplicación responde a través del proxy
- sin ERROR/FATAL en server.log durante el arranque
