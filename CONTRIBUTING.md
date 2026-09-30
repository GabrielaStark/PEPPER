# Contribuir a PEPPER

PEPPER crece por perfiles: cada legacy nuevo que alguien entiende con la herramienta puede dejar un perfil que sirva al siguiente. Esta página dice cómo se hace sin que salga nada del sistema que analizaste, y cómo se prueba sin un legacy real.

## Antes de nada

- Lee [`docs/documentacion/PRINCIPIOS.md`](docs/documentacion/PRINCIPIOS.md), [`ARQUITECTURA.md`](docs/documentacion/ARQUITECTURA.md) y [`PERFILES.md`](docs/documentacion/PERFILES.md). Las decisiones tomadas están en [`DECISIONES.md`](docs/documentacion/DECISIONES.md): si tu cambio contradice una, se discute la decisión, no se rodea.
- Todo va en **español**: código (identificadores en inglés cuando son técnicos, en español cuando son vocabulario del contrato: `efecto`, `comprueba`, `falla`), comentarios que explican el porqué, mensajes del CLI, documentación, pruebas. Es una decisión, no un descuido: los usuarios de PEPPER analizan sistemas de gobierno y empresa de habla hispana y el documento que produce es para ellos.
- Python 3.9 es el mínimo y CI lo prueba: nada de `match`, `X | Y` fuera de anotaciones con `from __future__ import annotations`, `removeprefix`.
- El núcleo no depende de nada fuera de la biblioteca estándar; `jsonschema` y `pyyaml` solo para validar, `playwright` solo para explorar.

## Correr las pruebas

```bash
pip install -r requirements-dev.txt && python3 -m playwright install chromium
python3 scripts/verificar.py                 # frontmatters, links, contratos, comandos citados vs CLI, tabla de perfiles
python3 -m unittest discover -s tests        # la suite (sin Docker; la prueba hermética con Chromium se salta si no está)
python3 scripts/e2e_docker.py                # con Docker: levanta un entorno real y lo verifica
```

CI corre los tres en cada cambio, y en CI nada se salta: sin Chromium o sin Docker, falla.

## Contribuir un perfil

Un perfil es todo el conocimiento de un sistema concreto como datos (`profiles/<id>/`). Nace `draft`, redactado casi siempre por `inspector-legacy` mientras analizabas un legacy real, y ahí está el problema: **lo redactaste sobre datos confidenciales**. Antes de proponerlo:

1. **Límpialo.** Un perfil describe un *stack*, no una instalación. Quita del `profile.json`, de `extractors.json`, de las plantillas y del `README.md`: el nombre del sistema y de la institución, hosts e IPs reales, nombres de paquete raíz que identifiquen al cliente, rutas de su servidor, cifras de su base, nombres de personas. Lo que quede debe servirle a otro legacy del mismo stack. Si un patrón solo tiene sentido para esa instalación, no va.
2. **Dale fixtures.** `profiles/<id>/fixtures/` con líneas de log sintéticas fieles a cada parser (`logs/<source>.log`), configuración sintética que `rehydrate` debe entender (`config/`), y un respaldo sintético chico del formato declarado (generado por `fixtures/synthesize.py`, no versionado como binario), más `expected.json` con lo que la prueba parametrizada debe encontrar. `tests/test_perfiles.py` corre eso para cada carpeta de `profiles/` en CI: es lo que te permite demostrar un perfil sin un legacy real.
3. **Valida.** `python3 -m pepper validate profiles/<id>/profile.json profiles/<id>/extractors.json profiles/<id>/parsers/*.json` y la suite completa.
4. **Documenta.** El `README.md` del perfil dice qué stack cubre, qué señales lo detectan, qué le falta para `validated`, y qué corrida real lo probó (sin nombrar el sistema). Agrega la fila a las tablas de `docs/documentacion/PERFILES.md` y `profiles/README.md` (verificar.py exige que coincidan con el disco).
5. **`validated` lo marca una persona** que vio el ciclo completo contra un legacy real y confirmó el documento. Un PR trae `draft`.

Si tu stack necesita algo que el núcleo no tiene (un lector para un formato nuevo, un mecanismo de datasource, un dialecto de SQL), el cambio va al núcleo en `pepper/inspect/readers/` o donde toque, con pruebas, y PERFILES.md lo declara. No pongas conocimiento de un sistema en el núcleo: ningún nombre, ninguna ruta de un cliente, ningún dominio de negocio.

## Contribuir al núcleo

- Cada bypass del aislamiento o de la frontera de datos que encuentres se convierte en una prueba de regresión antes de cerrarse. Si es una fuga, repórtala en privado primero ([`SECURITY.md`](SECURITY.md)).
- Un comando o una bandera nueva se documenta en `docs/documentacion/REFERENCIA.md` en el mismo cambio: verificar.py compara la documentación con el CLI.
- Una decisión de diseño nueva es una entrada en `DECISIONES.md` con el formato Decisión · Por qué · Consecuencia aceptada.
- El guardia de datos (`scripts/guardia_datos.py`, `.claude/settings.json`) no se edita desde un agente: lanza Claude Code con `PEPPER_GUARDIA_DEV=1` si desarrollas PEPPER con él.

## Qué no se acepta

- Datos de un legacy real (nombres, hosts, cifras, capturas) en ningún archivo del repositorio.
- Un perfil sin fixtures, o un lector nuevo sin pruebas.
- "Modernizar de paso" una versión en una receta.
- Un fallback que adivine (el perfil de configuración, el respaldo, la versión del servidor): ante la ambigüedad, BLOCKED.
