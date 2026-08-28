# Entorno de referencia

Lo que la fase **Rehydrate** debería producir para este fixture, partiendo únicamente de `artifacts/`.

Vive en `expected/` a propósito: es la clave de respuestas, no un insumo. Si estuviera en `artifacts/`, el ejercicio de reconstrucción perdería sentido — un legacy real no llega con su compose.

> **No verificado**: se escribió en un entorno sin Docker ni red. Es especificación ejecutable, no algo probado. Al validarlo por primera vez, corregir lo que haga falta aquí y en el perfil `java-wildfly-postgres`, y recién entonces marcar ese perfil como `validated`.

## Piezas

| Archivo | Qué es |
|---|---|
| `docker-compose.yml` | PostgreSQL 9.6 (con `log_statement=all`) + WildFly 10 |
| `wildfly.Dockerfile` | driver JDBC como módulo, despliegue del WAR, datasource |

## Piezas que faltan para correrlo

Requieren descarga o build (por eso no están en el repo):

- `solicitudes.war` → `cd ../../artifacts/source && mvn package`
- `postgresql-9.4.1212.jar` → driver JDBC compatible con Java 8
- `module.xml` → declaración del módulo `org.postgresql` para JBoss
- `configure-datasource.cli` → script de `jboss-cli` que crea `SolicitudesDS` según `artifacts/configuration/standalone-fragment.xml`

## Para qué sirve levantarlo

Para capturar evidencia **real** y contrastarla con la sintética de `raw-evidence/`. El flujo a ejecutar:

```bash
# rechazado (ciudadano SUSPENDED)
curl -X POST localhost:8080/solicitudes/api/applications \
  -H 'Content-Type: application/json' \
  -d '{"citizenId":1003,"tipoTramite":"LICENCIA_FUNCIONAMIENTO"}'

# exitoso
curl -X POST localhost:8080/solicitudes/api/applications \
  -H 'Content-Type: application/json' \
  -d '{"citizenId":1001,"tipoTramite":"LICENCIA_FUNCIONAMIENTO"}'
```

Si la evidencia real difiere en forma de la sintética, **la sintética está mal** y hay que corregirla: existe para imitar a la real.
