# Perfil: groovy-grails1-tomcat-mysql

WAR de **Grails 1.3** (Groovy 1.7, Spring 3.0, Hibernate 3.3, GSP + SiteMesh, Liquibase 1.9, Quartz 2.1, con un SPA de React embebido en `js/bundle.*.js`) desplegado en **Tomcat** sobre **JDK 7**, con **MySQL 5.7**.

**Estado: `draft`, corrido de punta a punta el 2026-09-22** contra el legacy real (un fork localizado de OpenBoxes 0.8.x) con el respaldo de producción: mapa → `rehydrate --up` (PARTIAL: externos al stub; aislamiento vivo VERIFIED) → explorador con dos roles (62/64 pantallas cada uno) → correlate → package → discovery → `funcional.md` publicado por Export. Lo promueve a `validated` una persona tras revisar el documento.

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

## Pendiente para `validated`

- [ ] Que una persona revise `docs/pepper/funcional.md` de la corrida y confirme que describe el sistema.
- [ ] Conseguir la configuración externa real de producción (`~/.grails/<app>-config.*`) y comparar con la generada.
