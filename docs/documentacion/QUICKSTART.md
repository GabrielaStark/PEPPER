# QUICKSTART de PEPPER

Le das un binario y un respaldo; te entrega **qué hace el sistema**. El porqué de cada decisión está en [`PRINCIPIOS.md`](PRINCIPIOS.md) y [`DECISIONES.md`](DECISIONES.md); qué hace cada comando del núcleo, en [`REFERENCIA.md`](REFERENCIA.md); si algo se traba, [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md).

## 1. Instala

Clona PEPPER con el nombre de tu proyecto y abre Claude Code **en esa carpeta** (solo Claude Code por ahora; con Codex no está probado) (los comandos `/pepper*` se cargan de `.claude/commands/` de la raíz). Requisitos: `python3` 3.9+, `pip install -r requirements-dev.txt` (jsonschema, playwright), `python3 -m playwright install chromium`, Docker con Compose v2, JDK (`javap`).

```bash
git clone https://github.com/GabrielaStark/PEPPER.git mi-legacy && cd mi-legacy
rm -rf .git     # ← desconecta el repo de la herramienta antes de meter nada
pip install -r requirements-dev.txt && python3 -m playwright install chromium
```

**Por qué se borra `.git`:** el clon viene con el remoto de PEPPER conectado. Trabajar adentro con un legacy y ese remoto puesto es la única forma de publicar por accidente el sistema de un cliente en un repositorio público. Sin `.git` no hay a dónde subir. Si quieres conservar git (para traer actualizaciones de la herramienta), corre al menos `git remote remove origin`; mientras haya algo en `legacy/` y el remoto siga puesto, cada comando del núcleo te lo recuerda. El producto (`docs/pepper/`, `docs/analysis/`) se copia al final al repositorio donde guardas el conocimiento de ese sistema.

## 2. Pon los artefactos

La carpeta no viene en el clon (git no versiona carpetas vacías): `mkdir -p legacy`. Adentro: el desplegable (hoy con perfil: **WAR** de Java; un JAR ejecutable aún no se detecta), el respaldo de la base (formato custom de `pg_dump`, `-Fc`) y, si sabes algo, `legacy/NOTAS.md` (una línea como "producción es WildFly 21" ahorra una desviación). Nada más. `legacy/`, `evidence/` y `pepper-out/` no se versionan nunca.

## 3. Corre

```text
/pepper
```

Eso es todo. En orden, y sin preguntarte nada salvo que se atore:

| Paso | Qué hace | Deja |
|---|---|---|
| mapa | abre el WAR y el respaldo: pantallas con botones y mensajes, clases con constantes, tablas con conteo, triggers y funciones con cuerpo, catálogos completos (roles, menús por rol, estados), distribuciones reales | `docs/pepper/system-map.json` + `map/*.md` |
| levantar | fabrica la red que el artefacto espera (sus IPs, su base, su usuario), restaura el respaldo, manda todo host externo a un stub, verifica el aislamiento antes y en vivo | `docs/pepper/environment.json`, `validation.md` |
| explorar | entra con cada rol, abre cada pantalla, intenta guardar en vacío (rechazos), llena y guarda, fotografía; después recorre con planes los flujos encadenados | `evidence/explore-*/` |
| descubrir | correlaciona lo observado con el mapa y escribe el documento | **`docs/pepper/funcional.md`** |

Tarda: el mapa segundos, levantar cerca de un minuto, **explorar unos diez minutos** (seis roles por diecinueve rutas del enrutador en el primer legacy real; las «pantallas» del mapa son más, porque cuenta diálogos y pasos), descubrir varios más. Se detiene en: aislamiento en rojo (no se levanta ni se explora nada); falta un insumo (`BLOCKED` con la lista de qué conseguir); stack sin perfil (deja el borrador para que lo revises); **una vez**, para preguntarte si el paquete con datos del legacy puede ir al modelo remoto — la respuesta se guarda en `pepper-out/data-boundary.json`; y si Export rechaza el documento (lo corrige el agente).

`/pepper mapa|levantar|explorar|descubrir` retoma desde una fase.

## 4. Lee

`docs/pepper/funcional.md`, 12 secciones: en una frase · quién lo usa (roles y qué puede hacer cada uno) · el recorrido principal · las otras puertas de entrada · estados con conteos reales · lo que pasa solo · acceso y sesión · sistemas externos y qué pasa si fallan · reportes · catálogos que definen el negocio · volumen real · **lo que no sé y a quién preguntarle**. Cada afirmación trae su origen: `[código]` `[base]` `[datos]` `[observado]` `[config]` `[doc]`.

La sección 12 es tu lista de trabajo: lo que se resuelve preguntándole a alguien, y lo que se resuelve con otra ventana.

## 5. Si hay quien conozca un flujo

```text
/pepper-observe <nombre del flujo>
```

La persona opera el sistema levantado (un flujo a la vez, que provoque un rechazo, que diga "terminé"); PEPPER captura y el documento se extiende. No se le pregunta nada que la evidencia ya responda.

## 6. Prueba en 5 minutos, sin legacy

```bash
python3 -m pepper demo        # correlate + package sobre examples/legacy-demo (sin mapa ni Docker)
```

Luego `cd pepper-out/legacy-demo/package && claude` para que un agente escriba el discovery, y `python3 -m pepper export …` para publicarlo (el comando exacto lo imprime `demo`). Califica contra [`examples/legacy-demo/expected/notes.md`](../../examples/legacy-demo/expected/notes.md).

## 7. Al terminar

```bash
docker compose -f pepper-out/rehydrate/docker-compose.yml down -v   # borra el volumen con datos reales
```

Lo que queda en el repo es `docs/pepper/`. La herramienta se borra o se ignora; `docs/analysis/funcional.md` es lo que stark lee.
