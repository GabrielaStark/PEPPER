# PEPPER

> Observar primero, inferir después, comparar al final.
>
> Por [@iamgabstark_](https://iamgabstark.com/) · complemento de [stark](https://github.com/GabrielaStark/stark) · [Principios](docs/documentacion/PRINCIPIOS.md)

**Le das el desplegable de un sistema legacy y un respaldo de su base — hoy, un WAR de Java con un `pg_dump` de PostgreSQL o un WAR de Grails con un `mysqldump` de MySQL, que es lo que tiene perfil — y PEPPER saca todo lo que el sistema ES (pantallas, roles, catálogos, reglas en la base), lo levanta en contenedores sin salida, lo recorre solo con cada rol, y escribe QUÉ HACE: quién lo usa y qué puede hacer cada quien, los recorridos, los estados, las reglas de negocio, lo que corre solo, con qué habla y qué pasa si falla, cuánto se usa, y qué no se sabe — cada afirmación con su origen.** Es el documento con el que alguien empieza una reingeniería sin haber visto el sistema.

**¿Por qué PEPPER?** **P**lataforma de **E**videncia y **P**rocesamiento para **P**atrones de **E**jecución y **R**eingeniería. Pepper organiza la realidad antes de que Stark actúe.

## En tres pasos

Necesitas Python 3.9+, Docker (con Compose v2), un JDK (`javap`, para leer un artefacto Java) y Claude Code.

```bash
git clone https://github.com/GabrielaStark/PEPPER.git mi-legacy && cd mi-legacy
rm -rf .git     # ← desconecta el repo de la herramienta: lo que descubras aquí es del sistema que analizas
pip install -r requirements-dev.txt && python3 -m playwright install chromium
mkdir -p legacy && cp /ruta/al/sistema.war /ruta/al/respaldo.dump legacy/   # y una línea en legacy/NOTAS.md si sabes algo
claude          # abre Claude Code EN esta carpeta; adentro: /pepper
```

**Ese `rm -rf .git` no es opcional.** El clon trae el remoto de PEPPER; sin borrarlo, un `git push` distraído publicaría el sistema que estás analizando en un repositorio público. Sin `.git` no hay a dónde subir nada. Si prefieres conservar git para actualizar la herramienta, al menos `git remote remove origin`: el núcleo te lo recuerda en cada comando mientras haya algo en `legacy/`.

`/pepper` corre todo y no te pregunta nada salvo que se atore. Tarda: el mapa segundos, levantar un minuto, **explorar unos diez minutos** (entra con cada rol y prueba cada pantalla), descubrir varios minutos más. No se colgó: está trabajando.

| Fase | Qué hace | Deja |
|---|---|---|
| **mapa** | abre el WAR y el respaldo: pantallas con botones y mensajes, clases con constantes, tablas con conteo, triggers y funciones con cuerpo, catálogos completos (roles, menús por rol, estados), distribuciones reales | `docs/pepper/system-map.json` + `map/*.md` |
| **levantar** | fabrica la red que el artefacto espera (sus IPs, su base, su usuario), restaura el respaldo, manda todo host externo a un stub, verifica el aislamiento antes y en vivo | `docs/pepper/environment.json` |
| **explorar** | entra con cada rol, abre cada pantalla, intenta guardar en vacío (rechazos), llena y guarda, fotografía; después recorre con planes los flujos encadenados | `evidence/explore-*/` |
| **descubrir** | cruza lo observado con el mapa y escribe el documento | **`docs/pepper/funcional.md`** |

Se detiene en cinco casos, y en todos dice qué: el aislamiento no está en verde; falta un insumo (`BLOCKED`, con la lista de qué conseguir); el stack no tiene perfil (deja un borrador para que una persona lo revise); **una sola vez**, para preguntarte si el paquete con datos del legacy puede ir al modelo remoto (la respuesta queda guardada en `pepper-out/data-boundary.json` y no vuelve a preguntar); o Export rechaza el documento (el agente corrige, tú no). Si hay quien conozca un flujo, `/pepper-observe <flujo>`: la persona opera, PEPPER captura, el documento se extiende.

**Nada del entorno reconstruido sale de la máquina.** Los contenedores viven en una red sin salida; cada host externo del artefacto se resuelve a un stub; el navegador (del explorador o de una persona) solo habla con `127.0.0.1` y el ingress le bloquea todo lo demás, incluidas las redirecciones que solo un navegador leería como otro origen; sin `pepper isolate --live` en verde no se levanta ni se explora nada. Si el artefacto trae credenciales de producción, se recrea *ese* ambiente adentro — jamás se toca el real. **Lo único que puede salir es el paquete del discovery** hacia el modelo remoto, y solo con la decisión explícita de la persona, escrita en `pepper-out/data-boundary.json` ([D24](docs/documentacion/DECISIONES.md)): antes de eso, Package se detiene si el paquete trae credenciales, datos de personas o archivos que no pudo inspeccionar, y dice dónde.

Camino completo: [`docs/documentacion/QUICKSTART.md`](docs/documentacion/QUICKSTART.md).

## El entregable

`docs/pepper/funcional.md`, doce secciones fijas: en una frase · quién lo usa (roles, personas, matriz opción × rol) · el recorrido principal · las otras puertas de entrada · estados con conteos reales · lo que pasa solo · acceso y sesión · sistemas externos y qué pasa si fallan · reportes · catálogos que definen el negocio · volumen real · **lo que no sé y a quién preguntarle**. Cada afirmación trae `[código]`, `[base]`, `[datos]`, `[observado]`, `[config]` o `[doc]`, y `pepper export` verifica que cada fuente exista. Es del sistema, no de una corrida: se acumula sesión a sesión. Contrato: [`schemas/functional-discovery.schema.json`](schemas/functional-discovery.schema.json).

## Las piezas

- **El núcleo** (`pepper/`, Python 3.9+): `detect`, `map` (con lector propio del formato custom de `pg_dump`, sin PostgreSQL), `rehydrate`, `isolate`, `proxy` (el ingress), `explore` (Playwright, local), `collect`, `correlate`, `package`, `export`. Hace lo mecánico igual cada vez y **nunca conoce una tecnología**: lo específico de un stack entra como perfil.
- **Los perfiles** (`profiles/<id>/`): detección, extractores del mapa, receta de rehydrate (plantillas de compose y restauración, imágenes por versión), colectores, parsers de logs, lectura de formularios. Datos, no código. Un stack nuevo es un perfil nuevo; el agente lo redacta como borrador y una persona lo valida.
- **Los agentes** (`.claude/`): `/pepper` y `/pepper-observe`; el subagente `descubridor-funcional` escribe el documento desde el paquete controlado; `inspector-legacy` redacta perfiles cuando ninguno aplica. Sus constituciones son las skills.
- **Los contratos** (`schemas/`): la interfaz entre todo; cualquier pieza es reemplazable mientras respete su schema.

Arquitectura: [`ARQUITECTURA.md`](docs/documentacion/ARQUITECTURA.md) · perfiles: [`PERFILES.md`](docs/documentacion/PERFILES.md) · por qué: [`DECISIONES.md`](docs/documentacion/DECISIONES.md) · comandos del núcleo: [`REFERENCIA.md`](docs/documentacion/REFERENCIA.md) · problemas: [`TROUBLESHOOTING.md`](docs/documentacion/TROUBLESHOOTING.md).

## La regla de oro

| | Qué es | ¿Va al git del proyecto? |
|---|---|---|
| **Herramienta** | `.claude/`, `pepper/`, `schemas/`, `profiles/`, `templates/`, `docs/documentacion/`, `examples/`, `tests/`, `scripts/` | ❌ se ignora; se actualiza recopiando |
| **Producto** | `docs/pepper/` (mapa, entorno, `funcional.md`, `discovery/`) y `docs/analysis/funcional.md` (la entrega a stark) | ✅ al git **de ese sistema**, nunca al de la herramienta (aquí `.gitignore` lo bloquea) |
| **Datos ajenos** | `legacy/`, `evidence/`, `pepper-out/` | ❌ nunca |

Al terminar: `docker compose -f pepper-out/rehydrate/docker-compose.yml down -v`, copia `docs/pepper/` y `docs/analysis/` al repositorio donde guardas el conocimiento de ese sistema, borra la herramienta e instala stark; su `arqueologo-codigo` encuentra el discovery en `docs/analysis/`.

## Prueba en 5 minutos, sin legacy

```bash
python3 -m pepper demo        # correlate + package sobre examples/legacy-demo
cd pepper-out/legacy-demo/package && claude     # o codex: el paquete trae CLAUDE.md y AGENTS.md
```

El juguete esconde tres cosas: una regla no documentada, una mentira en el manual y una rama que el flujo no ejercita. La clave: [`examples/legacy-demo/expected/notes.md`](examples/legacy-demo/expected/notes.md); la salida de referencia: [`expected/funcional.md`](examples/legacy-demo/expected/funcional.md).

## Preguntas que casi siempre salen

**¿Tengo que tener el código fuente?** No. Con el WAR y el respaldo, `pepper map` saca pantallas, clases (del bytecode), tablas, catálogos y triggers; `rehydrate` lo levanta; `explore` lo recorre.

**¿Y si mi stack no tiene perfil?** `/pepper` para en el borrador que redacta `inspector-legacy`; lo revisas y corres de nuevo. Si el sistema ya corre en otro lado, `/pepper-observe` con colectores genéricos (menos profundidad, y así se declara).

**¿Puedo observar producción en vez de levantar?** Sí, con dos límites: sin la observabilidad agresiva de un entorno desechable, y con datos reales en la evidencia. Si puedes levantar, levanta.

**¿Qué sabe el explorador que una persona no, y al revés?** El explorador descubre lo que el sistema *permite y rechaza* con cada rol, sin cansarse; no sabe cómo lo usa la oficina. Los datos reales dicen qué se usa; la sección 12 dice a quién preguntarle el resto.

**¿Y si me dieron un JAR, un dist de Node, un .NET?** Hoy `detect` reconoce **WAR de Java + respaldo PostgreSQL** y **WAR de Grails 1.x + respaldo MySQL**; un JAR ejecutable todavía no. Con cualquier otro stack `/pepper` se detiene en el inventario de lo que vio y en un borrador de perfil (`profiles/<id>/`, `status: draft`) que una persona revisa; ese perfil — señales de detección, extractores del mapa, receta para levantarlo, parsers de sus logs — es la forma de extender la herramienta, y va como datos, nunca como código del núcleo. Ver [`PERFILES.md`](docs/documentacion/PERFILES.md).

**¿Claude Code o Codex?** Claude Code. Los comandos son archivos de instrucciones y el paquete trae `AGENTS.md` para que otro agente pueda leerlo, pero **con Codex no se ha probado**.

## Estado

| Pieza | Estado |
|---|---|
| Núcleo: detect, map, rehydrate, isolate, proxy, explore, collect, correlate, package, export | implementados y probados (suite en `tests/`, `scripts/verificar.py`, CI). **El ciclo completo corrió en frío** desde un clon limpio de este repo, sin manos, hasta `funcional.md` (2026-09-11) |
| Perfil `java-springboot-jsf-postgres` | `draft`; corrió el pipeline entero contra un legacy real (mapa, levantar, explorar, descubrir). Un perfil `draft` corre igual: `environment.json` y `funcional.md` lo declaran; lo promueve a `validated` una persona tras verlo correr |
| Perfil `java-wildfly-postgres` | `draft`; parsers |
| Perfil `groovy-grails1-tomcat-mysql` | `draft`; el primer stack sin PostgreSQL: el mapa corrió contra un legacy real (1162 rutas, 10 jobs); `rehydrate` genera plan y compose; **el levantamiento de punta a punta está pendiente** de un respaldo de la base de la aplicación |
| Pendientes | promover un perfil a `validated`; levantar el perfil Grails/MySQL de punta a punta; multicomponente (varios desplegables terminan en `BLOCKED` con la lista de piezas); un perfil no-JVM; CI con un E2E de Docker (hoy CI corre la suite, la demo sintética y la prueba hermética del navegador contra el ingress) |

## Stack y requisitos

Claude Code · Python 3.9+ (`jsonschema` para publicar; `playwright` para explorar) · Docker con Compose v2 · JDK (`javap`) para el mapa de un artefacto JVM. Se usa desde el clon con `python3 -m pepper`; no se instala como paquete.

`python3 scripts/verificar.py` valida frontmatters, fences, links, nombres, scripts y contratos; `python3 -m unittest discover -s tests` corre la suite. El CI ejecuta ambos.

## Licencia y autoría

PEPPER — herramienta creada por [iamgabstark_](https://github.com/GabrielaStark). Licencia **MIT**. Complemento independiente de **stark**, de la misma autora.
