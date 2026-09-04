# Arquitectura

## Invariante central

> **El núcleo de PEPPER nunca conoce la tecnología del legacy.**

Todo conocimiento específico de un stack (cómo detectarlo, cómo levantarlo, cómo leer sus logs, cómo sacar sus pantallas y catálogos) entra como datos: un **perfil**. La prueba de fuego para cualquier cambio: *¿esta línea del núcleo dejaría de funcionar con otro stack?* Si sí, pertenece a un perfil.

## Vista general

```text
                      ┌──────────────────────────────────────────┐
                      │                PERFILES                  │
                      │ detección · extractores del mapa ·       │
                      │ receta de rehydrate · colectores ·       │
                      │ parsers · lectura de formularios         │
                      └──────────────────┬───────────────────────┘
                                         │ (datos, no código del núcleo)
                                         ▼
┌─────────┐   ┌───────────┐   ┌─────────┐   ┌───────────┐   ┌──────────┐   ┌──────────┐   ┌────────┐
│ INSPECT │ → │ REHYDRATE │ → │ OBSERVE │ → │ CORRELATE │ → │ PACKAGE  │ → │ DISCOVER │ → │ EXPORT │
└─────────┘   └───────────┘   └─────────┘   └───────────┘   └──────────┘   └──────────┘   └────────┘
 stack +       entorno en      evidencia     eventos y       carpeta        el agente       contrato
 MAPA del      contenedores    cruda de      peticiones      autocontenida  escribe QUÉ     validado,
 sistema       aislados        una ventana   con acción      mapa+evidencia HACE el sistema acumulado
```

Dos fuentes, no una. **El mapa** (`pepper map`, Inspect) dice lo que el sistema *es*: rutas, jobs, pantallas con campos y botones, clases con constantes y mensajes, tablas con conteo, triggers y funciones con cuerpo, catálogos completos (roles, menús, estados, tipos, parámetros) y distribuciones reales. **La evidencia** (Observe → Correlate) dice lo que el sistema *hace* cuando alguien lo opera: cada petición con su acción y campos, cada escritura, cada rechazo, cada job que corrió solo. Discover cruza las dos y escribe el documento funcional; Export lo valida y lo acumula.

## Módulos del núcleo

| Módulo | Entrada | Salida | Dependencia de tecnología |
|---|---|---|---|
| `inspect` | artefacto + respaldo + perfil | `system-map.json` + `map/*.md` (`pepper map`); `pepper detect` | vía `extractors.json` del perfil (patrones) y lectores genéricos (zip, javap, pg_dump custom) |
| `rehydrate` | artefactos + perfil | entorno corriendo + `environment.json`; `pepper isolate` verifica que no alcance nada externo | vía receta del perfil |
| `observe` | entorno corriendo + ventana | evidencia cruda por fuente (`pepper proxy`, `pepper collect`) | colectores genéricos + del perfil |
| `correlate` | evidencia cruda | `events.jsonl` + `flow.json/md` (petición → acción → SQL/log) | vía parsers del perfil; correlación genérica |
| `package` | correlated + mapa + legacy + discovery anterior | paquete controlado + manifest externo + gate de datos | ninguna |
| `discover` | paquete controlado | `funcional.json/md` (lo escribe el agente) | ninguna |
| `export` | salida del agente | validada contra el contrato; publicada por sesión y como documento del sistema | ninguna |

## `pepper map`: lo que el sistema es

Mecanismos del núcleo, patrones del perfil:

| Mecanismo | Qué saca | Cómo |
|---|---|---|
| `jvm_route_annotations` | rutas HTTP y jobs con su cron | `javap -v` sobre las clases que el perfil señala |
| `jvm_class_inventory` | por clase: métodos públicos, constantes, cadenas (mensajes, estados, JPQL) | `javap -c -constants` por lotes; solo clases propias (comparten paquete raíz con el WAR) |
| `view_templates` | pantallas: encabezados, campos, botones→acción, mensajes de validación, condiciones por rol, inclusiones | regex declaradas en el perfil sobre las vistas; bundle i18n resuelto |
| `pg_dump_custom` | tablas con conteo y columnas, triggers y funciones con cuerpo, vistas, catálogos completos, distribuciones de columnas de estado | lector propio del formato custom de `pg_dump` (`pepper/inspect/pgdump.py`), sin PostgreSQL |
| `config_hosts`, `archive_url_scan` | hosts externos | configuración y URLs incrustadas |

Fail-honest: si falta `javap`, el respaldo no es custom, o ningún extractor cubre una superficie, el mapa sale `complete: false` con `coverage_gaps`. Sin datos personales ni secretos: las tablas de personas se cuentan pero no se vuelcan; columnas y renglones con pinta de credencial o de dato personal se redactan; las cadenas del bytecode que parezcan credenciales se omiten.

## Escalera de soporte

```text
¿Hay perfil que detecte el stack?
 ├─ SÍ  → escalón 1: pipeline completo (mapa + rehydrate + observe)
 └─ NO  → ¿el sistema corre o puede levantarse a mano?
           ├─ SÍ  → escalón 2: Observe con colectores genéricos (sin mapa, o con uno parcial)
           └─ NO  → escalón 3: Inspect produce BLOCKED (stack + faltantes + borrador de perfil)
```

Los tres escalones producen un entregable. `BLOCKED` es un resultado, no un fracaso.

## Colectores genéricos (escalón 2)

- **Proxy HTTP** delante de la aplicación (`pepper proxy`): captura petición/respuesta, cuerpos de formulario (credenciales redactadas), inyecta el header de correlación, e impone al navegador una política que lo mantiene dentro del perímetro (D25).
- **stdout/stderr de contenedores** y **logs del motor de base** con cada sentencia.

Como PEPPER controla el entorno reconstruido, controla la observabilidad: niveles de log que jamás se activarían en producción.

## Motor de análisis intercambiable

Discover no invoca APIs de ningún agente: el paquete controlado es una carpeta autocontenida con el mapa, la evidencia, el legacy, el discovery anterior y el prompt (`CLAUDE.md` y `AGENTS.md` apuntan a él). La salida se valida contra `schemas/functional-discovery.schema.json` venga de donde venga. El agente opera en **solo lectura**; el manifest raíz queda fuera de su directorio; Package bloquea symlinks y aplica el gate local/remote de datos.

## El discovery es acumulativo

El documento es del **sistema**, no de una ventana. Cada sesión recibe `previous/funcional.json`, lo extiende y lo corrige; Export publica la salida de la sesión en `docs/pepper/discovery/<sid>/` y el vigente en `docs/pepper/funcional.md|json`. Los desconocidos de la sección 12 dicen qué ventana observar después.

## Los contratos son la interfaz

- `profile.schema.json` — perfiles ↔ núcleo (incluye `http`: cómo leer formularios)
- `parser.schema.json` — parsers declarativos ↔ normalización
- `system-map.schema.json` — `pepper map` ↔ paquete ↔ agente
- `environment.schema.json` — rehydrate ↔ resto
- `session.schema.json` — observe ↔ correlate
- `event.schema.json`, `flow.schema.json` — correlate ↔ paquete
- `functional-discovery.schema.json` — agente ↔ export ↔ stark

Cualquier pieza es reemplazable mientras respete su schema.
