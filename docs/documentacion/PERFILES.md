# Perfiles

Un **perfil** es todo el conocimiento específico de un stack tecnológico, empaquetado como datos. Es la razón por la que PEPPER puede aspirar a cualquier legacy sin que el núcleo crezca por tecnología.

## Qué aporta un perfil

| Aporte | Archivo | Ejemplo (java-springboot-jsf-postgres) |
|---|---|---|
| **Detección** | `profile.json › detection` | hay un `.war`, `pom.xml` con spring-boot, `application*.yml` con jdbc:postgresql |
| **Extractores del mapa** | `extractors.json` | qué paquetes son controladores y jobs, qué archivos son pantallas y con qué regex leerlas, qué bundle resuelve las etiquetas, qué tablas son catálogos y qué columnas son estados |
| **Receta de rehydrate** | `profile.json › rehydrate` + `compose.template.yml` | WildFly 21 + PostgreSQL 10, restaurar el respaldo, stub para cada host externo, ingress = `pepper proxy` |
| **Colectores** | `profile.json › collectors` | `docker logs` del app y de la base con `log_statement=all` |
| **Parsers** | `parsers/*.json` ([contrato](../../schemas/parser.schema.json)) | regex de la línea de WildFly, de PostgreSQL con `DETAIL`, continuaciones, ruido, afinidad |
| **Lectura de formularios** | `profile.json › http` | `javax.faces.source` nombra el botón; `javax.faces.*` y `ViewState` son ruido; el nombre corto del campo es el último segmento |
| **Validaciones** | `profile.json › validation` | "WildFly desplegó el WAR", "la raíz responde", "el stub no recibió peticiones al arrancar" |

Formato: carpeta en `profiles/<id>/` con `profile.json` (contrato: [`schemas/profile.schema.json`](../../schemas/profile.schema.json)) más sus recursos.

## Los extractores: mecanismos del núcleo, patrones del perfil

`pepper map` entiende seis mecanismos (`jvm_route_annotations`, `jvm_class_inventory`, `view_templates`, `pg_dump_custom`, `config_hosts`, `archive_url_scan`, ver [ARQUITECTURA.md](ARQUITECTURA.md)). Un perfil declara cuáles aplican y con qué patrones. Un stack con vistas JSP en vez de XHTML cambia `member_patterns` y las regex de `view_templates`; un stack sin JVM no declara los `jvm_*`. Si un stack necesita un mecanismo nuevo (p. ej. leer un respaldo de otro motor), el mecanismo se agrega al núcleo de forma genérica y el perfil lo parametriza.

Lo que un mecanismo no puede leer se declara: el mapa sale `complete: false` con el hueco nombrado.

## Ciclo de vida: los perfiles se fabrican con la herramienta

```text
llega un legacy con stack desconocido
        ↓
INSPECT: el agente identifica el stack a partir de los artefactos
        ↓
el agente redacta un BORRADOR de perfil (status: draft)
  · señales de detección de ESTOS artefactos
  · extractores del mapa
  · receta de rehydrate, colectores, parsers, validaciones
        ↓
un humano lo revisa y lo prueba con ese legacy
        ↓
si funciona → status: validated → entra a la librería
        ↓
el siguiente legacy con ese stack cae en el escalón 1
```

## Reglas

1. **Un perfil nunca modifica el núcleo.** Si un stack nuevo "necesita" tocar el correlacionador o el mapa, el diseño del núcleo está mal y se corrige ahí, de forma genérica.
2. **Un perfil `draft` nunca corre sin supervisión.** Solo los `validated` habilitan el escalón 1 automatizado.
3. **Fidelidad primero**: la receta reproduce las versiones originales del stack, no las moderniza.
4. **Sin perfil no hay bloqueo total**: aplican los colectores genéricos (escalón 2) o la inspección con reporte de faltantes (escalón 3).

## Perfiles

| id | estado | nota |
|---|---|---|
| `java-springboot-jsf-postgres` | draft | el que corrió el primer legacy real de punta a punta; trae extractores completos |
| `java-wildfly-postgres` | draft | el primero; parsers de WildFly y PostgreSQL, sin extractores |
