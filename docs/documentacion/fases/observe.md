# Fase 1 — Observe

## Objetivo

Capturar evidencia técnica de **una ejecución funcional real** del legacy, delimitada por una ventana de tiempo.

## Ventana de flujo (manual en el MVP)

La unidad de observación es una acción funcional ejecutada por un humano:

```text
FLUJO:  Registrar solicitud
INICIO: 13:20:00   ← el usuario marca inicio
        (usa la aplicación normalmente)
FIN:    13:22:14   ← el usuario marca fin
```

Todo lo capturado dentro de la ventana se etiqueta con un `session_id` de flujo. Automatizar la ejecución del flujo queda explícitamente fuera del MVP.

## Colectores

### Genéricos (núcleo — funcionan con cualquier stack)

| Colector | Captura | Nota |
|---|---|---|
| Proxy HTTP inverso | request/response, status, duración | **inyecta header de correlación** en cada petición; es la fuente de `correlation_id` cuando el legacy no emite ninguno |
| Contenedores | stdout/stderr, eventos docker/podman | igual para cualquier lenguaje |
| Base de datos | sentencias SQL, transacciones, errores | vía configuración de log del motor (p. ej. `log_statement=all` en PostgreSQL) |

### De perfil (específicos del stack)

```text
logs de aplicación (WildFly, Spring, IIS, ...)
cadenas de excepciones del runtime
audit logs
traces / métricas / eventos JVM cuando existan
```

El perfil declara cada colector: dónde está la fuente, cómo activarla y qué parser la normaliza.

## La ventaja del entorno rehidratado

Como el entorno lo levantó PEPPER, la observabilidad se configura **antes del arranque**: niveles de log verbosos, log de todas las sentencias SQL, proxy delante de la app. Nada de esto sería aceptable en producción; en un entorno rehidratado y desechable es gratis. Esta es la razón técnica por la que Rehydrate y Observe se potencian mutuamente.

En el escalón 2 (sistema levantado a mano, sin perfil), solo operan los colectores genéricos: menos profundidad, mismo pipeline.

## Salida

Evidencia cruda por fuente, sin interpretar, etiquetada con la ventana:

```text
pepper-out/observe/<session_id>/
├── session.json          (flujo, inicio, fin, colectores activos)
├── http.jsonl
├── containers/*.log
├── database.log
└── application/*.log     (si hay perfil)
```

Observe **no interpreta ni filtra**: la reducción es responsabilidad de Correlate.
