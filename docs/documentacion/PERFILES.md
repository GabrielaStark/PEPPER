# Perfiles

Un **perfil** es todo el conocimiento de un stack tecnológico empaquetado como **datos**: `profiles/<id>/` con `profile.json` (contrato: [`schemas/profile.schema.json`](../../schemas/profile.schema.json)), `extractors.json` (contrato: [`schemas/extractors.schema.json`](../../schemas/extractors.schema.json)), plantillas de compose y restore, parsers y fixtures. Ningún archivo de un perfil es Python.

## El modelo honesto: el núcleo conoce formatos, no sistemas

Hasta la auditoría 2026-09-29 este documento decía "el núcleo no conoce tecnologías". No era cierto: el 22 % del núcleo leía bytecode de la JVM, Groovy compilado, YAML de Spring, `pg_dump -Fc` y SQL de MySQL, y el único perfil que no era Spring costó 2.5 líneas de Python por cada línea de perfil. La formulación que el código sí cumple (Principio 4):

> El núcleo conoce **formatos** (bytecode JVM, Groovy compilado, YAML de Spring, `pg_dump` custom, SQL en texto, plantillas y configuración como texto, `.env`/`.properties`/`.ini`, JSON, XML). Un **sistema nuevo de un formato conocido** es solo un perfil. Un **formato nuevo** es Python: un lector en `pepper/inspect/readers/` (o el módulo que toque) con pruebas, una fila en las tablas de abajo y una entrada en su schema.

Lo que eso cuesta, medido en este repositorio:

| Caso | Qué hace falta | Ejemplo |
|---|---|---|
| Otro sistema del mismo stack | nada: el perfil ya aplica (escalón 1) | un segundo WAR de Spring Boot + JSF + PostgreSQL |
| Otro stack, mismos formatos | un perfil: `profile.json`, `extractors.json`, plantillas, parsers, fixtures; **cero Python** | `php-apache-mysql`: fuente en texto (`regex_extractor`, `view_templates`, `archive_url_scan`), `.env` (`key_value`), mysqldump (`sql_dump`) |
| Un formato que el núcleo no lee | un lector genérico en el núcleo, con pruebas y schema, **y** el perfil que lo usa | Groovy compilado (`groovy_*`, 2026-09-21); `.env`/JSON/XML para el datasource (2026-09-29); un respaldo de SQL Server o de Oracle mañana |

## Qué aporta un perfil

| Aporte | Archivo | Ejemplo |
|---|---|---|
| **Detección** | `profile.json › detection` | hay un `.war`, `pom.xml` con spring-boot, `application*.yml` con jdbc:postgresql; o `*.php` y un `.env` con `DB_CONNECTION=mysql` |
| **Extractores del mapa** | `extractors.json` | qué mecanismo corre sobre qué miembros y con qué regex; qué tablas son catálogos y qué columnas son estados |
| **Receta de rehydrate** | `profile.json › rehydrate` + `compose.template.yml` + `restore.template.sh` | imagen del servidor por versión, cómo leer el datasource, qué base fabricar y cómo sondearla, cómo restaurar, qué dice el log cuando arrancó o falló |
| **Colectores** | `profile.json › collectors` | qué archivo de `evidence/<sid>/containers/` normaliza qué parser; cómo se activa cada fuente lo dice el compose y lo explica el README del perfil |
| **Parsers** | `parsers/*.json` ([contrato](../../schemas/parser.schema.json)) | regex de la línea, timestamp, campos, tipo de evento, continuaciones, ruido, afinidad |
| **Lectura de formularios** | `profile.json › http` | qué campo nombra la acción (`javax.faces.source`, `_action_save`, `_method`), qué campos son ruido, cómo se acorta un nombre |
| **Fixtures** | `fixtures/` | logs sintéticos por parser, configuración sintética, un respaldo sintético (`synthesize.py`) y `expected.json`: lo que `tests/test_perfiles.py` comprueba en CI sin un legacy real |

## Los lectores del núcleo (lo que un perfil puede parametrizar)

### Mecanismos de `pepper map` (`pepper/inspect/readers/`)

| Mecanismo | Formato que lee | Superficies | Necesita |
|---|---|---|---|
| `regex_extractor` | cualquier miembro de texto, con una regex de grupos nombrados | la que el perfil declare: `entrypoints`, `jobs` o `external_dependencies` | — |
| `view_templates` | plantillas de texto (XHTML, GSP, Blade, PHP…) con regex por título, encabezado, campo, botón, mensaje, condición, inclusión; bundle `clave=valor` opcional | `screens` | — |
| `archive_url_scan` | URLs incrustadas en miembros de texto | `external_dependencies` | — |
| `config_hosts` | archivos `clave: valor` con hosts/URLs | `external_dependencies` (notas) | — |
| `sql_dump` | SQL en texto (mysqldump, mariadb-dump, pg_dump plano) | `data_stores`, `catalogs` | el respaldo |
| `pg_dump_custom` | `pg_dump -Fc` leído en Python puro | `data_stores`, `catalogs` | el respaldo |
| `jvm_route_annotations` | bytecode de la JVM (`@*Mapping`, `@Scheduled`) | `entrypoints`, `jobs` | `javap` |
| `jvm_class_inventory` | bytecode de la JVM (métodos, constantes, cadenas) | `classes` | `javap` |
| `groovy_controller_actions` | controladores Grails compilados | `entrypoints` | `javap` |
| `groovy_url_mappings` | `UrlMappings` de Grails compilado | `entrypoints` | `javap` |
| `groovy_config_values` | `Config`/`DataSource` de Grails compilados | `jobs` (y notas) | `javap` |

Una superficie que ningún mecanismo del perfil cubre no sale como cero: sale como **hueco declarado** en el mapa (`complete: false`). `pepper map` valida `extractors.json` contra su contrato antes de correr y dice qué clave está mal.

### Mecanismos de `rehydrate.datasource`

| Mecanismo | Formato | Cómo se declara |
|---|---|---|
| `key_value` | `.env`, `.properties`, `.ini`, YAML plano (`CLAVE=valor`, `clave: valor`, secciones `[x]`) | `files` (globs) + `keys` (qué clave es `url` o `host`/`port`/`db`/`user`/`password`/`engine`) |
| `json` | JSON, claves con puntos (`ConnectionStrings.Default`) | `files` + `keys` |
| `xml` | ruta simple de elementos y atributos (`configuration/connectionStrings/add[@name=Default]/@connectionString`) | `files` + `keys` |
| `spring_config` | `application*.yml|properties` dentro del artefacto, con perfiles de Spring | `config_patterns` |
| `groovy_config` | `DataSource.groovy` compilado, reconstruido del bytecode | `class_root`, `script`, `environment_key`, `prefer`, `keys` |

`datasource.url_pattern` (regex con grupos `engine`/`host`/`port`/`db` y opcionales `user`/`password`) reemplaza a la URL JDBC cuando la URL del stack tiene otra forma (`postgres://u:p@h/db`, `Server=h;Database=db;…`, `mysql:host=h;dbname=db`). Lo que la configuración no dice es BLOCKED con el archivo y la clave que faltó; nunca se adivina.

### Formatos de respaldo (`rehydrate.database.dump.format`)

`pg_dump_custom` (`pg_dump -Fc`) y `sql_text` (mysqldump, mariadb-dump, pg_dump plano). Otro motor es un formato nuevo: lector + fila aquí.

### Desplegables (`rehydrate.artifact_kind`)

`archive` (zip: WAR/JAR/EAR, o lo que `artifact_suffixes` declare) o `directory` (una carpeta bajo `legacy/`: PHP, Node con fuente, un dist estático). Una carpeta se empaca como `legacy.tar` junto al compose para que el contenedor reciba **un archivo** `:ro`, no un directorio del host (`isolate` sigue sin admitir directorios del host: D45).

## Ciclo de vida: los perfiles se fabrican con la herramienta

```text
llega un legacy con stack desconocido
        ↓
INSPECT: el agente identifica el stack a partir de los artefactos
        ↓
el agente redacta un BORRADOR de perfil (status: draft) con fixtures sintéticos
  · señales de detección de ESTOS artefactos (sin nombres del sistema)
  · extractores del mapa (mecanismos existentes + patrones)
  · receta de rehydrate, colectores, parsers
        ↓
una persona lo revisa y lo prueba con ese legacy
        ↓
si funciona → status: validated → entra a la librería (un PR al clon de PEPPER)
        ↓
el siguiente legacy con ese stack cae en el escalón 1
```

Con `pepper init`, el borrador que el agente escribe en el workspace cae en `profiles/` de la instalación (enlace): es una contribución desde el primer momento, limpiada como dice [`CONTRIBUTING.md`](../../CONTRIBUTING.md).

## Reglas

1. **Un perfil no lleva Python ni conocimiento de un sistema.** Si un stack necesita un lector que no existe, el lector entra al núcleo como formato genérico (con pruebas, schema y fila arriba) y el perfil lo parametriza. Ningún nombre de cliente, host, paquete raíz ni dominio de negocio entra al núcleo.
2. **Un perfil `draft` corre igual, y se declara.** `/pepper` lo usa sin manos; `environment.json` y `funcional.md` dicen que el perfil es un borrador. Lo promueve a `validated` una persona cuando lo vio correr de punta a punta contra un legacy real y confirmó el documento.
3. **Fidelidad primero**: la receta reproduce las versiones originales del stack, no las moderniza; lo que no sabe (versión, charset, `php.ini`) lo pide en `NOTAS.md` o lo declara como desviación, nunca lo adivina.
4. **Todo perfil trae fixtures.** `tests/test_perfiles.py` los ejecuta por carpeta en CI; un perfil sin fixtures no entra.
5. **Sin perfil no hay bloqueo total**: aplican los colectores genéricos (escalón 2) o la inspección con reporte de faltantes (escalón 3).

## Perfiles

| id | estado | nota |
|---|---|---|
| `groovy-grails1-tomcat-mysql` | validated | Grails 1.3 en Tomcat con MySQL; corrió el ciclo completo contra un legacy real (2026-09-22) y la persona responsable confirmó el documento |
| `java-springboot-jsf-postgres` | draft | el que corrió el primer legacy real de punta a punta; trae extractores completos |
| `java-springboot-fatjar-postgres` | draft | fat jars de Spring Boot (varias piezas) con PostgreSQL; el E2E de CI lo levanta con Docker en cada cambio |
| `java-wildfly-postgres` | draft | el primero; parsers de WildFly y PostgreSQL, extractores heredados |
| `php-apache-mysql` | draft | PHP 7/8 con Apache y MySQL, datasource en `.env`; la primera familia que no es JVM y la primera sin una línea de Python; redactado sin legacy real, con fixtures sintéticos |
