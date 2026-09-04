# Sistema de Solicitudes — qué hace el sistema

> Fuentes: código fuente (`legacy/source/`), manual técnico de 2018, configuración, y una ventana observada (flow-001: dos intentos de registro).
> Orígenes: [código] [base] [datos] [observado] [config] [doc] [humano].

## 1. En una frase

Ventanilla registra solicitudes de trámite de ciudadanos: consulta el padrón, asigna un folio y guarda la solicitud con su historial. [doc] [observado flow-001]

## 2. Quién lo usa

| Rol | Qué es |
|---|---|
| Ventanilla | Envía la solicitud con el identificador del ciudadano y el tipo de trámite. [doc] |

Solo hay una capacidad: **Registrar solicitud** (`POST /api/applications`). [código] No hay roles ni permisos en el código: cualquiera con acceso al servicio registra.

## 3. El recorrido principal

```
Ventanilla envía solicitud ──► consulta padrón ──► ¿ACTIVO? ──no──► rechazo "El ciudadano no se encuentra activo" (nada se escribe)
                                                     │sí
                                                     ▼
                                     folio SOL-<año>-<consecutivo> ──► guarda solicitud + historial REGISTERED ──► devuelve folio
```

1. Ventanilla envía la solicitud. [observado]
2. El sistema consulta al ciudadano en el padrón (estado y nacionalidad). [observado] [código: ApplicationService]
3. Si no está ACTIVO, rechaza sin escribir nada. Se vio con un ciudadano SUSPENDIDO. [observado] [código: ApplicationService:35]
4. Toma el siguiente consecutivo y arma el folio `SOL-2026-000042`. [observado] [código: ApplicationDao:28]
5. Guarda la solicitud y un primer historial en REGISTERED, en la misma operación. [observado]
6. Devuelve el folio. [observado]

## 4. Las otras puertas de entrada

Ninguna conocida. El manual menciona consulta y cancelación como "pendientes de documentar"; no hay código para ellas. [doc]

## 5. Estados

**Ciudadano (padrón)** [doc] [código]: ACTIVE (puede registrar) · SUSPENDED (se rechaza) · INACTIVE (declarado, sin efecto observado). El manual dice que el estado es estadístico; el sistema lo usa para decidir.

**Solicitud**: REGISTERED, único estado observado. [observado]

## 6. Lo que pasa solo

Nada: no hay jobs ni triggers. [código] [base]

## 7. Acceso y sesión

No hay autenticación en el código. [código]

## 8. Sistemas externos

| Sistema | Para qué | Si falla |
|---|---|---|
| PostgreSQL | padrón, folios, solicitudes [observado] | no se registra |
| Correo SMTP | según el manual, confirmación al ciudadano [doc] [config] | desconocido: **no se observó ningún envío** y el código nunca lo invoca [código: NotificationService] |

## 9. Reportes

Ninguno.

## 10. Catálogos que definen el negocio

Estados del ciudadano: ACTIVE, SUSPENDED, INACTIVE. [doc]

## 11. Volumen real

El consecutivo de folios iba en 42 y la solicitud creada recibió el id 87: hay solicitudes que no pasaron por este flujo, o el consecutivo se reinició. [observado]

## 12. Lo que no sé (y a quién preguntarle)

1. **¿Qué pasa con un ciudadano extranjero?** El código lo manda a revisión consular; nadie lo ejercitó. → Observar un registro con el ciudadano 1005 (BR).
2. **¿Qué pasa si el ciudadano no existe?** El código responde 404; no se observó. → Observar un registro con un id inexistente.
3. **¿El correo se envía en otro momento, o nunca?** → Quien opere el sistema: ¿los ciudadanos reciben correo?
