# Notas del legacy — [nombre del sistema]

> Lo que TÚ sabes del sistema y ningún artefacto dice. Prosa libre, sin formalidad; incompleto está bien.
> Los agentes de PEPPER lo leen PRIMERO y lo citan como evidencia (`NOTAS.md:12`). Si algo aquí
> contradice a los artefactos, lo reportan como discrepancia — no lo resuelven en silencio.
> Una línea tuya ("producción es WildFly 21") vale horas de inferencia.
>
> **Sin credenciales aquí.** Este archivo vive en `legacy/`, que no se versiona, pero igual: nada de contraseñas.

## Qué es

<!-- Para qué sirve el sistema, quién lo usa, desde cuándo. Dos líneas. -->

## Qué es cada artefacto

<!-- LO MÁS IMPORTANTE si hay más de uno. Una línea por archivo o carpeta de legacy/:

     api-core-2.3.1.jar    → la aplicación principal, escucha en 8080
     batch-jobs-1.7.0.jar  → procesos nocturnos, no atiende peticiones
     notificaciones.jar    → manda correos, escucha en 8082
     dist/                 → el front; lo sirve nginx en el 80 y pega al 8080

     Si no sabes cuál es cuál, dilo así: "no sé cuál de los tres es el principal".
     Eso es información: PEPPER lo trata como desconocido en vez de adivinar. -->

## Cómo corre en producción

<!-- Cómo lo arrancan: ¿java -jar? ¿un servicio de systemd? ¿un script? ¿ya en contenedores?
     Runtime y versión (Java 11, Node 18, .NET 6…). Servidor de aplicaciones si lo hay
     (WildFly 21, Tomcat 9, IIS…). Puerto y URL de cada pieza. Quién sirve el front
     (nginx, Apache, el mismo backend). Sistema operativo del servidor. -->

## Cómo se hablan entre sí

<!-- Solo si son varios: ¿el front pega directo a cada servicio o hay un gateway?
     ¿Los servicios se llaman entre ellos, por HTTP, por cola, por la misma base?
     ¿Comparten base de datos o cada uno tiene la suya? -->

## Base de datos

<!-- Motor y versión en producción (PostgreSQL 16, MySQL 8, SQL Server 2019…). Nombre de la base.
     ¿Con qué se hizo el respaldo (pg_dump -Fc, mysqldump, .bak)? ¿Salió de producción o de otro
     ambiente? Si hay más de una base, cuál usa cada artefacto. -->

## Servicios externos que usa

<!-- Otros sistemas, buses, APIs, correo, servidores de archivos. Cuáles importan y cuáles se pueden dejar caer. -->

## Cómo se entra

<!-- ¿Hay usuario de pruebas? ¿Cómo se crea uno? ¿Roles? -->

## Flujos que importan

<!-- Qué se quiere entender primero. "Registrar solicitud", "Generar reporte mensual"… -->

## Quién sabe del sistema

<!-- Nombres y qué saben (operación, negocio, infraestructura). Son los destinatarios de las preguntas abiertas. -->

## Lo que sospechas

<!-- Cosas raras conocidas, módulos muertos, "eso nunca funcionó", "eso lo cambió alguien y nadie sabe". -->
