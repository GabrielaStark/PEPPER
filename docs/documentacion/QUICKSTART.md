# QUICKSTART de PEPPER

Le das un binario y un respaldo; te entrega **qué hace el sistema**. El porqué de cada decisión está en [`PRINCIPIOS.md`](PRINCIPIOS.md) y [`DECISIONES.md`](DECISIONES.md); qué hace cada comando del núcleo, en [`REFERENCIA.md`](REFERENCIA.md); qué protege y qué no, en [`THREAT-MODEL.md`](THREAT-MODEL.md); si algo se traba, [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md).

## 1. Instala la herramienta

Requisitos: Python 3.9+, Docker con Compose v2 (Linux o macOS) con acceso al registro de imágenes, un JDK con `javap` (JDK 8 además si el legacy es Groovy 1.7), Playwright con Chromium, y Claude Code con acceso a Opus (solo Claude Code por ahora; con Codex no está probado).

```bash
git clone https://github.com/GabrielaStark/PEPPER.git && cd PEPPER
pip install -r requirements-dev.txt && python3 -m playwright install chromium
python3 scripts/verificar.py && python3 -m unittest discover -s tests   # opcional: todo en verde en tu máquina
```

La herramienta se queda en ese clon: se actualiza con `git pull`, tiene versión (`python3 -m pepper --version`) y recibe perfiles como contribuciones.

## 2. Crea un workspace para el legacy

```bash
python3 -m pepper init ~/mi-legacy
```

Crea `~/mi-legacy/` con `legacy/` (vacío, con `NOTAS.md` para lo que sepas del sistema), `docs/pepper/`, `pepper-out/`, `evidence/`, los comandos y agentes de Claude Code, el guardia de datos, y un enlace `pepper` a la herramienta para que `python3 -m pepper …` funcione desde ahí. **Sin repositorio ni remoto**: no hay a dónde subir el sistema de alguien por accidente; si quieres versionar el producto (`docs/pepper/`), haces `git init` tú, y el `.gitignore` del workspace ya excluye `legacy/`, `evidence/` y `pepper-out/`.

## 3. Pon los artefactos

En `~/mi-legacy/legacy/`: el desplegable (hoy con perfil: WAR de Spring Boot o de Grails 1.x, fat jars de Spring Boot) y el respaldo de la base (`pg_dump -Fc`, o SQL en texto de `mysqldump`/`mariadb-dump`), y, si sabes algo, una línea en `legacy/NOTAS.md` ("producción es WildFly 21" ahorra una desviación; la versión del servidor es obligatoria si el artefacto no la trae). Nada más.

## 4. Corre

```text
cd ~/mi-legacy && claude
/pepper
```

Eso es todo. En orden, y sin preguntarte nada salvo que se atore:

| Paso | Qué hace | Deja |
|---|---|---|
| mapa | abre el desplegable y el respaldo: pantallas con botones y mensajes, clases con constantes, tablas con conteo, triggers y funciones con cuerpo, catálogos completos (roles, menús por rol, estados), distribuciones reales; todo redactado | `docs/pepper/system-map.json` + `map/*.md` |
| levantar | fabrica la red que el artefacto espera (sus IPs, su base, su usuario), restaura el respaldo, manda todo host externo a un stub, verifica el aislamiento antes de crear, según Docker antes de arrancar, y en vivo con una sonda | `docs/pepper/environment.json`, `validation.md` |
| explorar | entra con cada rol (las claves de usuario las resuelve el núcleo dentro del contenedor), abre cada pantalla, intenta guardar en vacío (rechazos), llena y guarda, fotografía; después recorre con planes los flujos encadenados | `evidence/explore-*/` |
| descubrir | correlaciona lo observado con el mapa y escribe el documento | **`docs/pepper/funcional.md`** |

Tarda: el mapa segundos, levantar cerca de un minuto (más en arm64 con imágenes amd64 emuladas), **explorar según el sistema** (~1 minuto por pantalla con formulario y por rol; en el legacy validado, 64 rutas con dos roles; `--budget` lo acota), descubrir varios más.

Se detiene en: aislamiento en rojo (no se levanta ni se explora nada); falta un insumo (`BLOCKED` con la lista de qué conseguir); ambigüedad que solo tú resuelves (varios perfiles de configuración, varios respaldos, la versión del servidor: una línea en NOTAS.md); stack sin perfil (deja el borrador para que lo revises); el paquete trae datos que **tú autorizas en tu terminal** con `python3 -m pepper authorize …` (te pide escribir AUTORIZO; una vez por sistema y por categoría de datos); un recorrido FALLIDO o INTERRUMPIDO; y si Export rechaza el documento (lo corrige el agente).

`/pepper mapa|levantar|explorar|descubrir` retoma desde una fase.

## 5. Lee

`docs/pepper/funcional.md`, 12 secciones: en una frase · quién lo usa (roles y qué puede hacer cada uno) · el recorrido principal · las otras puertas de entrada · estados con conteos reales · lo que pasa solo · acceso y sesión · sistemas externos y qué pasa si fallan · reportes · catálogos que definen el negocio · volumen real · **lo que no sé y a quién preguntarle**. Cada afirmación trae su origen: `[código]` `[base]` `[datos]` `[observado]` `[config]` `[doc]`. Export comprobó que cada fuente exista y que no haya credenciales ni datos con patrón; que la fuente sostenga lo que dice, lo compruebas tú.

La sección 12 es tu lista de trabajo: lo que se resuelve preguntándole a alguien, y lo que se resuelve con otra ventana.

## 6. Si hay quien conozca un flujo

```text
/pepper-observe <nombre del flujo>
```

PEPPER abre su propio navegador (con el resolver cerrado, todo por el ingress); la persona opera ahí (un flujo a la vez, que provoque un rechazo, que cierre la ventana al terminar); se captura y el documento se extiende. No se le pregunta nada que la evidencia ya responda.

## 7. Prueba en 5 minutos, sin legacy

```bash
python3 -m pepper demo        # correlate + package sobre examples/legacy-demo (sin mapa ni Docker)
```

Luego `cd pepper-out/legacy-demo/package && claude` para que un agente escriba el discovery, y `python3 -m pepper export …` para publicarlo (el comando exacto lo imprime `demo`). Califica contra [`examples/legacy-demo/expected/notes.md`](../../examples/legacy-demo/expected/notes.md).

## 8. Al terminar

```bash
docker compose -f pepper-out/rehydrate/docker-compose.yml down -v   # borra el volumen con datos reales
```

Lo que queda en el workspace es `docs/pepper/` (y `docs/analysis/funcional.md`, por si el conocimiento sigue hacia stark). `legacy/`, `evidence/` y `pepper-out/` son datos ajenos: bórralos cuando el dato lo exija, junto con los transcripts de Claude Code (`~/.claude/projects/…`), que guardan lo que el agente leyó.
