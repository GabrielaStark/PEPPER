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

## Lo que antes estaba en `profile.json` y el núcleo no leía

Movido aquí el 2026-09-30 (auditoría 2026-09-29): `rehydrate.steps`, `validation[]` y `collectors[].method|location|enable` eran documentación disfrazada de contrato — ningún código los leía como datos. El contenido se conserva tal cual, como prosa.

### Receta de rehydrate, paso a paso

1. leer legacy/NOTAS.md: servidor de aplicaciones y versión, Java, motor y versión de la base (D18); contrastar con la cabecera del respaldo y los descriptores del WAR, y registrar discrepancias
2. extraer a un directorio temporal (nunca en legacy/) la configuración embebida del WAR: META-INF/maven/*/pom.xml y WEB-INF/classes/application*.yml; elegir el perfil de Spring completo (url, username, password del datasource)
3. derivar el ambiente que el artefacto espera: subnet y IP del datasource, nombre de la base, usuario, IPs/hosts de servicios externos, puerto, rutas de archivos (D17)
4. generar docker-compose.yml desde compose.template.yml: red interna, base en la IP esperada con el nombre esperado, stub con alias para cada host externo, ingress publicando el puerto (D19)
5. verificar el aislamiento con `python3 -m pepper isolate <compose> --hosts <hosts>` ANTES de levantar nada; si no está en verde, corregir el compose (D19)
6. si el WAR trae WEB-INF/jboss-web.xml → imagen jboss/wildfly de la versión de NOTAS.md y despliegue del WAR original en standalone/deployments (D20); solo sin descriptores → java -jar
7. levantar db y stub; restaurar el respaldo dentro de la base con el nombre esperado usando un pg_restore >= versión del pg_dump (cabecera), sin dueños ni privilegios; crear vacíos los roles que el respaldo referencie; re-apuntar todo pg_foreign_server al stub
8. levantar app e ingress; esperar WFLYSRV0010 (WildFly) o 'Started Application' (java -jar)
9. validar: despliegue sin ERROR, 'profiles are active' con el perfil elegido, JSF inicializado, raíz y login responden, sentencias JDBC de la app en el log de la base, stub sin peticiones al arrancar, el contenedor db NO alcanza los hosts foráneos originales
10. escribir environment.json (PARTIAL si algo externo quedó stubeado) y validation.md con las desviaciones: perfil de Spring usado, versiones, discrepancias con NOTAS.md, hallazgos del artefacto

### Colectores: de dónde sale cada fuente y cómo se activa

| fuente | método | ubicación | cómo se activa | parser |
|---|---|---|---|---|
| `wildfly` | container_stdout | evidence/<session_id>/containers/app.log (via `pepper collect`) | el WAR desplegado en WildFly loggea a consola; `pepper collect` lo captura con docker logs --timestamps | `parsers/wildfly-server.json` |
| `postgresql` | container_stdout | evidence/<session_id>/containers/db.err.log (via `pepper collect`; el motor loggea a stderr) | log_statement=all y log_line_prefix='%m [%p] %u@%d ' configurados en el compose antes del arranque | `parsers/postgresql-log.json` |
| `springboot` | log_file | /var/log/pepper/app.log | solo en modo java -jar: arrancar con --logging.file=/var/log/pepper/app.log --logging.level.<paquete>=DEBUG --logging.level.org.hibernate.SQL=DEBUG | `parsers/springboot-app.json` |

### Qué se comprueba tras el arranque

- aislamiento verificado por el núcleo antes y después de levantar — `python3 -m pepper isolate <compose> --hosts <hosts externos del artefacto> --live`
- WildFly desplegó el WAR (WFLYSRV0010) o Spring Boot arrancó (Started Application)
- 'The following profiles are active' muestra el perfil de Spring elegido
- JSF inicializado (Initializing Mojarra / WFLYJSF0007)
- sin líneas ERROR en el arranque
- raíz responde (302 a login con Spring Security) y la página de login renderiza como JSF (marcadores javax.faces / ViewState)
- la base tiene las tablas del respaldo y el log de PostgreSQL muestra conexiones 'PostgreSQL JDBC Driver' y sentencias de Hibernate
- el stub no registró peticiones durante el arranque; los hosts externos del WAR resuelven al stub (getent hosts)
- pg_foreign_server apunta al stub; el contenedor db no alcanza los hosts foráneos originales (pg_isready → no response)
