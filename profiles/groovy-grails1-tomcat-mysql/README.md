# Perfil: groovy-grails1-tomcat-mysql

WAR de **Grails 1.3** (Groovy 1.7, Spring 3.0, Hibernate 3.3, GSP + SiteMesh, Liquibase 1.9, Quartz 2.1, con un SPA de React embebido en `js/bundle.*.js`) desplegado en **Tomcat** sobre **JDK 7**, con **MySQL 5.7**.

**Estado: `validated` (2026-09-22).** Corrido de punta a punta contra el legacy real (un fork localizado de OpenBoxes 0.8.x) con el respaldo de producción: mapa → `rehydrate --up` (PARTIAL: externos al stub; aislamiento vivo VERIFIED) → explorador con dos roles (62/64 pantallas cada uno) → correlate → package → discovery → `funcional.md` publicado por Export. Lo promueve a `validated` una persona tras revisar el documento.

## Contenido

| Archivo | Qué hace |
|---|---|
| `profile.json` | detección (7 señales, dentro del WAR); `rehydrate.datasource` = `groovy_config` (DataSource.groovy compilado, entorno `production`); `rehydrate.database` = MySQL con respaldo `sql_text`, imagen por versión del respaldo, sonda `mysql`, `localhost` → el app en la pila de red de la base; receta en 12 pasos; 2 colectores; validaciones |
| `extractors.json` | `jvm_class_inventory` (org/pih/warehouse), `view_templates` para GSP (resuelve `<warehouse:message>` / `<g:message>` con messages + messages_es), `archive_url_scan`, `groovy_controller_actions`, `groovy_url_mappings`, `groovy_config_values` (jobs con cron desde Config y `triggers`), `sql_dump` |
| `compose.template.yml` | red interna, MySQL con general log a stdout, stub con alias, Tomcat con `network_mode: service:db` (el datasource embebido apunta a `localhost:3306`), ingress |
| `restore.template.sh` | restaura un mysqldump de la **base de la aplicación** dentro de la base esperada (`CREATE DATABASE`/`USE` se ignoran, `DEFINER` → `CURRENT_USER`) y deja la marca en `pepper_meta.restored` |
| `parsers/tomcat-grails-stdout.json` | stdout de Tomcat: log4j de Grails (layout por defecto `%d [%t] %-5p %c{2} %x - %m%n`) + JULI OneLineFormatter |
| `parsers/mysql-general-log.json` | general log de MySQL 5.7 (`<ts>\t<conn> <Comando>\t<argumento>`), afinidad por id de conexión |

## Por qué la receta es como es

- **El datasource está compilado**, no en un properties: `WEB-INF/classes/DataSource$_run_closure*.class`. El núcleo lo reconstruye del bytecode (`pepper/inspect/groovyconfig.py`): driver, dialecto, usuario, contraseña (solo a `.env`) y la URL del entorno `production`.
- **`localhost` es la especificación, no un defecto.** En el servidor original MySQL vivía junto a Tomcat. Se reproduce con `network_mode: service:db` (`rehydrate.database.localhost = shared_network_namespace`); `isolate` lo acepta y verifica las redes y el DNS a través de `db`.
- **`sessionVariables=storage_engine=InnoDB` contra MySQL 5.7.36.** La variable `storage_engine` se eliminó en MySQL 5.7.5. Si el pool no conecta ("Unknown system variable 'storage_engine'"), producción debió usar la configuración externa (`~/.grails/<app>-config.properties`, Config.groovy:22-38). La receta prueba primero el WAR tal cual y solo después monta un override generado por PEPPER, declarado como desviación. **No verificado en runtime.**
- **La base se crea sola.** BootStrap corre Liquibase al arrancar (`install/install.xml` si la base está vacía, luego `changelog.xml`). Sin respaldo de la aplicación, el sistema levanta con esquema completo y solo datos semilla (usuarios admin/manager, roles ROLE_ADMIN/MANAGER/USER; `install/baseline-data.xml`). Por eso `startup_timeout_s` es 900.
- **Contraseñas de la app**: `user.password` = Base64(SHA-1(clave)) (PasswordCodec, `sun.misc.BASE64Encoder`, así que necesita JDK 8 o anterior). La credencial de prueba se fija solo en la base del contenedor, con la sonda del perfil (`mysql`), callando la sesión con `sql_log_off`.
- **Un respaldo del esquema `mysql` (sistema) no se restaura**: el núcleo lo detecta antes de levantar (`sql_dump.system_only`) y responde `BLOCKED` diciendo qué respaldo conseguir. Cargarlo traería los hashes de producción, los grants y `validate_password.so`, y dejaría fuera las cuentas del contenedor.
- **Rutas sin anotaciones.** Las acciones de un controlador Grails 1.x son closures asignadas en el constructor; `allowedMethods` da el verbo; `UrlMappings` agrega las rutas REST con plantillas. Los tres mecanismos `groovy_*` los leen del bytecode.
- **javap.** El de JDK 25 rechaza clases de Groovy 1.7 (`ACC_SYNTHETIC` en campos de major 47); el núcleo prueba con todos los javap de la máquina y anota cuál leyó qué. Con un JDK 8 instalado, el mapa sale completo.

## Lo que enseñó la corrida real (2026-09-22)

- **`storage_engine` sí rompe el pool** contra MySQL 5.7.36 ("Unknown system variable"): c3p0 reintenta sin fin y Tomcat queda "arrancado" sin app. `rehydrate.datasource.url_strip_params` lo quita de la URL y `rehydrate.extra_templates` monta `grails-config.properties` como `~/.grails/<app>-config.properties` con la URL limpia; queda como desviación. La configuración externa real de producción sigue siendo una pregunta abierta.
- **El general log de MySQL 5.7 no acepta `/proc/self/fd/1` ni `/dev/stdout`** como archivo: va a `/var/lib/mysql/general.log` y el sidecar `dblog` (`tail -F` sobre el volumen, solo lectura) lo saca por su stdout → `containers/dblog.log`.
- **mysqldump califica las vistas con el esquema de origen** (`origen`.`tabla`): `restore.sh` lee el nombre de la cabecera y lo reescribe a la base esperada.
- **El respaldo lo hizo `mariadb-dump`**: se restaura con `mariadb:<versión>` (`tool_images` por herramienta), no con un `mysql:<versión-MariaDB>` inexistente.
- **Imágenes solo amd64** (`mysql:5.7.36`, `tomcat:7-jre7`): en una Mac arm64 corren emuladas (`platform: ${PEPPER_PLATFORM}`); Tomcat arrancó en ~30 s aun así.
- **Parsers contra logs reales**: MySQL 63 502 eventos / 6 sin parsear (cabeceras); Tomcat 871 eventos / 67 sin parsear (`println` sin prefijo log4j) tras extender la continuación al SQL multilínea de DataService, líneas vacías y `Field error in object`. JULI usa OneLineFormatter (`INFO: Server startup in …`). El app no loguea el `X-Pepper-Correlation-Id`: la correlación con HTTP es por ventana y afinidad de hilo.
- **Comportamiento del sistema al arrancar**: `RefreshProductAvailabilityJob` corre de inmediato y produjo deadlocks de MySQL (evidencia del arranque, no de la ventana explorada).

## Lo que sigue pendiente (no bloquea `validated`)

- [ ] Conseguir la configuración externa real de producción (`~/.grails/<app>-config.*`) y comparar con la que PEPPER genera: hoy la URL limpia del datasource es una desviación declarada, no una copia del original.
- [ ] Un recorrido de negocio encadenado (solicitar → surtir → recibir) con un plan de `pepper explore`: la corrida de validación observó pantallas y rechazos, no el ciclo completo.

## Lo que antes estaba en `profile.json` y el núcleo no leía

Movido aquí el 2026-09-30 (auditoría 2026-09-29): `rehydrate.steps`, `validation[]` y `collectors[].method|location|enable` eran documentación disfrazada de contrato — ningún código los leía como datos. El contenido se conserva tal cual, como prosa.

### Receta de rehydrate, paso a paso

1. leer legacy/NOTAS.md (servidor, versiones, base) y contrastar con el artefacto; registrar discrepancias sin resolverlas
2. datasource (rehydrate.datasource = groovy_config): el núcleo reconstruye DataSource.groovy del bytecode con javap y toma environments.production.dataSource.{url,username,password}; Config.groovy aporta los hosts externos (correo, ldap, APIs)
3. respaldo (rehydrate.database.dump = sql_text): mysqldump/mariadb-dump de la BASE DE LA APLICACIÓN; si es el esquema de sistema `mysql` (user, db, tables_priv…) el núcleo responde BLOCKED: no es la base del sistema
4. versiones: MySQL de la cabecera del respaldo (Server version) → imagen mysql:<versión>; Tomcat/JDK de NOTAS.md (sin nota: tomcat:7-jre7, declarado como desviación); JDK ≤ 8 obligatorio (sun.misc.BASE64Encoder en PasswordCodec)
5. si la URL de production apunta a localhost (rehydrate.database.localhost = shared_network_namespace): el app corre con network_mode service:db, así localhost:3306 dentro del app ES la base; no se inventa otro host
6. si la URL lleva sessionVariables=storage_engine=InnoDB y el MySQL es >= 5.7.5 (la variable ya no existe), el pool no conecta: probar primero tal cual; si falla con "Unknown system variable 'storage_engine'", montar un openboxes-config.properties generado por PEPPER en /root/.grails/ que solo reemplace dataSource.url sin esa variable, y declararlo como desviación
7. generar docker-compose.yml desde compose.template.yml: red interna, db MySQL con general_log a stdout, stub con alias para cada host externo del artefacto, app Tomcat+JDK 7 en la pila de red de db, ingress (proxy de PEPPER) delante del puerto 8080
8. verificar el aislamiento con `python3 -m pepper isolate <compose> --hosts <hosts>` ANTES de levantar (network_mode service:db se acepta: hereda las redes y el DNS de db)
9. levantar db y stub; restaurar el respaldo con restore.sh DENTRO de la base que el artefacto espera (CREATE DATABASE/USE del respaldo se ignoran; DEFINER → CURRENT_USER); la marca queda en pepper_meta.restored
10. levantar app e ingress; el WAR ORIGINAL se monta como webapps/<nombre>.war (contexto /<nombre>); esperar 'Server startup in' de Tomcat — el primer arranque corre Liquibase y puede tardar minutos (startup_timeout_s)
11. fijar credenciales de prueba SOLO en la base del contenedor (pepper explore, credentials.sql): UPDATE user SET password = TO_BASE64(UNHEX(SHA1('<clave>'))) (PasswordCodec: SHA-1 + Base64); nunca en el legacy
12. escribir environment.json (PARTIAL si hubo stubs) y validation.md con desviaciones

### Colectores: de dónde sale cada fuente y cómo se activa

| fuente | método | ubicación | cómo se activa | parser |
|---|---|---|---|---|
| `tomcat-grails` | container_stdout | evidence/<session_id>/containers/app.log (via `pepper collect`; por el stdout del contenedor de Tomcat salen tanto log4j de Grails como JULI de Tomcat) | sin cambios en el artefacto: los niveles vienen del Config.groovy compilado (Config.groovy:176-240: org.pih.warehouse, grails.app.*, liquibase, quartz). Subir a DEBUG o activar org.hibernate.SQL exige un openboxes-config.groovy externo con bloque log4j (Config.groovy:22); solo si la primera sesión muestra que falta detalle, y se declara como desviación | `parsers/tomcat-grails-stdout.json` |
| `mysql` | container_stdout | evidence/<session_id>/containers/dblog.log (via `pepper collect`; el servicio `dblog` hace tail del general log de MySQL, que va a un archivo del volumen: MySQL 5.7 rechaza /proc/self/fd/1 y /dev/stdout como general_log_file — comprobado en la prueba real 2026-09-22) | --general-log=1 --general-log-file=/var/lib/mysql/general.log --log-output=FILE --log-timestamps=UTC en el command del contenedor db, antes del arranque; el sidecar `dblog` (alpine, `tail -F` sobre el volumen db-data en solo lectura) lo saca por su stdout | `parsers/mysql-general-log.json` |

### Qué se comprueba tras el arranque

- aislamiento verificado por el núcleo antes y después de levantar — `python3 -m pepper isolate <compose> --hosts <hosts externos del artefacto> --live`
- Tomcat desplegó el WAR en el contexto esperado (log 'Deploying web application archive' + 'Server startup in') y sin 'SEVERE: Error listenerStart'
- el pool c3p0 conectó: no aparece 'Connection could not be created' ni 'Unknown system variable' en el log del app
- Liquibase terminó: 'Finished running liquibase changelog(s)!' y la tabla DATABASECHANGELOG trae el último changeset del WAR (0.8.x/changelog-2021-07-27-2153-alter-table-invoice-add-column-date-posted.xml en este legacy) — `docker compose exec db mysql -uroot -p"$DB_PASSWORD" -e "select id, filename from <db>.DATABASECHANGELOG order by dateexecuted desc limit 3"`
- GET /<contexto>/ responde por el ingress (302 a /<contexto>/auth/login) y la pantalla de login renderiza (campos username/password)
- el general log de MySQL muestra conexiones del app y sentencias de Hibernate sobre la base esperada
- el stub no registró peticiones durante el arranque; los hosts externos del WAR resuelven al stub dentro del contenedor db (getent hosts)
