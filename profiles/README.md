# Perfiles

Todo el conocimiento específico de un stack vive aquí, como datos. El núcleo consume perfiles vía [`schemas/profile.schema.json`](../schemas/profile.schema.json) y nunca conoce tecnologías directamente. Concepto y ciclo de vida: [PERFILES.md](../docs/documentacion/PERFILES.md); cómo redactar uno: skill [`perfil-stack`](../.claude/skills/perfil-stack/SKILL.md).

## Estructura de un perfil

```text
profiles/<id>/
├── profile.json          contrato (detección, receta de rehydrate, colectores)
├── extractors.json       cómo `pepper map` mina este stack (contrato: schemas/extractors.schema.json)
├── compose.template.yml  plantilla de orquestación referida por la receta
├── parsers/              normalizadores de cada fuente a event.schema.json
├── fixtures/             lo que demuestra el perfil sin un legacy real (ver abajo)
└── README.md             notas del perfil: por qué la receta es como es, qué falta para `validated`
```

## Fixtures: demostrar un perfil sin un legacy real

Un contribuidor sin acceso a un sistema de ese stack puede demostrar algo más que "valida contra el schema": `tests/test_perfiles.py` recorre cada carpeta de `profiles/` y, si trae `fixtures/`, corre sobre ellos los mismos lectores que correrían contra el legacy. Contrato:

```text
profiles/<id>/fixtures/
├── expected.json           qué debe salir (obligatorio si hay fixtures/)
├── logs/<source>.log       líneas de la fuente <source> (la de collectors[].source), reales anonimizadas o
│                           sintéticas fieles al formato que el parser declara — con el prefijo de Docker si
│                           el colector es el stdout de un contenedor
├── config/artifact/…       archivos de configuración tal como viven DENTRO del desplegable (la prueba los empaca en un zip)
├── config/junto/…          archivos que la persona deja junto al desplegable (p. ej. configuration/standalone.xml)
├── dump/<respaldo>         un respaldo sintético chico en texto (sql_text), versionado
└── synthesize.py           genera lo que no se versiona (un pg_dump custom es binario) en el directorio que
                            recibe como argumento; puede importar `write_custom_dump` de tests/test_systemmap.py
```

`expected.json`:

```json
{
  "logs": {"<source>": {"events": 6, "unparsed": 1, "sql_tables": ["user"]}},
  "datasource": {"name": "prod", "engine": "postgresql", "host": "10.42.7.2", "port": 5432, "db": "nominas_prod", "username": "nominas"},
  "dump": {"file": "respaldo.dump", "format": "pg_dump_custom", "dbname": "base_origen", "server_version": "10.6",
           "tables": ["ctroles", "cita"], "catalogs": ["ctroles"], "not_catalogs": ["trabajador"]}
}
```

- `logs`: por cada archivo, las líneas sin parsear deben ser exactamente `unparsed` (0 si se omite) y, si se declaran, los eventos `events` y las tablas `sql_tables` que el SQL debe reconocer. Un fixture sin un solo evento no demuestra nada.
- `datasource`: `discover_datasource` + `datasource_facts` sobre `config/` deben dar exactamente eso; la contraseña (ficticia) debe existir y **nunca** se escribe en `expected.json`. `null` cuando el datasource del stack no vive en texto (Groovy compilado) y no hay `config/`.
- `dump`: `read_dump_facts` y el mecanismo del mapa de ese formato (`pg_dump_custom` o `sql_dump`, con los patrones del `extractors.json` del perfil) deben dar la base de origen, la versión, esas tablas, y volcar como catálogo las de `catalogs` pero nunca las de `not_catalogs` (personas, tablas grandes).

Ninguna fila, host ni credencial de los fixtures puede ser real: son sintéticos y lo dicen en `expected.json › notes`.

## Reglas

- `status: "draft"` = redactado (a menudo por el agente al inspeccionar), sin que una persona lo haya promovido; **corre igual**, y `environment.json` / `funcional.md` lo declaran.
- `status: "validated"` = una persona lo vio correr de punta a punta contra un legacy real y lo marcó.
- Un perfil nunca requiere cambios en el núcleo. Si parece necesitarlos, el defecto está en el núcleo.
- Fidelidad primero: las recetas reproducen versiones originales, no modernizan.

## Perfiles

| id | estado | nota |
|---|---|---|
| [groovy-grails1-tomcat-mysql](groovy-grails1-tomcat-mysql/) | validated | Grails 1.x, Tomcat, MySQL; el ciclo completo contra un legacy real y una persona confirmó el documento |
| [java-springboot-jsf-postgres](java-springboot-jsf-postgres/) | draft | corrió el ciclo entero contra un legacy real: extractores del mapa, receta de rehydrate, lectura de formularios, parsers |
| [java-springboot-fatjar-postgres](java-springboot-fatjar-postgres/) | draft | varios fat jars de Spring Boot y un front estático contra una base; artefactos sintéticos y Docker real (es el perfil del E2E de CI) |
| [java-wildfly-postgres](java-wildfly-postgres/) | draft | el primero; parsers de WildFly y PostgreSQL; extractores heredados del perfil JSF sin corrida real (no enumera rutas JAX-RS) |
