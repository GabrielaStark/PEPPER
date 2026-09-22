# Perfil: java-springboot-fatjar-postgres

Sistema de **varios fat jars de Spring Boot** (backend, puerta de enlace, descubrimiento de servicios) con un **front estático** opcional y **PostgreSQL**. Es el primer perfil que levanta un sistema de **varias piezas**: un servicio por desplegable, cada uno con su IP, su puerto y su alias dentro de la red interna.

**Estado: `draft`.** El reparto de papeles, el compose por pieza, el arranque por pieza y `environment.json` se probaron con artefactos sintéticos y con Docker de verdad (2026-09-22): las cuatro piezas se levantan, el front estático arranca y cada pieza recibe su propia validación. **No se ha corrido contra un sistema real de varias piezas**; eso es lo que falta para `validated`.

## Contenido

| Archivo | Qué hace |
|---|---|
| `profile.json` | detección (5 señales); `rehydrate.components` con 4 reglas de clasificación, plantilla por motor, `datasource_role` y `ingress_role`; `rehydrate.database` = PostgreSQL con respaldo `pg_dump_custom`; receta en 9 pasos |
| `compose.template.yml` | red interna, PostgreSQL con `log_statement=all`, stub con alias, **el bloque de piezas que Rehydrate fabrica**, ingress apuntando a la pieza de entrada |
| `service.template.yml` | una pieza **java**: el fat jar se ejecuta tal cual con `java -jar`, con su IP, su alias y su perfil de configuración |
| `service-static.template.yml` | una pieza **estática**: el dist original servido por httpd, sin compilar ni tocar |
| `restore.template.sh` | restaura el `pg_dump -Fc` dentro de la base que el artefacto espera y re-apunta al stub todo servidor foráneo |
| `parsers/springboot-app.json` | stdout de Spring Boot (una captura por pieza) |
| `parsers/postgresql-log.json` | log de PostgreSQL con sentencias |

## Por qué la receta es como es

- **Todas las piezas o ninguna.** Levantar un solo servicio de un sistema de cuatro produce un ambiente a medias, y todo lo que se observe encima es basura. Si una pieza no encaja en ninguna regla de `classify`, el perfil se detiene diciendo cuál: una pieza sin papel no se levanta ni se declara.
- **El datasource sale de una sola pieza** (`datasource_role: backend`). Con dos backends, `BLOCKED`: no se sabe cuál base es la del sistema, y hoy PEPPER fabrica una sola base por entorno.
- **El ingress entra por la puerta de enlace** (`ingress_role: gateway`), que es por donde entra la gente. Sin esa declaración y con varias candidatas, `BLOCKED`: por dónde se entra no se adivina.
- **Cada motor tiene su plantilla.** Un fat jar y un dist estático no se levantan igual; `service_template` es un objeto por motor en vez de una plantilla con condicionales.
- **Cada pieza arranca con su propio patrón.** Un front estático nunca dice "Started": `ready_log_pattern` va en la regla de clasificación. Una pieza que no arranca deja el entorno en `FAILED`, con una validación por pieza en `environment.json`.
- **Las piezas se hablan por su alias de red**, no por un host de producción. Lo que el artefacto pida por nombre externo cae en el stub y queda registrado.

## Pendiente para `validated`

- [ ] Correr el ciclo completo contra un sistema real de varias piezas: mapa, levantar, explorar, descubrir.
- [ ] Confirmar cómo se descubren entre sí en el original (Eureka, variables de entorno, un gateway con rutas fijas) y si el alias de red basta o hay que declarar desviaciones.
- [ ] `pepper map` toma un artefacto a la vez: con varias piezas hoy se mapea la que habla con la base. Mapear todas y unir los mapas es trabajo del núcleo, no del perfil.
- [ ] Probar ambos parsers contra logs reales de cada pieza.
