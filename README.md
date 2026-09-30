# PEPPER

> Observar primero, inferir después, comparar al final.
>
> Por [@iamgabstark_](https://iamgabstark.com/) · [Principios](docs/documentacion/PRINCIPIOS.md) · [Qué protege y qué no](docs/documentacion/THREAT-MODEL.md) · [Seguridad](SECURITY.md) · [Contribuir](CONTRIBUTING.md)

**Le das el desplegable de un sistema legacy y un respaldo de su base, y PEPPER saca todo lo que el sistema ES (pantallas, roles, catálogos, reglas en la base), lo levanta en contenedores sin salida, lo recorre solo con cada rol, y escribe QUÉ HACE: quién lo usa y qué puede hacer cada quien, los recorridos, los estados, las reglas de negocio, lo que corre solo, con qué habla y qué pasa si falla, cuánto se usa, y qué no se sabe — cada afirmación con su origen.** Es el documento con el que alguien empieza una reingeniería sin haber visto el sistema.

**¿Por qué PEPPER?** **P**lataforma de **E**videncia y **P**rocesamiento para **P**atrones de **E**jecución y **R**eingeniería. PEPPER se usa sola; su salida puede seguir hacia [stark](https://github.com/GabrielaStark/stark), de la misma autora, como conocimiento `inferido` que una persona confirma.

## Para qué sistemas sirve hoy

PEPPER aspira a cualquier legacy; hoy corre de punta a punta con **aplicaciones web sobre la JVM con PostgreSQL o MySQL**: un WAR de Spring Boot con JSF sobre WildFly, un WAR de Grails 1.x sobre Tomcat, fat jars de Spring Boot. Cuatro perfiles: uno `validated` (corrió el ciclo completo contra un legacy real con su respaldo de producción y la persona responsable confirmó que el documento describe su sistema) y tres `draft`. Con cualquier otro stack, `/pepper` se detiene en el inventario de lo que vio y en un borrador de perfil que una persona revisa. Qué cuesta una familia nueva (.NET, PHP, escritorio, COBOL) está en [`PERFILES.md`](docs/documentacion/PERFILES.md): un sistema dentro de una familia conocida es un perfil; una familia nueva es un lector nuevo en el núcleo, y así se dice.

## Lo que sale de la máquina, y lo que no

- Los contenedores del legacy **no tienen salida**: red interna verificada antes de crear, según Docker antes de arrancar, y en vivo con una sonda desde dentro que intenta salir y tiene que fracasar. Todo host externo del artefacto se resuelve a un stub que registra y cierra. El navegador que recorre el sistema (y el que usa una persona) es un Chromium de PEPPER con el resolver cerrado.
- **El agente que orquesta es un modelo remoto**, y lo que lee sale de la máquina. Un guardia técnico le impide abrir el respaldo, el desplegable, la evidencia cruda y la base desechable; lo que sí lee (el mapa redactado, el estado del entorno, el registro de acciones del explorador) está escrito en [`THREAT-MODEL.md`](docs/documentacion/THREAT-MODEL.md).
- El paquete que el modelo remoto analiza lleva solo lo que PEPPER pudo inspeccionar y sustituir (credenciales quitadas; CURP, RFC, correo, CLABE y tarjeta con seudónimo), con la autorización que **una persona** da en su terminal, por sistema y por categoría. El respaldo y el desplegable no viajan. Un nombre propio o un dato sin patrón no se detecta.
- Si tus datos no pueden salir así, PEPPER no es para ti todavía: no existe un camino con modelo local.

## Requisitos

| Qué | Para qué | Notas |
|---|---|---|
| Python 3.9 o superior | el núcleo | probado en 3.9 (CI) y 3.11; solo biblioteca estándar más `jsonschema`, `pyyaml`, `playwright` (`pip install -r requirements-dev.txt`) |
| Docker con Compose v2, en Linux o macOS | levantar el legacy aislado | Docker Engine y Docker Desktop probados (Ubuntu amd64 en CI, macOS arm64 a mano); necesita acceso al registro para bajar las imágenes de las versiones del legacy. Podman, OrbStack, Rancher Desktop y WSL2 **no verificados** (la sonda dirá si aíslan). Windows containers: no |
| JDK con `javap` | leer el bytecode de un artefacto JVM | para Groovy 1.7 hace falta además un JDK 8 |
| Playwright con Chromium (`python3 -m playwright install chromium`) | el explorador y `/pepper-observe` | descarga neutral del navegador |
| **Claude Code**, con acceso a Opus | orquestar `/pepper`, escribir `explore.json` y los planes, y el documento | es un servicio de pago de Anthropic; sin él tienes el núcleo (mapa, entorno, evidencia, `flow.md`) pero no `funcional.md`. Codex: no probado, y sin el guardia de datos |

## En tres pasos

```bash
git clone https://github.com/GabrielaStark/PEPPER.git && cd PEPPER
pip install -r requirements-dev.txt && python3 -m playwright install chromium
python3 -m pepper init ~/mi-legacy          # el workspace, aparte de la herramienta, sin remoto
cp /ruta/al/sistema.war /ruta/al/respaldo.dump ~/mi-legacy/legacy/   # y una línea en legacy/NOTAS.md si sabes algo
cd ~/mi-legacy && claude                     # abre Claude Code EN el workspace; adentro: /pepper
```

La herramienta se queda en su clon (se actualiza con `git pull`, se versiona, recibe perfiles como contribuciones) y el trabajo sobre cada legacy ocurre en un workspace aparte que no tiene repositorio ni remoto: no hay a dónde subir el sistema de alguien por accidente. Un perfil que redactes ahí se contribuye como un cambio normal a la herramienta ([`CONTRIBUTING.md`](CONTRIBUTING.md)).

`/pepper` corre todo y no te pregunta nada salvo que se atore. Tarda: el mapa segundos, levantar un minuto (más en arm64 si las imágenes son amd64 emuladas), **explorar según el sistema** (~1 minuto por pantalla con formulario y por rol; `--budget` lo acota), descubrir varios minutos más. No se colgó: está trabajando.

| Fase | Qué hace | Deja |
|---|---|---|
| **mapa** | abre el desplegable y el respaldo: pantallas con botones y mensajes, clases con constantes, tablas con conteo, triggers y funciones con cuerpo, catálogos completos (roles, menús por rol, estados), distribuciones reales; todo redactado | `docs/pepper/system-map.json` + `map/*.md` |
| **levantar** | fabrica la red que el artefacto espera (sus IPs, su base, su usuario), restaura el respaldo, manda todo host externo a un stub, verifica el aislamiento antes de crear, antes de arrancar y en vivo | `docs/pepper/environment.json` |
| **explorar** | entra con cada rol, abre cada pantalla, intenta guardar en vacío (rechazos), llena y guarda, fotografía; después recorre con planes los flujos encadenados | `evidence/explore-*/` |
| **descubrir** | cruza lo observado con el mapa y escribe el documento | **`docs/pepper/funcional.md`** |

Se detiene, y en todos los casos dice qué: el aislamiento no está en verde; falta un insumo (`BLOCKED`, con la lista de qué conseguir); hay ambigüedad que solo una persona resuelve (varios perfiles de configuración, varios respaldos, la versión del servidor); el stack no tiene perfil (deja un borrador para que una persona lo revise); el paquete trae datos que una persona tiene que autorizar en su terminal (`pepper authorize`; una vez por sistema y por categoría); un recorrido del explorador termina FALLIDO o INTERRUMPIDO; o Export rechaza el documento (el agente corrige, tú no). Si hay quien conozca un flujo, `/pepper-observe <flujo>`: la persona opera en el navegador de PEPPER, se captura, el documento se extiende.

Camino completo: [`docs/documentacion/QUICKSTART.md`](docs/documentacion/QUICKSTART.md).

## El entregable

`docs/pepper/funcional.md`, doce secciones fijas: en una frase · quién lo usa (roles, personas, matriz opción × rol) · el recorrido principal · las otras puertas de entrada · estados con conteos reales · lo que pasa solo · acceso y sesión · sistemas externos y qué pasa si fallan · reportes · catálogos que definen el negocio · volumen real · **lo que no sé y a quién preguntarle**. Cada afirmación trae `[código]`, `[base]`, `[datos]`, `[observado]`, `[config]` o `[doc]`. `pepper export` comprueba que cada fuente exista y que el documento no lleve credenciales ni datos de personas con patrón; que la fuente sostenga la afirmación lo compruebas tú al leer. Es del sistema, no de una corrida: se acumula sesión a sesión. Contrato: [`schemas/functional-discovery.schema.json`](schemas/functional-discovery.schema.json).

## Las piezas

- **El núcleo** (`pepper/`, Python 3.9+): `detect`, `map` (con lectores propios de `pg_dump -Fc`, SQL en texto, bytecode JVM y Groovy compilado), `rehydrate`, `isolate`, `proxy` (el ingress), `explore` (Playwright, local), `collect`, `correlate`, `package`, `export`, `init`. Hace lo mecánico igual cada vez. Conoce **formatos** (los lectores viven en `pepper/inspect/readers/`), nunca un sistema.
- **Los perfiles** (`profiles/<id>/`): detección, extractores del mapa, receta de rehydrate (plantillas de compose y restauración, imágenes por versión), parsers de logs, lectura de formularios, fixtures. Datos que parametrizan los lectores del núcleo. Un sistema nuevo dentro de una familia conocida es un perfil nuevo.
- **Los agentes** (`.claude/`): `/pepper` y `/pepper-observe`; el subagente `descubridor-funcional` escribe el documento desde el paquete controlado; `inspector-legacy` redacta perfiles cuando ninguno aplica. Sus constituciones son las skills. Y el guardia de datos (`scripts/guardia_datos.py`), que acota lo que el agente puede leer.
- **Los contratos** (`schemas/`): la interfaz entre todo; cualquier pieza es reemplazable mientras respete su schema.

Arquitectura: [`ARQUITECTURA.md`](docs/documentacion/ARQUITECTURA.md) · perfiles: [`PERFILES.md`](docs/documentacion/PERFILES.md) · por qué: [`DECISIONES.md`](docs/documentacion/DECISIONES.md) · comandos del núcleo: [`REFERENCIA.md`](docs/documentacion/REFERENCIA.md) · problemas: [`TROUBLESHOOTING.md`](docs/documentacion/TROUBLESHOOTING.md) · la auditoría que motivó esta versión: [`AUDITORIA-2026-09-29.md`](docs/documentacion/AUDITORIA-2026-09-29.md) · qué cambió: [`CHANGELOG.md`](CHANGELOG.md).

## La regla de oro

| | Qué es | Dónde vive | ¿Va a git? |
|---|---|---|---|
| **Herramienta** | este repositorio | su clon | ✅ el de PEPPER; se actualiza con `git pull` |
| **Producto** | `docs/pepper/` (mapa, entorno, `funcional.md`, `discovery/`) y `docs/analysis/funcional.md` | el workspace | ✅ al repositorio donde guardas el conocimiento de **ese** sistema, si quieres versionarlo (`git init` tuyo; el workspace nace sin remoto) |
| **Datos ajenos** | `legacy/`, `evidence/`, `pepper-out/` | el workspace | ❌ nunca (el `.gitignore` del workspace los excluye) |

Al terminar: `docker compose -f pepper-out/rehydrate/docker-compose.yml down -v` y conserva `docs/pepper/`. Los transcripts de Claude Code guardan lo que el agente leyó y viven fuera del workspace: bórralos también si el dato lo exige.

## Prueba en 5 minutos, sin legacy

```bash
python3 -m pepper demo        # correlate + package sobre examples/legacy-demo
cd pepper-out/legacy-demo/package && claude     # el paquete trae CLAUDE.md y AGENTS.md
```

El juguete esconde tres cosas: una regla no documentada, una mentira en el manual y una rama que el flujo no ejercita. La clave: [`examples/legacy-demo/expected/notes.md`](examples/legacy-demo/expected/notes.md); la salida de referencia: [`expected/funcional.md`](examples/legacy-demo/expected/funcional.md). El demo cubre correlate → package → export; el levantamiento aislado real lo prueba `scripts/e2e_docker.py` en CI con un servicio sintético.

## Preguntas que casi siempre salen

**¿Tengo que tener el código fuente?** No. Con el desplegable y el respaldo, `pepper map` saca pantallas, clases (del bytecode), tablas, catálogos y triggers; `rehydrate` lo levanta; `explore` lo recorre.

**¿Y si mi stack no tiene perfil?** `/pepper` para en el borrador que redacta `inspector-legacy`; lo revisas, lo pruebas con los fixtures del perfil y corres de nuevo. Si el sistema ya corre en otro lado, `/pepper-observe` con colectores genéricos (menos profundidad, y así se declara).

**¿Puedo observar producción en vez de levantar?** Sí, con dos límites: sin la observabilidad agresiva de un entorno desechable, y con datos reales en la evidencia. Si puedes levantar, levanta.

**¿Qué sabe el explorador que una persona no, y al revés?** El explorador descubre lo que el sistema *permite y rechaza* con cada rol, sin cansarse; no sabe cómo lo usa la oficina. Los datos reales dicen qué se usa; la sección 12 dice a quién preguntarle el resto.

**¿Y si me dieron un dist de Node, un .NET, un PHP?** Un stack cuyo código fuente viaja en el desplegable (PHP, Django, Rails) se mapea con datos (`regex_extractor`, lectores de configuración genéricos) y necesita una receta de rehydrate con sus imágenes: es un perfil. IL de .NET Framework, escritorio y COBOL necesitan lectores y un destino de ejecución que el núcleo no tiene: está escrito en [`PERFILES.md`](docs/documentacion/PERFILES.md) qué costaría.

**¿Claude Code o Codex?** Claude Code. Los comandos son archivos de instrucciones y el paquete trae `AGENTS.md` para que otro agente pueda leerlo, pero **con Codex no se ha probado** y el guardia de datos es un hook de Claude Code.

## Estado

| Pieza | Estado |
|---|---|
| Núcleo: detect, map, rehydrate, isolate, proxy, explore, collect, correlate, package, export, init | implementados y probados (suite en `tests/`, `scripts/verificar.py`, CI). CI corre la suite, una prueba hermética con Chromium real detrás del ingress, y un E2E que **levanta un entorno real con Docker** en cada cambio (`scripts/e2e_docker.py`: base restaurada, aplicación servida por el ingress, aislamiento en vivo con sonda). En CI nada de eso se salta. El ciclo completo corrió de punta a punta contra un legacy real con datos de producción (2026-09-22) |
| Perfil `groovy-grails1-tomcat-mysql` | **`validated`** (2026-09-22): el ciclo completo contra un legacy real con su respaldo de producción, recorrido con dos roles, y la persona responsable confirmó que el documento describe su sistema. Es la única validación externa: n = 1 |
| Perfil `java-springboot-jsf-postgres` | `draft`; corrió el pipeline entero contra un legacy real (mapa, levantar, explorar, descubrir) |
| Perfil `java-springboot-fatjar-postgres` | `draft`; varias piezas; probado con artefactos sintéticos y Docker real en CI, nunca contra un sistema real |
| Perfil `java-wildfly-postgres` | `draft`; parsers y extractores heredados sin corrida real |
| Perfil `php-apache-mysql` | `draft`; la primera familia que no es JVM y la primera sin una línea de Python: detección, datasource en `.env`, rutas, jobs, pantallas y respaldo con lectores genéricos; el fuente viaja como carpeta. Redactado sin legacy real: fixtures sintéticos en CI |
| Versión | `0.2.0` (`pepper --version`); qué cambió y qué hallazgo cierra cada cambio: [`CHANGELOG.md`](CHANGELOG.md) |
| Pendientes | `php-apache-mysql` corrido contra un legacy real (hoy solo fixtures); un sistema con **una base por servicio**; .NET Framework, escritorio y COBOL (lectores y destino de ejecución nuevos); un camino con modelo local |

## Contribuir

[`CONTRIBUTING.md`](CONTRIBUTING.md): cómo se redacta y se prueba un perfil sin un legacy real (fixtures), qué se limpia antes de contribuirlo, y cómo se corre la suite. Fugas: [`SECURITY.md`](SECURITY.md), en privado.

## Licencia y autoría

PEPPER — herramienta creada por [iamgabstark_](https://github.com/GabrielaStark). Licencia **MIT**.
