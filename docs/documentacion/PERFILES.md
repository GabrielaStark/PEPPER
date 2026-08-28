# Perfiles

Un **perfil** es todo el conocimiento específico de un stack tecnológico, empaquetado como datos. Es la razón por la que PEPPER puede aspirar a cualquier legacy sin que el núcleo crezca por tecnología.

## Qué aporta un perfil

| Aporte | Ejemplo (java-wildfly-postgres) |
|---|---|
| **Detección** | señales: existe `pom.xml`, hay un `.war`, `standalone.xml` menciona datasources |
| **Receta de rehydrate** | imágenes (WildFly 10 + JRE 8, PostgreSQL 9), orden de arranque, cómo desplegar el WAR, cómo restaurar el dump, plantilla de datasource |
| **Colectores** | dónde está `server.log`, cómo activar log verboso, `log_statement=all` en el Postgres del compose |
| **Parsers** | un JSON por fuente ([contrato](../../schemas/parser.schema.json)): la expresión regular de la línea, cómo mapear sus grupos a campos del evento, continuaciones, fusiones y ruido — datos, no código |
| **Validaciones** | "hay un deployment `OK` en WildFly", "la BD acepta conexiones", "el endpoint raíz responde" |

Formato: carpeta en `profiles/<id>/` con un `profile.json` (contrato: [`schemas/profile.schema.json`](../../schemas/profile.schema.json)) más sus recursos (plantillas de compose, configs, `parsers/*.json`).

Que los parsers sean declarativos es lo que hace realista "cualquier legacy": cubrir un formato de log nuevo es escribir una expresión regular con grupos nombrados, no programar — y eso el agente lo puede redactar durante Inspect.

## Ciclo de vida: los perfiles se fabrican con la herramienta

```text
llega un legacy con stack desconocido
        ↓
INSPECT: el agente identifica el stack a partir de los artefactos
        ↓
el agente redacta un BORRADOR de perfil (status: draft)
  · receta de rehydrate propuesta
  · colectores y validaciones propuestos
        ↓
un humano lo revisa, lo corrige y lo prueba con ese legacy
        ↓
si funciona → status: validated → entra a la librería
        ↓
el siguiente legacy con ese stack cae en el escalón 1
```

Cada legacy raro que llega **alimenta la librería** en lugar de bloquear la herramienta.

## Reglas

1. **Un perfil nunca modifica el núcleo.** Si un stack nuevo "necesita" tocar el correlacionador, el diseño del núcleo está mal y se corrige ahí, de forma genérica.
2. **Un perfil `draft` nunca corre sin supervisión.** Solo los `validated` habilitan el escalón 1 automatizado.
3. **Fidelidad primero**: la receta reproduce las versiones originales del stack, no las moderniza.
4. **Sin perfil no hay bloqueo total**: aplican los colectores genéricos (escalón 2) o la inspección con reporte de faltantes (escalón 3).

## Perfiles planeados

```text
java-wildfly-postgres    ← primero (prueba la tubería completa)
spring-boot / tomcat / angular-nginx / node / oracle / mongodb / ...
```

El primer perfil existe para demostrar el pipeline de punta a punta, no para acotar la herramienta: es secuencia, no límite.
