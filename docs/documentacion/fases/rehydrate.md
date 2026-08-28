# Fase 0 — Rehydrate

## Objetivo

Reconstruir un entorno ejecutable local del legacy a partir de los artefactos disponibles, en contenedores, sin modificar el sistema.

> PEPPER intenta reconstruir un entorno ejecutable y determina qué información adicional hace falta cuando la reproducción no es posible. **No promete levantar cualquier sistema.**

## Fidelidad antes que modernización

Rehydrate **reproduce**, no moderniza. Si el legacy necesita Java 8, WildFly 10 y PostgreSQL 9, eso es lo que se levanta. Modernizar es otro problema y otra herramienta.

## Insumos posibles

Todo se trata como evidencia; nada es obligatorio por sí solo:

```text
código fuente          src/, pom.xml, package.json, angular.json, ...
artefactos compilados  .war, .jar, dist/, .ear, binarios
base de datos          .dump, .sql, .backup, scripts de creación/migración
configuración          properties, yml, standalone.xml, .env, nginx.conf, ...
otros                  Dockerfile, docker-compose, certificados, notas, diagramas
```

## Flujo

```text
artefactos → inspección → identificación del stack → detección de dependencias
→ plan de reconstrucción → contenedores → restauración de datos → configuración
→ arranque → validación → legacy ejecutable
```

## De dónde sale el plan de reconstrucción

- **Con perfil** (escalón 1): la receta del perfil genera el plan (compose, imágenes, datasources, orden de arranque). Determinístico y repetible.
- **Sin perfil** (rehydrate asistido): el agente inspecciona los artefactos y **redacta un plan borrador**. Un humano lo revisa y ejecuta. Si funciona, el plan validado se guarda como perfil nuevo (ver [profiles.md](../PERFILES.md)) — así crece la librería.

## Cuando faltan insumos

PEPPER **no inventa** lo que falta (datasources, certificados, servicios externos, variables). Produce un reporte `BLOCKED` que enumera: qué se detectó, qué falta y qué evidencia conseguir a continuación. Ese reporte es un entregable de primera clase (`missing-evidence.md`).

## Validación

Un contenedor `running` no significa que la aplicación funcione. Según el caso, Rehydrate valida:

```text
contenedores iniciados        aplicación desplegada
base restaurada               datasource operativo
endpoint accesible            frontend cargando
backend respondiendo          sin errores críticos de arranque
```

Las validaciones específicas del stack las aporta el perfil; las genéricas (contenedor arriba, puerto respondiendo) son del núcleo.

## Salida

```text
pepper-out/rehydrate/
├── environment.json      ← contrato: schemas/environment.schema.json
├── docker-compose.yml    (o equivalente generado)
├── configuration/
├── missing-evidence.md   (si aplica)
└── validation.md
```

`environment.json.status` ∈ `READY | PARTIAL | BLOCKED | FAILED`. Solo `READY` (o `PARTIAL` con acuerdo explícito del usuario) habilita pasar a Observe.
