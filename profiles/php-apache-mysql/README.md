# Perfil `php-apache-mysql` (borrador)

PHP 7/8 servido por Apache con `mod_php` y una base MySQL 5.7/8, con la conexión declarada en un `.env` (`DB_CONNECTION`, `DB_HOST`, `DB_PORT`, `DB_DATABASE`, `DB_USERNAME`, `DB_PASSWORD`): Laravel y Lumen, CodeIgniter 4, Symfony con dotenv, y PHP clásico que cargue un `.env`. Es la primera familia de PEPPER que **no es JVM**, y la primera que entra **solo con datos**: ningún archivo de este perfil es Python.

**Estado: `draft`, redactado sin un legacy real** (auditoría 2026-09-29). Lo que sí corrió: el E2E de CI (`scripts/e2e_docker.py --profile php-apache-mysql`) levanta con Docker una ventanilla sintética en PHP clásico con su respaldo (`examples/e2e-php/`), la restaura, la sirve por el ingress y verifica el aislamiento en vivo, en cada cambio. Lo que aquí se afirma sale de la documentación de la imagen oficial `php:<versión>-apache`, de Apache 2.4, de Laravel y de lo que el perfil `groovy-grails1-tomcat-mysql` aprendió de MySQL en su corrida real. Sus `fixtures/` son sintéticos y la suite los ejecuta en CI. Pasa a `validated` cuando una persona vea el ciclo completo contra un sistema real y confirme que el documento describe su sistema.

## Qué necesita en `legacy/`

| Insumo | Obligatorio | Por qué |
|---|---|---|
| La **carpeta** del fuente tal como estaba desplegada, con su `.env` de producción (`legacy/<sistema>/`) | sí | es el desplegable: PHP no compila. Un zip se descomprime antes (`rehydrate.artifact_kind = directory`). |
| Respaldo `mysqldump`/`mariadb-dump` de la base de la aplicación (`.sql`) | sí | su cabecera dice la versión del motor; sin ella, NOTAS.md. |
| `NOTAS.md` con la versión de PHP de producción (`PHP 7.4`) | sí | el fuente no la dice (`composer.json` declara un rango) y por fidelidad no se adivina: sin la nota, BLOCKED. |
| `NOTAS.md` con Apache, charset/collation, `sql_mode`, plugin de autenticación | no | lo que falte queda como desviación declarada. |
| `crontab` / `cron.d/*` del servidor | no | entran al mapa como tareas programadas. |

## Cómo lo detecta (`detection`)

`*.php` (obligatorio), un `.env` con `DB_CONNECTION=mysql` (obligatorio), y suman `composer.json`, `artisan`, `.htaccess`, un `<?php` y un respaldo con cabecera `MySQL dump`/`MariaDB dump`. Un WAR o un fat jar no llegan al mínimo porque no traen `.env` con `DB_CONNECTION`.

## Cómo lo levanta (`rehydrate`)

- **Datasource** con el lector genérico `key_value` sobre `.env`: motor, host, puerto, base, usuario y contraseña, cada uno por su clave. Si falta una clave, BLOCKED nombrando el archivo y la clave; si hay dos `.env` con valores distintos, BLOCKED (elegir sería adivinar el ambiente).
- **`DB_HOST=localhost|127.0.0.1`** (lo normal en un servidor de una máquina): el app corre en la pila de red de `db` (`network_mode: service:db`) y `localhost:3306` dentro del app es la base. Si es un nombre, `db` lleva ese nombre como alias. No se inventa un host.
- **MySQL** de la versión que declara el respaldo (`mysql:5.7` / `mysql:8.0`), con el general log a un archivo del volumen y el sidecar `dblog` sacándolo por stdout (5.7 rechaza `/dev/stdout` como `general_log_file`; probado en la corrida real del perfil Grails). `restore.sh` restaura dentro de `DB_DATABASE`, quita `CREATE DATABASE`/`USE`, reescribe `DEFINER` y el esquema calificado de las vistas, se niega si el respaldo es el esquema de sistema `mysql`, y **crea al usuario `DB_USERNAME` con la clave del `.env`** (root del contenedor lleva la misma clave; el `.env` de `pepper-out/` no se versiona).
- **PHP** `php:<versión de NOTAS.md>-apache`: `mod_php`, como en el servidor original. El fuente se monta **solo lectura** en `/legacy` y el contenedor lo **copia** a `/var/www/html` al arrancar, porque PHP y Apache escriben (`storage/`, `bootstrap/cache/`, sesiones, subidas): lo escrito muere con el contenedor y el legacy no se toca. Si el proyecto trae `public/index.php` (Laravel, Symfony, CodeIgniter 4) el `DocumentRoot` pasa a `public/`; si no, la raíz (PHP clásico). `mod_rewrite` se habilita porque un `.htaccess` lo espera: sin él las rutas dan 404 y el sistema observado no sería el original.
- **Extensiones de MySQL para PHP**: la imagen oficial `php:*-apache` no trae `pdo_mysql` ni `mysqli`; el contenedor las compila al arrancar con las fuentes que la imagen incluye (sin red, uno o dos minutos: `startup_timeout_s` es 420). Es una desviación declarada: el servidor original las tenía por paquete. Otras extensiones que producción tuviera (gd, intl, zip, soap…) las dirá la primera corrida real y entrarán como datos del perfil.
- **`DB_HOST=localhost` en PHP no es TCP**: mysqlnd usa el socket Unix de MySQL, y una pila de red compartida no comparte sockets. Por eso `/var/run/mysqld` es un volumen común a `db` y `app`: `localhost` funciona como en el servidor original, y `127.0.0.1` va por TCP dentro de la misma pila.
- **Arrancó** cuando Apache dice `resuming normal operations`. **Falló** con `PHP Fatal error`, `PHP Parse error`, un socket que no abre, o `SQLSTATE[HY000] [1045|2002|2054]` (credenciales, sin conexión, o plugin de autenticación de MySQL 8 que un PHP viejo no habla: eso se reporta, no se parchea aquí).
- **Hosts externos** del `.env` y del fuente (SMTP, APIs de pago, servicios de gobierno) resuelven al `stub` en 80, 443, 25, 465 y 587.

Lo que producción tenía y este perfil no reproduce por no saberlo se declara como desviación en `validation.md`: `php.ini` (límites de subida, zona horaria), `charset`/`collation`/`sql_mode`, `default_authentication_plugin`, y las extensiones de PHP más allá de `pdo_mysql`/`mysqli`.

## Qué observa (`collectors`)

| Fuente | Dónde | Parser |
|---|---|---|
| `apache-php` | stdout+stderr del contenedor `app` (`containers/app.log`): access log `combined` y error log de Apache con los avisos y errores fatales de PHP | `parsers/apache-php-stdout.json` |
| `mysql` | stdout del sidecar `dblog` (`containers/dblog.log`): general log con cada sentencia y sus valores | `parsers/mysql-general-log.json` |

Las peticiones HTTP que cuentan las registra el ingress (`http.jsonl`); el access log las corrobora del lado del servidor. Con PDO la emulación de prepares (default) manda cada sentencia completa; sin ella, `Prepare` trae la plantilla y `Execute` la sentencia con valores, y las dos cuentan como SQL. `http` del perfil dice qué campos de un formulario son la acción (`_method`, `action`, `submit`…) y cuáles son ruido (`_token`, `PHPSESSID`, `laravel_session`).

## Qué mapea (`extractors.json`)

Todo con lectores genéricos parametrizados; ninguno es de PHP:

| Superficie | Mecanismo | Qué lee |
|---|---|---|
| entrypoints | `regex_extractor` | `Route::get|post|…('/ruta', manejador)` de `routes/web.php` (http_route) y `routes/api.php` (rest_endpoint); formularios `<form action="x.php" method="post">` del PHP clásico |
| jobs | `regex_extractor` | `$schedule->command('x')->daily()` del scheduler de Laravel; crontabs (`crontab`, `cron.d/*`, `*.cron`) |
| external_dependencies | `archive_url_scan` | URLs en `.php`, `.env`, `.ini`, `.json`, `.yml`, `.xml`, `.js`, `.htaccess`, `.conf` |
| screens | `view_templates` | plantillas Blade y vistas PHP: título, encabezados, etiquetas, campos, formularios, botones, mensajes `__()`/`@lang`, condiciones `@can`/`@role`/`@auth`, inclusiones |
| data_stores, catalogs | `sql_dump` | tablas, columnas, conteos, catálogos redactados, distribuciones de estado y fecha, vistas, rutinas, triggers |

Lo que no se enumera de antemano, y se dice como hueco: las páginas `.php` sueltas del PHP clásico sin enrutador (las descubre el explorador al navegar), los textos de `__('archivo.clave')` (se listan por clave: los `lang/*.php` son arreglos PHP, no un bundle `clave=valor`), y las rutas registradas dinámicamente (`Route::resource` se lista como una entrada, no como sus siete rutas).

## Fixtures

`fixtures/` trae lo que la suite (`tests/test_perfiles.py`) necesita para probar el perfil sin un legacy real: líneas de log sintéticas fieles a cada parser (`logs/apache-php.log`, `logs/mysql.log`), el `.env` que el datasource debe leer (`config/artifact/.env`), un fuente mínimo Laravel + PHP clásico con `.env`, `routes/web.php` y `routes/api.php`, el scheduler, un crontab, una vista Blade y un formulario clásico (`source/`), el generador del respaldo mysqldump (`synthesize.py`) y `expected.json` con lo que cada lector debe encontrar: 11 eventos de Apache/PHP, 13 del general log con `users` y `tramites` reconocidas, el datasource, 6 tablas con dos catálogos, y 14 entrypoints, 6 jobs, 3 hosts y una pantalla del mapa. Son inventados; no describen ningún sistema. El E2E de Docker usa otro fixture, más chico y ejecutable (`examples/e2e-php/`).

## Qué le falta para `validated`

1. Una corrida real de punta a punta (mapa → `rehydrate --up` con aislamiento vivo → explorador → correlate → package → discovery → export) contra un sistema PHP con su respaldo.
2. Ajustar los parsers contra los logs reales de esa corrida (hoy están escritos sobre el formato documentado).
3. Que la persona responsable lea el documento funcional y confirme que describe su sistema.
