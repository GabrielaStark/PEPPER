# Sistema de Solicitudes — Manual técnico

**Versión del documento:** 1.1
**Última actualización:** marzo 2018
**Elaboró:** Coordinación de Sistemas

> Documento heredado. El personal que lo elaboró ya no labora en la dependencia.

## 1. Descripción general

El Sistema de Solicitudes permite al personal de ventanilla registrar las solicitudes
de trámite que presentan los ciudadanos. El sistema asigna un folio consecutivo por
año y conserva el historial de estados de cada solicitud.

## 2. Arquitectura

- Aplicación Java EE desplegada como WAR en el servidor de aplicaciones.
- Base de datos PostgreSQL en el servidor SRV-BD-01.
- Acceso mediante el datasource `SolicitudesDS`.

## 3. Flujo: Registro de solicitud

El registro de una solicitud realiza los siguientes pasos:

1. La ventanilla envía la solicitud con el identificador del ciudadano y el tipo de trámite.
2. El sistema consulta al ciudadano en el padrón.
3. Se genera el folio consecutivo del año en curso.
4. Se guarda la solicitud y su primer registro de historial.
5. **Se envía un correo de confirmación al ciudadano** con el folio asignado, a través
   del servidor SMTP institucional (`mail.dependencia.gob.mx`).
6. Se devuelve el folio a la ventanilla.

## 4. Catálogo de estados del ciudadano

El padrón maneja los siguientes estados: `ACTIVE`, `SUSPENDED`, `INACTIVE`.

El campo se utiliza con fines estadísticos y de depuración del padrón.

## 5. Notificaciones

Todas las notificaciones del sistema se envían por correo electrónico. La configuración
del servidor SMTP se encuentra en `application.properties`
(`notificaciones.habilitado=true`).

## 6. Pendientes documentados

- Falta documentar el módulo de consulta de solicitudes.
- Falta documentar el proceso de cancelación.
