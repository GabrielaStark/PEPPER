# PEPPER

> Observar primero, inferir después, comparar al final.
>
> Por [@iamgabstark_](https://iamgabstark.com/) · complemento de [stark](https://github.com/GabrielaStark/stark) · [Principios](docs/documentacion/PRINCIPIOS.md)

**PEPPER es descubrimiento dinámico de sistemas legacy: toma lo que quede de un sistema, intenta volverlo a poner vivo en contenedores, observa una ejecución real, correlaciona la evidencia y la convierte en conocimiento estructurado — flujos observados, reglas candidatas, contradicciones y desconocidos, cada uno con su evidencia.**

**¿Por qué PEPPER?** **P**lataforma de **E**videncia y **P**rocesamiento para **P**atrones de **E**jecución y **R**eingeniería. Y sí: Pepper organiza la realidad antes de que Stark actúe.

Donde stark lee el manual del coche, PEPPER lo enciende, lo maneja y te dice qué hace de verdad. Sirve para cualquier legacy: lo que la herramienta sabe de cada tecnología entra como datos (perfiles), nunca como código del núcleo.

Clonas, corres `/pepper-init`, y la herramienta te lleva fase por fase: [empieza aquí →](docs/documentacion/QUICKSTART.md)

---

## Las piezas: qué es cada cosa

- **stark** — el framework Spec-Driven de la autora: convierte Claude Code en un equipo que trabaja con specs, con gates humanos y sellos. Hace **onboarding estático** de un legacy: código, documentación, configuración.
- **PEPPER** — esta herramienta: **descubrimiento dinámico**. Agrega la fuente que el análisis estático no puede ver: el comportamiento real del sistema mientras corre. Es independiente de stark, y lo complementa: su salida es el análisis arqueológico que stark consume en reingeniería.
- **El núcleo** (`pepper/`) — Python y biblioteca estándar para captura/correlación; `jsonschema` es obligatorio para validar y publicar. Hace lo mecánico y repetible. Las manos.
- **Los agentes** (`.claude/`) — comandos `/pepper-*`, subagentes y skills, con la misma forma que stark: una fase por comando, un gate humano al final de cada una. La cabeza.

> **PEPPER piensa antes de que stark actúe.**

### El problema

Entender un legacy no es solo leer código. Hay flujos que nadie documentó, reglas de negocio escondidas, dependencias que solo aparecen al ejecutar, ambientes que nadie sabe levantar, y documentación que afirma cosas que el sistema no hace. Cada fuente miente un poco. PEPPER contrasta las tres:

```text
lo que dice el código + lo que dice la documentación + lo que realmente hace el sistema
```

### Lo que PEPPER no es

No es "una IA que lee logs". El agente interpreta; PEPPER prepara la realidad que debe interpretar: reconstruye el entorno, captura, normaliza, correlaciona, reduce el contexto, y valida que cada conclusión apunte a evidencia. No repara nada, no moderniza nada, y no promete levantar cualquier sistema — cuando no puede, dice exactamente qué falta.

---

## Quick Start

### Instalar

Paso a paso, empezando de cero.

**Requisitos**: `git`, `python3` (3.9+; el que trae macOS sirve), **Docker** (para levantar el legacy) y [Claude Code](https://claude.com/claude-code) o Codex — los comandos `/pepper-*` corren ahí.

Dos formas, según dónde viva tu legacy:

| Tu situación | Modo |
|---|---|
| Tienes artefactos sueltos (WAR, EXE, dist, dump, configs) sin repo | **Workspace** (lo que sigue) |
| El legacy tiene su repo y ahí mismo vas a seguir con stark | **Encima del repo** (más abajo) |

**Workspace:**

```bash
# 1. Descarga la herramienta con el nombre de TU proyecto
git clone https://github.com/GabrielaStark/PEPPER.git sistema-nominas
cd sistema-nominas

# 2. IMPORTANTE — desconéctate del repo de la herramienta EMPEZANDO DE CERO.
#    (quitar solo el remote NO basta: los archivos de PEPPER seguirían rastreados
#    y un push a tu propio remoto versionaría toda la herramienta — regla de oro)
rm -rf .git && git init

#    (¿tu proyecto tiene remoto propio? git remote add origin <tu-repo>)

# 3. Opcional, recomendado: validación de contratos y resolución de compose sin Docker
pip install -r requirements-dev.txt

# 4. Pon TODO lo que tengas del legacy en legacy/
#    (la carpeta no viene en el clon: git no versiona carpetas vacías)
mkdir -p legacy
cp /ruta/a/lo-que-tengas/* legacy/
```

Verifica antes del primer push a tu propio remoto: `git remote -v` no debe mencionar `GabrielaStark/PEPPER`, y `git ls-files | head` no debe listar archivos de la herramienta (con `rm -rf .git && git init` quedan sin rastrear hasta que el `.gitignore` decide).

**Encima del repo** (desde la raíz del repo del legacy):

```bash
git clone --depth 1 https://github.com/GabrielaStark/PEPPER.git /tmp/pepper && \
cp -r /tmp/pepper/.claude /tmp/pepper/pepper /tmp/pepper/schemas /tmp/pepper/profiles /tmp/pepper/templates . && \
mkdir -p docs/pepper && cp -r /tmp/pepper/docs/documentacion docs/ && \
[ -f CLAUDE.md ] || cp /tmp/pepper/CLAUDE.md . ; [ -f AGENTS.md ] || cp /tmp/pepper/AGENTS.md . ; \
cp /tmp/pepper/LICENSE LICENSE.pepper && \
printf '.claude/\npepper/\nschemas/\nprofiles/\ntemplates/\ndocs/documentacion/\nCLAUDE.md\nAGENTS.md\nLICENSE.pepper\npepper-out/\nevidence/\n' >> .gitignore && \
rm -rf /tmp/pepper
```

Aquí no hay remote que quitar: la herramienta llega copiada (no clonada) y gitignoreada; tu repo sigue apuntando a donde siempre. Si el repo ya tenía `.claude/`, `CLAUDE.md` o `AGENTS.md` propios, revisa antes: `cp -r` sobreescribe archivos del mismo nombre (los de PEPPER se llaman `pepper-*`, así que conviven con los de stark; los de la raíz no se pisan gracias al `[ -f … ] ||`). En este modo los artefactos son el repo mismo: `/pepper-inspect .`, y el núcleo excluye su propia herramienta al detectar y al empaquetar.

### Usarlo: tú mandas en cada gate

```bash
claude          # (o codex) desde la raíz del workspace
```

y adentro:

1. **`/pepper-init`** — verifica herramientas, prepara carpetas, detecta qué perfil aplica y te deja `legacy/NOTAS.md`: **escribe ahí lo que sepas** (qué servidor corre en producción, versiones, cómo se levanta, servicios externos, flujos que importan). Tu nota manda sobre lo que la herramienta infiera.
2. **`/pepper-inspect`** — el análisis del stack detectado, con cada afirmación citando evidencia.
3. **`/pepper-rehydrate`** — te presenta el **plan** para levantarlo en local y se detiene ✋. Este gate es tuyo: si el plan dice WildFly y tú sabes que producción es Tomcat, aquí lo dices y se corrige. Nada se levanta hasta que apruebes. Ya aprobado: contenedores con las IPs y hostnames que el artefacto espera, respaldo restaurado, todo lo externo stubeado, y `pepper isolate --live` en verde — **aislado o no se sigue**.
4. **`/pepper-observe <flujo>`** — tú usas la aplicación (un flujo a la vez); PEPPER captura todo con `correlation_id`.
5. **`/pepper-correlate <session_id>`** — el núcleo amarra petición → SQL → log, reduce y empaqueta. Antes de abrir Claude Code aplica un gate local de secretos/PII y deja un manifest externo obligatorio.
6. **`/pepper-discover <session_id>`** → **`/pepper-export <session_id>`** — el entregable: flujos observados, reglas de negocio candidatas, contradicciones y desconocidos, cada uno con su evidencia, publicado en `docs/pepper/discovery/` y `docs/analysis/` (listo para stark).

**Si truena** (va a pasar: cada legacy enseña algo): [`docs/documentacion/TROUBLESHOOTING.md`](docs/documentacion/TROUBLESHOOTING.md) primero; si es la herramienta, abre un issue en el repo de PEPPER con el reporte del error — **sin datos de tu legacy**.

### La regla de oro: la herramienta no se commitea; el producto sí; los datos ajenos nunca

| | Qué es | ¿Va al git del proyecto? |
|---|---|---|
| **Herramienta** | `.claude/`, `pepper/`, `schemas/`, `profiles/`, `templates/`, `docs/documentacion/`, `CLAUDE.md`, `AGENTS.md`, `LICENSE.pepper` (y en el workspace: `examples/`, `tests/`, `scripts/`) | ❌ NO — se ignora; vive en tu disco y se actualiza recopiando |
| **Producto** | `docs/pepper/` (reportes, entorno, discovery) y `docs/analysis/runtime-discovery-*.md` (la entrega a stark) | ✅ SÍ — es el conocimiento del legacy |
| **Datos ajenos** | `legacy/` (artefactos, en el workspace), `evidence/` (capturas), `pepper-out/` (intermedios) | ❌ NUNCA — contienen datos que no son tuyos |

Un perfil nuevo que el agente redacte queda en `profiles/<id>/` — es herramienta: cópialo al repo de PEPPER para que sirva al siguiente legacy.

**Al terminar con PEPPER**, borras la herramienta (`rm -rf .claude pepper schemas profiles docs/documentacion CLAUDE.md AGENTS.md LICENSE.pepper pepper-out evidence`, y quitas sus líneas del `.gitignore`), instalas stark, y su `arqueologo-codigo` encuentra el discovery en `docs/analysis/`. En el repo solo quedó lo que PEPPER produjo.

En el workspace, además: `printf 'examples/\ntests/\nscripts/\n.github/\nrequirements-dev.txt\npyproject.toml\n' >> .gitignore` (lo demás ya viene ignorado).

### Prueba en 5 minutos

```bash
python3 -m pepper demo                                   # legacy de juguete con evidencia ya capturada
cd pepper-out/legacy-demo/package && claude              # o codex — el paquete trae CLAUDE.md y AGENTS.md
python3 -m pepper export pepper-out/legacy-demo/package --manifest pepper-out/legacy-demo/package.evidence-manifest.json --out pepper-out/legacy-demo/export
```

El juguete esconde tres cosas a propósito — una regla de negocio no documentada, una mentira en el manual y una rama que el flujo no ejercita. La clave de respuestas: [`examples/legacy-demo/expected/notes.md`](examples/legacy-demo/expected/notes.md).

### Documentación completa

- 📖 [`docs/documentacion/PRINCIPIOS.md`](docs/documentacion/PRINCIPIOS.md) — filosofía: observar primero, inferir después, comparar al final
- 🚀 [`docs/documentacion/QUICKSTART.md`](docs/documentacion/QUICKSTART.md) — **empieza aquí**: el camino feliz por escalón
- 📋 [`docs/documentacion/REFERENCIA.md`](docs/documentacion/REFERENCIA.md) — qué esperar de cada agente y cómo validar
- 🛟 [`docs/documentacion/TROUBLESHOOTING.md`](docs/documentacion/TROUBLESHOOTING.md) — problemas comunes
- 🧭 [`docs/documentacion/DECISIONES.md`](docs/documentacion/DECISIONES.md) — por qué PEPPER decide lo que decide
- 🏗 [`docs/documentacion/ARQUITECTURA.md`](docs/documentacion/ARQUITECTURA.md) · [`PERFILES.md`](docs/documentacion/PERFILES.md) · [`fases/`](docs/documentacion/fases/) — la especificación técnica
- 🔭 [`docs/documentacion/VISION.md`](docs/documentacion/VISION.md) — el documento original de la idea

---

## Arquitectura del framework

```text
pepper/
├── .claude/
│   ├── commands/                      ← 7 comandos slash /pepper-*
│   │   ├── pepper-init.md             ← Fase 0
│   │   ├── pepper-inspect.md          ← Fase 1
│   │   ├── pepper-rehydrate.md        ← Fase 2
│   │   ├── pepper-observe.md          ← Fase 3
│   │   ├── pepper-correlate.md        ← Fase 4 (sin subagente: la hace el núcleo)
│   │   ├── pepper-discover.md         ← Fase 5
│   │   └── pepper-export.md           ← Fase 6
│   ├── agents/                        ← 4 subagentes
│   │   ├── inspector-legacy.md        ← artefactos → stack-report + borrador de perfil
│   │   ├── rehidratador-legacy.md     ← receta → entorno desechable → environment.json
│   │   ├── observador-runtime.md      ← colectores + ventana → evidence/<session_id>/
│   │   └── descubridor-runtime.md     ← paquete → runtime-discovery.json/md
│   └── skills/                        ← 3 constituciones
│       ├── evidencia-runtime/         ← disciplina de evidencia (todos los agentes)
│       ├── perfil-stack/              ← cómo se redacta un perfil y sus parsers
│       └── discovery-runtime/         ← estructura del discovery; viaja como prompt.md en cada paquete
├── pepper/                            ← el núcleo (Python 3.9+, stdlib)
│   ├── correlate/                     ← parsers declarativos, reducción, correlación
│   ├── package/                       ← paquete controlado
│   ├── export/                        ← validación contra el contrato + publicación
│   ├── detect.py · validate.py        ← herramientas para los agentes
│   └── cli.py                         ← python3 -m pepper …
├── profiles/                          ← el conocimiento de cada stack, como datos
│   └── java-wildfly-postgres/         ← primer perfil (draft): profile.json + parsers/*.json
├── schemas/                           ← 8 contratos JSON Schema: la interfaz entre todo
├── docs/
│   ├── pepper/                        ← OUTPUT en tu workspace: stack-report, environment, discovery/
│   └── documentacion/                 ← este manual + ARQUITECTURA, PERFILES, VISION, fases/
├── templates/NOTAS-LEGACY.md          ← lo que el humano sabe del legacy; init lo copia a legacy/NOTAS.md
├── examples/legacy-demo/              ← legacy de juguete: artefactos, evidencia, clave de respuestas
├── tests/                             ← 120 tests del núcleo
├── scripts/verificar.py               ← auto-verificación del framework (CI)
├── AGENTS.md · CLAUDE.md              ← la misma guía para Codex y para Claude Code
├── legacy/ · evidence/ · pepper-out/  ← en tu workspace: artefactos, capturas, intermedios (ignorados)
└── pyproject.toml
```

---

## Los escalones en una imagen

```text
 ¿Hay perfil validado para el stack?
        │
   sí ──┼── no ──► ¿El sistema corre en algún lado accesible?
        │                    │
        ▼               sí ──┼── no
   ESCALÓN 1                 │        │
   inspect → rehydrate       ▼        ▼
   → observe → correlate  ESCALÓN 2   ESCALÓN 3
   → discover → export    observe con  inspect → BLOCKED con
                          colectores   faltantes + borrador
                          genéricos    de perfil
                          → correlate  (que, validado, vuelve
                          → discover   escalón 1 al siguiente
                          → export     legacy de ese stack)
```

**Ningún legacy recibe "no soportado".** Los tres escalones producen un entregable.

---

## Reglas no negociables

1. **Toda conclusión referencia evidencia íntegra.** IDs que resuelven a un evento o a una línea cruda; un manifest externo obligatorio amarra los bytes fuera del paquete. `pepper export` rechaza lo que no resuelve o fue alterado.
2. **El legacy es solo lectura.** PEPPER descubre; no repara, no moderniza, no hace commit en el repo del legacy.
3. **El entorno rehidratado no alcanza nada externo.** Todos los contenedores, incluido el ingress, viven sólo en redes internas; el host entra por un puerto publicado en loopback. Stub para cada host del artefacto, servidores foráneos re-apuntados y `pepper isolate` antes y después de levantar. La salida de red no es negociable.
4. **Lo determinístico lo hace el núcleo.** Misma evidencia → mismos bytes. La reducción se audita en `reduction.md`; el SQL nunca se deduplica; los errores y las escrituras nunca se descartan.
5. **Lo observado no se mezcla con lo inferido.** `correlation_id` es lo que la fuente emitió; lo inferido va aparte con su base.
6. **Cada fase termina en gate humano.** El plan de rehydrate se aprueba antes de ejecutarse; el flujo lo ejecuta el humano; el discovery se lee completo antes de publicarse.
7. **Nada entra a stark como `confirmada`.** Lo más alto que PEPPER entrega es `inferida` (código **y** runtime); las contradicciones van a `en-duda`; los desconocidos, a preguntas abiertas. Solo una persona con nombre promueve una regla.
8. **BLOCKED es un entregable.** Nunca se inventan insumos faltantes.

---

## Preguntas que casi siempre salen

**¿Tengo que tener el código fuente?** No. Con un WAR, un dump y la configuración, Inspect y Rehydrate funcionan; Discover trabajará contra evidencia y documentación, sin comparación runtime ↔ código.

**¿Y si mi stack no tiene perfil?** Escalón 2 o 3. Un perfil es un JSON con una regex por fuente de logs; el agente lo redacta como borrador durante Inspect y tú lo validas con el primer legacy. Ver [`PERFILES.md`](docs/documentacion/PERFILES.md).

**¿Puedo observar producción en vez de rehidratar?** Sí (escalón 2), con dos límites: sin la observabilidad agresiva que un entorno desechable permite, y con datos reales en la evidencia — que por eso nunca se versiona. Si puedes rehidratar, rehidrata.

**¿Claude Code o Codex?** Los dos. Los comandos son archivos de instrucciones; el paquete de discovery trae `CLAUDE.md` y `AGENTS.md` apuntando al mismo prompt; la salida valida contra el mismo schema. Puedes correr ambos sobre la misma evidencia y contrastar.

**¿Qué le entrega PEPPER a stark exactamente?** `runtime-discovery.md` en `docs/analysis/` (el input de `arqueologo-codigo` en reingeniería) y una lista de qué llevar a la sección 11 de `REGLAS_DE_NEGOCIO.md`, con el mapeo de confianza. Ver [`REFERENCIA.md` §7](docs/documentacion/REFERENCIA.md#7-fase-6-export-y-entrega-a-stark).

---

## Estado del proyecto

| Pieza | Estado |
|---|---|
| Comandos, agentes y skills (`.claude/`) | escritos; ejercitados sobre un legacy real |
| Núcleo — Correlate, Package, Export, detect, validate, isolate, proxy | **implementados y probados** (120 tests) |
| Núcleo — proxy HTTP con `correlation_id` (`pepper proxy`, el ingress) | **implementado y probado**; emite `http.jsonl` por stdout y redacta credenciales |
| Núcleo — colector genérico de contenedores (`pepper collect`) | **implementado y probado**; las fuentes del perfil (archivos dentro de contenedores) las copia el agente |
| Contratos (`schemas/`) | 8, definidos y validados |
| Perfil `java-wildfly-postgres` | `draft`: parsers probados; receta de rehydrate sin ejecutar |
| **Pipeline completo contra un legacy real** | **ejecutado (2026-09-02)**: init → inspect → rehydrate (AISLADO --live) → observe (proxy + collect) → correlate → discover → export publicado |
| Fixture `examples/legacy-demo` | listo; evidencia **sintética** marcada como tal; entorno Docker de referencia sin verificar |

Roadmap en [`VISION.md` §35](docs/documentacion/VISION.md).

---

## Stack y requisitos

- **Claude Code** (o Codex: ver [`AGENTS.md`](AGENTS.md))
- **Python 3.9+** — captura y correlación usan stdlib; `jsonschema` es obligatorio para Export (`pip install -r requirements-dev.txt`)
- **Docker** con Compose v2 — solo para Rehydrate (escalón 1)
- Acceso a modelo Claude Opus (recomendado para los 4 subagentes)

El repo se auto-verifica: `python3 scripts/verificar.py` valida frontmatters, fences, links, nombres citados, scripts y contratos; `python3 -m unittest discover -s tests` corre la suite del núcleo. El CI ejecuta ambos en cada push.

---

## Licencia y autoría

PEPPER — herramienta creada por [iamgabstark_](https://github.com/GabrielaStark). Licencia **MIT**.

Complemento independiente de **stark**, de la misma autora.
