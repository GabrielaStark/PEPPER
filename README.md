# PEPPER

> Observar primero, inferir después, comparar al final.
>
> Por [@iamgabstark_](https://iamgabstark.com/) · complemento de [stark](https://github.com/GabrielaStark/stark) · [Principios](docs/documentacion/PRINCIPIOS.md)

**PEPPER es descubrimiento dinámico de sistemas legacy: toma lo que quede de un sistema, intenta volverlo a poner vivo en contenedores, observa una ejecución real, correlaciona la evidencia y la convierte en conocimiento estructurado — flujos observados, reglas candidatas, contradicciones y desconocidos, cada uno con su evidencia.**

Donde stark lee el manual del coche, PEPPER lo enciende, lo maneja y te dice qué hace de verdad. Sirve para cualquier legacy: lo que la herramienta sabe de cada tecnología entra como datos (perfiles), nunca como código del núcleo.

Clonas, corres `/pepper-init`, y la herramienta te lleva fase por fase: [empieza aquí →](docs/documentacion/QUICKSTART.md)

---

## Las piezas: qué es cada cosa

- **stark** — el framework Spec-Driven de la autora: convierte Claude Code en un equipo que trabaja con specs, con gates humanos y sellos. Hace **onboarding estático** de un legacy: código, documentación, configuración.
- **PEPPER** — esta herramienta: **descubrimiento dinámico**. Agrega la fuente que el análisis estático no puede ver: el comportamiento real del sistema mientras corre. Es independiente de stark, y lo complementa: su salida es el análisis arqueológico que stark consume en reingeniería.
- **El núcleo** (`pepper/`) — Python, sin dependencias: lo mecánico y repetible (parsear, reducir, correlacionar, empaquetar, validar). Las manos.
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

Un workspace por legacy:

```bash
git clone https://github.com/GabrielaStark/pepper.git sistema-nominas
cd sistema-nominas && rm -rf .git && git init
pip install -r requirements-dev.txt
```

### La regla de oro: la herramienta no se commitea; el producto sí; los datos ajenos nunca

| | Qué es | ¿Va al git de tu workspace? |
|---|---|---|
| **Herramienta** | `.claude/`, `pepper/`, `schemas/`, `profiles/` existentes, `docs/documentacion/`, `examples/`, `tests/`, `scripts/` | ❌ NO — se ignora; vive en tu disco y se actualiza recopiando |
| **Producto** | `docs/pepper/` (reportes, entorno, discovery) y los perfiles nuevos que redactes | ✅ SÍ — es el conocimiento del legacy |
| **Datos ajenos** | `legacy/` (artefactos), `evidence/` (capturas), `pepper-out/` (intermedios) | ❌ NUNCA — contienen código y datos que no son tuyos |

```bash
printf '.claude/\npepper/\nschemas/\ndocs/documentacion/\nexamples/\ntests/\nscripts/\n.github/\nrequirements-dev.txt\npyproject.toml\n' >> .gitignore
```

(`legacy/`, `evidence/` y `pepper-out/` ya vienen ignorados.)

### Ejecutar las fases

```text
0  /pepper-init                    ← arranca: herramientas, carpetas, escalón de soporte
1  /pepper-inspect                 ← artefactos → stack con evidencia, faltantes, perfil
2  /pepper-rehydrate               ← receta → contenedores fieles → environment.json
3  /pepper-observe <flujo>         ← tú ejecutas el flujo; PEPPER captura
4  /pepper-correlate <session_id>  ← núcleo: normaliza, reduce, correlaciona, empaqueta
5  /pepper-discover <session_id>   ← agente: secuencia, reglas candidatas, contradicciones, desconocidos
6  /pepper-export <session_id>     ← valida contra el contrato, publica, entrega a stark
```

Cada fase termina en un **gate humano** ✋. El flujo observado lo ejecutas tú; el plan de reconstrucción lo apruebas antes de que se levante un contenedor; el discovery lo lees completo antes de publicarlo.

### Prueba en 5 minutos

```bash
python3 -m pepper demo                                   # legacy de juguete con evidencia ya capturada
cd pepper-out/legacy-demo/package && claude              # o codex — el paquete trae CLAUDE.md y AGENTS.md
python3 -m pepper export pepper-out/legacy-demo/package --out pepper-out/legacy-demo/export
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
├── schemas/                           ← 7 contratos JSON Schema: la interfaz entre todo
├── docs/
│   ├── pepper/                        ← OUTPUT en tu workspace: stack-report, environment, discovery/
│   └── documentacion/                 ← este manual + ARQUITECTURA, PERFILES, VISION, fases/
├── examples/legacy-demo/              ← legacy de juguete: artefactos, evidencia, clave de respuestas
├── tests/                             ← 29 tests del núcleo
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

1. **Toda conclusión referencia evidencia.** IDs que resuelven a un evento o a una línea cruda. `pepper export` rechaza lo que no resuelve — no publica, no corrige.
2. **El legacy es solo lectura.** PEPPER descubre; no repara, no moderniza, no hace commit en el repo del legacy.
3. **Lo determinístico lo hace el núcleo.** Misma evidencia → mismos bytes. La reducción se audita en `reduction.md`; el SQL nunca se deduplica; los errores y las escrituras nunca se descartan.
4. **Lo observado no se mezcla con lo inferido.** `correlation_id` es lo que la fuente emitió; lo inferido va aparte con su base.
5. **Cada fase termina en gate humano.** El plan de rehydrate se aprueba antes de ejecutarse; el flujo lo ejecuta el humano; el discovery se lee completo antes de publicarse.
6. **Nada entra a stark como `confirmada`.** Lo más alto que PEPPER entrega es `inferida` (código **y** runtime); las contradicciones van a `en-duda`; los desconocidos, a preguntas abiertas. Solo una persona con nombre promueve una regla.
7. **BLOCKED es un entregable.** Nunca se inventan insumos faltantes.

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
| Comandos, agentes y skills (`.claude/`) | escritos; sin probar aún sobre un legacy real |
| Núcleo — Correlate, Package, Export, detect, validate | **implementados y probados** (29 tests) |
| Núcleo — proxy HTTP con `correlation_id`, colector genérico de contenedores | pendientes: Observe funciona con los logs que el perfil declare |
| Contratos (`schemas/`) | 7, definidos y validados |
| Perfil `java-wildfly-postgres` | `draft`: parsers probados; receta de rehydrate sin ejecutar |
| Fixture `examples/legacy-demo` | listo; evidencia **sintética** marcada como tal; entorno Docker de referencia sin verificar |

Roadmap en [`VISION.md` §35](docs/documentacion/VISION.md).

---

## Stack y requisitos

- **Claude Code** (o Codex: ver [`AGENTS.md`](AGENTS.md))
- **Python 3.9+** — el núcleo corre sin dependencias; `jsonschema` habilita la validación de contratos (`pip install -r requirements-dev.txt`)
- **Docker** con Compose v2 — solo para Rehydrate (escalón 1)
- Acceso a modelo Claude Opus (recomendado para los 4 subagentes)

El repo se auto-verifica: `python3 scripts/verificar.py` valida frontmatters, fences, links, nombres citados, scripts y contratos; `python3 -m unittest discover -s tests` corre la suite del núcleo. El CI ejecuta ambos en cada push.

---

## Licencia y autoría

PEPPER — herramienta creada por [iamgabstark_](https://github.com/GabrielaStark). Licencia **MIT**.

Complemento independiente de **stark**, de la misma autora. Nombre: Plataforma de Evidencia y Procesamiento para Patrones de Ejecución y Reingeniería — y sí, Pepper organiza la realidad antes de que Stark actúe.
