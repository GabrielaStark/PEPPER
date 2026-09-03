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

## El artefacto dicta el ambiente

El caso típico es un WAR/JAR/dist y un respaldo, sin código ni configuración externa. Rehydrate busca la configuración **dentro** del artefacto y fabrica el ambiente que espera: una red Docker con el subnet de las IPs hardcodeadas, la base en esa IP con el nombre y el rol que el artefacto pide (aunque el respaldo traiga otro nombre), el respaldo restaurado ahí, y el artefacto arrancado con el perfil de configuración que esté completo. Reproducir lo que el artefacto dice no es inventar.

Los hosts externos que el artefacto invoca (servicios, buses, SMTP, servidores foráneos) se resuelven en esa red a un **stub** que responde error y registra cada petición — evidencia de qué dependencias invoca cada flujo. Un entorno rehidratado nunca llama a un servicio externo real.

## El aislamiento lo verifica el núcleo, no el agente

El entorno corre con la configuración real del legacy: sus IPs, sus hosts, sus credenciales de producción. Si tiene salida — y la máquina del ingeniero suele tener VPN a la red institucional — **el legacy alcanza producción**. Por eso el aislamiento no depende de que el agente se acuerde de escribirlo:

```bash
python3 -m pepper isolate <compose> --hosts "<hosts externos>"          # antes de levantar nada
python3 -m pepper isolate <compose> --hosts "<hosts>" --live            # después, contra los contenedores
```

Qué comprueba: que toda red sea `internal` (salvo la red de publicación, exclusiva del ingress verificado por hash), que ningún servicio use `network_mode: host` ni una red no declarada, que ningún `extra_hosts` apunte fuera de las subredes internas, que cada host externo del artefacto esté aliaseado al stub, y —con `--live`— que Docker confirme lo mismo sobre los contenedores en ejecución. Sale con código 1 si hay fuga; el agente tiene prohibido levantar el entorno en rojo.

## Cuando faltan insumos de verdad

`BLOCKED` es solo para cuando no hay artefacto desplegable, el respaldo no se puede restaurar, o el artefacto no dice a qué conectarse en ningún perfil. PEPPER **no inventa** eso. Produce `missing-evidence.md`: qué se detectó, qué falta y qué evidencia conseguir. Es un entregable de primera clase.

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
