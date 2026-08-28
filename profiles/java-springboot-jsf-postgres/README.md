# Perfil: java-springboot-jsf-postgres

WAR de **Spring Boot 1.5** con **JSF (JoinFaces / Mojarra) + PrimeFaces + Hibernate** sobre **PostgreSQL**, desplegado en **WildFly** en producción (`jboss-web.xml` dentro del WAR) aunque el MANIFEST lo declare ejecutable.

**Estado: `draft`** — redactado durante Inspect de un legacy real y refinado tras rehidratarlo a mano: WildFly 21 + PostgreSQL 16, WAR original sin modificar, red interna, stub para lo externo, servidores foráneos re-apuntados. Lo que falta para `validated` es ejecutar la receta **desde la plantilla** de punta a punta (la primera corrida se armó a mano) y capturar evidencia real con sus parsers.

## Contenido

| Archivo | Estado |
|---|---|
| `profile.json` | detección (mira dentro del WAR), receta en 9 pasos, colectores, validaciones — valida contra `schemas/profile.schema.json` |
| `compose.template.yml` | plantilla con `{{variables}}`: red interna, base en la IP esperada, stub con alias, WildFly, ingress |
| `parsers/springboot-app.json` | patrón `FILE_LOG_PATTERN` de Spring Boot 1.5 (para `java -jar`) |
| `parsers/postgresql-log.json` | `log_statement=all` con `log_line_prefix='%m [%p] %u@%d '` |

## Lecciones del primer legacy (por qué la receta es como es)

- **Con `java -jar` no arranca**: JoinFaces 2.4 no escanea `war:file:`; el Tomcat 8.5.11 embebido tiene un NPE en JASPIC; y `WEB-INF/lib` trae jars de API sin código que, según el orden del zip, sombrean a Mojarra (`ClassFormatError: Absent Code attribute`). En WildFly nada de eso importa: el servidor pone su JSF y sus APIs. → D20.
- **El respaldo traía `USER MAPPING` con la contraseña de una base foránea de producción**, y una vista con `dblink` la alcanzó a través de la VPN de la máquina. → red `internal` y re-apunte de `pg_foreign_server` al stub en `restore.sh` (D19).
- **La nota del humano decidió el servidor**: "producción es WildFly 21" en una línea (D18). La cabecera del respaldo decía servidor 10.6 y la nota PostgreSQL 16: discrepancia registrada, no resuelta.

## Pendiente

- [ ] Parser para `server.log` de WildFly cuando el WAR se despliega ahí (hoy el colector `springboot` asume `java -jar`); el perfil `java-wildfly-postgres` ya trae uno reutilizable.
- [ ] Ejecutar la receta desde la plantilla, no a mano.
- [ ] Capturar evidencia real de un flujo y probar los parsers contra ella.
