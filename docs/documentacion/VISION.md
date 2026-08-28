# PEPPER

> **Plataforma de Evidencia y Procesamiento para Patrones de Ejecución y Reingeniería**

**PEPPER** es un complemento independiente para STARK orientado al descubrimiento dinámico de sistemas legacy.

Su propósito es tomar los artefactos disponibles de un sistema, reconstruir un entorno ejecutable cuando sea necesario, observar ejecuciones reales, capturar evidencia técnica, correlacionarla y convertirla en conocimiento estructurado que pueda ser consumido por STARK.

---

# 1. Idea principal

El problema de los sistemas legacy no es solamente leer código.

Frecuentemente hay que:

- entender qué hace realmente el sistema;
- identificar flujos que nadie documentó;
- encontrar reglas de negocio escondidas;
- descubrir dependencias;
- reconstruir ambientes difíciles de levantar;
- mantener comportamiento sin romperlo;
- y realizar reingenierías con información incompleta.

STARK ya puede realizar un **onboarding estático** utilizando:

```text
código
documentación
configuración
estructura
dependencias
reglas de negocio
```

PEPPER agrega otra fuente:

> **el comportamiento real del sistema mientras está ejecutándose.**

La idea es contrastar:

```text
lo que dice el código
        +
lo que dice la documentación
        +
lo que realmente hace el sistema
```

---

# 2. Principio

> **Observar primero, inferir después, comparar al final.**

PEPPER no asume que una sola fuente contiene toda la verdad.

En un legacy pueden coexistir:

```text
documentación
código
base de datos
configuración
runtime
conocimiento del desarrollador
conocimiento del usuario
```

PEPPER agrega el **runtime** como evidencia explícita.

---

# 3. Flujo general

```text
Código / WAR / JAR / dist / backup
                ↓
         0. REHYDRATE
   levanta legacy en contenedores
                ↓
           1. OBSERVE
      captura ejecución real
                ↓
          2. CORRELATE
     estructura la evidencia
                ↓
       PAQUETE CONTROLADO
                ↓
      Claude Code / Codex
                ↓
          3. DISCOVER
 flujos + reglas candidatas + evidencia
                ↓
           4. EXPORT
                ↓
              STARK
```

---

# 4. Qué es PEPPER

PEPPER es un **motor de descubrimiento dinámico para sistemas legacy**.

Puede utilizarse como complemento de STARK, pero debe poder existir de forma independiente.

Sus responsabilidades principales son:

```text
reconstruir
observar
capturar
normalizar
correlacionar
estructurar
descubrir
exportar
```

PEPPER no está pensado para modificar el legacy.

Su función es obtener conocimiento sobre él.

---

# 5. Arquitectura conceptual

```text
┌──────────────────────────────────────────┐
│          ARTEFACTOS DEL LEGACY           │
│                                          │
│ código / WAR / JAR / dist / backup       │
│ configuración / scripts / documentación  │
└────────────────────┬─────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────┐
│             0. REHYDRATE                 │
│                                          │
│ reconstruye entorno ejecutable           │
│ identifica runtime                       │
│ levanta contenedores                     │
│ restaura base de datos                   │
│ valida dependencias                      │
└────────────────────┬─────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────┐
│              SISTEMA LEGACY              │
│                                          │
│ frontend / backend / WildFly / BD        │
│ servicios / contenedores / integraciones │
└────────────────────┬─────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────┐
│              1. OBSERVE                  │
│                                          │
│ logs / HTTP / BD / excepciones / eventos │
└────────────────────┬─────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────┐
│             2. CORRELATE                 │
│                                          │
│ normalización + correlación               │
│ temporal / requests / sesiones / SQL     │
└────────────────────┬─────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────┐
│          PAQUETE CONTROLADO              │
│                                          │
│ evidencia estructurada y reducida        │
└────────────────────┬─────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────┐
│        CLAUDE CODE / CODEX               │
│                                          │
│ investiga evidencia + código             │
│ correlaciona runtime ↔ implementación    │
└────────────────────┬─────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────┐
│              3. DISCOVER                 │
│                                          │
│ flujo / reglas candidatas / evidencia    │
│ dependencias / contradicciones / unknowns│
└────────────────────┬─────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────┐
│               4. EXPORT                  │
│                                          │
│ runtime-discovery.md/json                │
│ flows / rules / contradictions           │
└────────────────────┬─────────────────────┘
                     │
                     ▼
                   STARK
```

---

# 6. Fase 0 — REHYDRATE

## 6.1 Objetivo

Antes de observar un legacy, PEPPER puede intentar reconstruir un entorno ejecutable local a partir de los artefactos disponibles.

La instrucción conceptual es:

> **Dame lo que tengas del legacy y voy a intentar volverlo ejecutable en un entorno controlado.**

PEPPER no debe asumir que el sistema ya está levantado.

---

## 6.2 Insumos posibles

### Código fuente

```text
src/
pom.xml
build.gradle
package.json
angular.json
configuration/
```

Con esto PEPPER puede identificar:

- lenguaje;
- framework;
- sistema de build;
- versión del runtime;
- dependencias;
- artefactos a generar;
- configuración requerida.

### Artefactos compilados

```text
application.war
service.jar
dist/
ear/
binary/
```

No es obligatorio contar con código fuente.

Ejemplos:

```text
WAR + WildFly + properties + base de datos
```

o:

```text
dist Angular + JAR backend + dump PostgreSQL
```

pueden ser suficientes.

### Base de datos

PEPPER puede recibir:

```text
.dump
.sql
.backup
.dmp
scripts de creación
scripts de migración
datos de prueba
```

Y utilizar eso para:

- identificar el motor;
- levantar un contenedor;
- restaurar datos;
- validar conectividad;
- preparar el datasource.

### Configuración

Ejemplos:

```text
application.properties
application.yml
standalone.xml
.env
config.json
httpd.conf
nginx.conf
```

La configuración puede revelar:

- puertos;
- datasources;
- endpoints;
- servicios externos;
- certificados;
- variables de entorno;
- rutas y dependencias.

### Otros artefactos

```text
Dockerfile
docker-compose.yml
scripts de arranque
certificados
documentación
diagramas
librerías
imágenes
información del servidor original
notas técnicas
```

Todo se trata como evidencia para reconstruir el runtime.

---

# 7. Flujo de Rehydrate

```text
ARTEFACTOS DEL LEGACY
        ↓
INSPECCIÓN
        ↓
IDENTIFICACIÓN DEL STACK
        ↓
DETECCIÓN DE DEPENDENCIAS
        ↓
PLAN DE RECONSTRUCCIÓN
        ↓
CONTENEDORES / RUNTIME
        ↓
RESTAURACIÓN DE DATOS
        ↓
CONFIGURACIÓN
        ↓
ARRANQUE
        ↓
VALIDACIÓN
        ↓
LEGACY EJECUTABLE
```

---

# 8. Ejemplos de Rehydrate

## Caso A — WAR + PostgreSQL

Entrada:

```text
legacy.war
application.properties
database.dump
```

PEPPER identifica:

```text
Java
WildFly
PostgreSQL
```

Y prepara:

```text
WildFly container
PostgreSQL container
datasource
deploy del WAR
restauración de BD
```

Resultado:

```text
legacy ejecutándose localmente
```

## Caso B — Angular + JAR

Entrada:

```text
frontend/dist/
backend.jar
database.sql
```

PEPPER puede reconstruir:

```text
Nginx
Java runtime
PostgreSQL
```

Resultado:

```text
frontend + backend + base de datos
```

en un entorno reproducible.

## Caso C — Código fuente completo

Entrada:

```text
frontend/
backend/
database/
```

PEPPER analiza:

```text
pom.xml
package.json
angular.json
properties
migrations
Dockerfile
```

Después:

```text
detectar stack
→ construir frontend
→ construir backend
→ preparar BD
→ crear contenedores
→ levantar entorno
```

## Caso D — Artefactos incompletos

Entrada:

```text
application.war
```

Pero se detecta que faltan:

```text
datasource
servicio externo
certificado
variables de ambiente
```

PEPPER no inventa esos elementos.

Debe generar algo como:

```text
ESTADO: BLOCKED

Detectado:
- aplicación Java WAR
- servidor Java EE requerido
- datasource LegacyDS
- endpoint externo

Faltante:
- configuración de LegacyDS
- respaldo o esquema de BD
- configuración del servicio externo
- certificado

Siguiente evidencia recomendada:
- standalone.xml
- backup de BD
- properties del ambiente
```

---

# 9. Rehydrate no promete levantar cualquier sistema

PEPPER no debe afirmar:

> “Dame cualquier legacy y automáticamente lo levanto.”

La afirmación correcta es:

> **PEPPER intenta reconstruir un entorno ejecutable a partir de los artefactos disponibles y determina qué información adicional hace falta cuando la reproducción no es posible.**

---

# 10. Fidelidad antes que modernización

Rehydrate busca **reproducir**, no modernizar.

Si el legacy necesita:

```text
Java 8
WildFly 10
PostgreSQL 9
Node antiguo
```

PEPPER no debe actualizarlo automáticamente.

Principio:

> **primero reproducir el comportamiento original; modernizar es otro problema.**

---

# 11. Estados de Rehydrate

```text
READY
PARTIAL
BLOCKED
FAILED
```

### READY

El legacy está ejecutándose y puede pasar a Observe.

### PARTIAL

Parte del sistema funciona, pero existen dependencias faltantes.

### BLOCKED

No existe suficiente evidencia para reconstruir el sistema.

### FAILED

La reconstrucción era viable con los insumos existentes, pero ocurrió un fallo técnico.

---

# 12. Validación de Rehydrate

Un contenedor en estado `running` no significa que la aplicación funcione.

PEPPER debe validar, según el caso:

```text
contenedores iniciados
aplicación desplegada
base restaurada
datasource operativo
endpoint accesible
frontend cargando
backend respondiendo
ausencia de errores críticos de arranque
```

---

# 13. Salida de Rehydrate

Ejemplo:

```text
pepper/
└── rehydrate/
    ├── environment.json
    ├── dependencies.json
    ├── docker-compose.yml
    ├── configuration/
    ├── missing-evidence.md
    └── validation.md
```

Ejemplo de `environment.json`:

```json
{
  "application": {
    "type": "war",
    "artifact": "legacy.war"
  },
  "runtime": {
    "java": "8",
    "server": "wildfly",
    "server_version": "10"
  },
  "database": {
    "engine": "postgresql",
    "restored": true
  },
  "status": "READY"
}
```

---

# 14. Fase 1 — OBSERVE

Una vez que el legacy está ejecutándose, PEPPER observa una ejecución funcional real.

La entrada principal es una acción de usuario.

Ejemplo:

```text
FLUJO: Registrar solicitud
INICIO: 13:20:00
FIN:    13:22:14
```

Durante esa ventana PEPPER captura evidencia.

---

# 15. Fuentes de evidencia

## Aplicación

```text
WildFly logs
Spring logs
Java exceptions
application logs
audit logs
```

## Contenedores

```text
Docker logs
Podman logs
stdout
stderr
container events
```

## HTTP

```text
endpoint
method
status code
duration
request metadata
response metadata
```

## Base de datos

```text
SQL
transacciones
procedimientos almacenados
errores
tablas afectadas
```

## Runtime

Cuando exista:

```text
traces
metrics
JVM events
service calls
```

---

# 16. Fase 2 — CORRELATE

El agente no debería buscar líneas relevantes directamente dentro de gigabytes de logs.

Primero debe existir una capa determinística de reducción y correlación.

PEPPER puede correlacionar utilizando:

```text
timestamp
request ID
trace ID
session ID
user ID
endpoint
thread
container
service
exception chain
transaction
database table
```

---

# 17. Normalización

Las distintas fuentes deberían convertirse, cuando sea posible, a un formato común.

Ejemplo:

```json
{
  "timestamp": "2026-08-25T13:21:03.481-06:00",
  "session_id": "flow-001",
  "source": "wildfly",
  "component": "ApplicationService",
  "event_type": "method",
  "operation": "saveApplication",
  "correlation_id": "req-8172",
  "message": "Saving application",
  "metadata": {}
}
```

Evento de base de datos:

```json
{
  "timestamp": "2026-08-25T13:21:03.612-06:00",
  "session_id": "flow-001",
  "source": "postgresql",
  "component": "database",
  "event_type": "sql",
  "operation": "INSERT",
  "correlation_id": "req-8172",
  "metadata": {
    "table": "application"
  }
}
```

---

# 18. Resultado de correlación

Ejemplo:

```text
FLOW-001

13:21:01 frontend
POST /applications

13:21:02 gateway
request forwarded

13:21:02 wildfly
ApplicationService.saveApplication()

13:21:03 database
SELECT citizen

13:21:03 wildfly
validateCitizen()

13:21:04 database
INSERT application

13:21:04 database
INSERT application_history

13:21:05 wildfly
folio generated

13:21:05 frontend
HTTP 201
```

---

# 19. Paquete controlado

Observe + Correlate deben generar un paquete de evidencia acotado.

Ejemplo:

```text
/runtime-discovery/
├── README.md
├── session.json
├── evidence/
│   ├── events.jsonl
│   ├── application.log
│   ├── database.log
│   └── http.jsonl
├── correlated/
│   └── flow.json
├── legacy/
│   └── source-code/
└── output/
```

El objetivo es que el agente trabaje sobre evidencia útil y estructurada.

---

# 20. Claude Code / Codex como motor de análisis

En la primera versión, PEPPER puede utilizar:

```text
Claude Code
Codex
```

El agente recibe:

```text
evidencia correlacionada
logs relevantes
metadata
código fuente cuando exista
configuración disponible
```

Y puede:

- inspeccionar logs;
- buscar clases;
- seguir referencias;
- inspeccionar SQL;
- analizar configuración;
- relacionar eventos con implementación;
- contrastar runtime contra código;
- detectar contradicciones;
- producir un descubrimiento estructurado.

---

# 21. Principio read-only

Durante Discovery, el agente puede:

```text
leer
buscar
inspeccionar
correlacionar
analizar
reportar
```

No debe:

```text
modificar código
cambiar datos
actualizar configuración
reiniciar servicios
desplegar
corregir defectos
hacer commit
hacer push
```

PEPPER descubre.

No repara.

---

# 22. Fase 3 — DISCOVER

El objetivo es transformar evidencia técnica en conocimiento útil.

Debe responder preguntas como:

- ¿Qué flujo funcional ocurrió?
- ¿Qué componentes participaron?
- ¿Qué secuencia se observó?
- ¿Qué validaciones parecen existir?
- ¿Qué datos fueron consultados?
- ¿Qué tablas cambiaron?
- ¿Qué dependencias participaron?
- ¿Qué bifurcaciones parecen existir?
- ¿Qué reglas de negocio son candidatas?
- ¿Qué evidencia respalda cada conclusión?
- ¿Qué no puede determinarse?
- ¿Qué contradice al código o documentación?

---

# 23. Límite del discovery

PEPPER no debe afirmar:

> “Descubrí todas las reglas de negocio.”

La salida correcta es:

> **flujos observados y reglas de negocio candidatas respaldadas por evidencia de ejecución.**

Una regla solo puede descubrirse dinámicamente si deja algún rastro observable.

---

# 24. Ejemplo de regla candidata

```text
Regla candidata:
El sistema parece validar el estado del ciudadano antes de guardar una solicitud.

Evidencia:
E-034 ApplicationService.validateCitizen
E-037 SELECT citizen.status
E-041 INSERT application

Confianza:
alta
```

No debe convertirse en una regla autoritativa sin evidencia suficiente.

---

# 25. Modelo de confianza

Cada conclusión debería incorporar confianza.

Ejemplo:

```text
confirmada
fuertemente sustentada
candidata
desconocida
contradicha
```

Posteriormente puede mapearse a estados de STARK:

```text
confirmada
inferida
posible defecto
pendiente
```

---

# 26. Comparación runtime vs código

Cuando existe código fuente:

```text
DESCUBRIMIENTO ESTÁTICO
A → B → C → D

EJECUCIÓN OBSERVADA
A → B → X → D
```

Resultado:

```text
CONTRADICCIÓN

Esperado:
C

Observado:
X

Posibles causas:
- configuración
- condición no documentada
- código muerto
- comportamiento dependiente del ambiente
- análisis estático incompleto

Validación humana requerida.
```

---

# 27. Fase 4 — EXPORT

Salida mínima:

```text
runtime-discovery.md
runtime-discovery.json
```

Posibles artefactos adicionales:

```text
flows.json
candidate-rules.json
contradictions.json
unknowns.json
dependencies.json
evidence-map.json
```

---

# 28. Contrato de salida

Ejemplo:

```json
{
  "flow": {
    "name": "Registrar solicitud",
    "observed_start": "...",
    "observed_end": "..."
  },
  "components": [],
  "steps": [],
  "candidate_rules": [],
  "queries": [],
  "dependencies": [],
  "errors": [],
  "contradictions": [],
  "unknowns": [],
  "evidence": []
}
```

---

# 29. Relación con STARK

PEPPER no reemplaza el onboarding de STARK.

Lo complementa.

```text
STARK
descubrimiento estático
        +
PEPPER
descubrimiento dinámico
        ↓
análisis legacy
        ↓
validación humana
        ↓
mantenimiento / reingeniería
```

STARK puede consumir:

```text
runtime-discovery.md
runtime-discovery.json
flows.json
candidate-rules.json
contradictions.json
unknowns.json
```

---

# 30. Separación de responsabilidades

## PEPPER

```text
rehydrate
observe
capture
normalize
correlate
discover
export
```

## STARK

```text
analizar contexto general
comparar fuentes
gestionar contradicciones
definir trabajo
planear cambios
validar implementación
```

> **PEPPER piensa antes de que STARK actúe.**

---

# 31. MVP

El MVP debe demostrar un ciclo completo.

Entorno inicial sugerido:

```text
Java
WildFly
PostgreSQL
Docker
```

Entrada:

```text
WAR o código
dump de base de datos
configuración disponible
```

Flujo de prueba:

```text
Registrar solicitud
```

Pipeline:

```text
1. Rehydrate
2. levantar legacy
3. marcar inicio de flujo
4. ejecutar acción
5. marcar fin de flujo
6. capturar logs y eventos
7. correlacionar
8. generar paquete controlado
9. analizar con Codex o Claude Code
10. generar runtime-discovery.md/json
```

---

# 32. Criterios de éxito del MVP

El MVP tiene éxito si:

- reconstruye un entorno ejecutable;
- identifica claramente cuando falta información;
- captura evidencia relevante;
- reduce ruido;
- reconstruye la secuencia del flujo;
- identifica componentes participantes;
- detecta consultas y tablas relevantes;
- genera reglas candidatas útiles;
- cada inferencia apunta a evidencia;
- distingue hechos de inferencias;
- identifica desconocidos;
- genera una salida reutilizable;
- el proceso puede repetirse con otro flujo.

No necesita descubrir automáticamente todo el sistema.

---

# 33. Qué no debe construirse todavía

Para el MVP:

```text
no dashboard empresarial
no Kubernetes
no observabilidad completa
no multi-agent orchestration
no RAG
no base vectorial
no remediación automática
no monitoreo productivo continuo
no soporte universal de tecnologías
no modernización automática
```

Primero debe demostrarse:

```text
artefactos
→ runtime
→ evidencia
→ correlación
→ discovery
→ export
```

---

# 34. Estructura inicial del repositorio

Repositorio:

```text
stark-pepper/
│
├── README.md
│
├── docs/
│   ├── architecture.md
│   ├── rehydrate.md
│   ├── observe.md
│   ├── correlate.md
│   ├── discover.md
│   └── output-contract.md
│
├── pepper/
│   ├── rehydrate/
│   ├── observe/
│   ├── correlate/
│   ├── package/
│   ├── discover/
│   └── export/
│
├── profiles/
│   ├── java-wildfly-postgres/
│   └── examples/
│
├── schemas/
│   ├── event.schema.json
│   ├── runtime-discovery.schema.json
│   └── environment.schema.json
│
├── prompts/
│   ├── discovery.md
│   └── runtime-code-comparison.md
│
├── examples/
│   └── legacy-demo/
│
└── tests/
```

---

# 35. Roadmap

## Fase 1 — MVP

```text
1 legacy
Java + WildFly + PostgreSQL
Docker
Rehydrate asistido
captura manual de flujo
logs
correlación básica
Codex / Claude Code
Markdown + JSON
```

## Fase 2 — Herramienta estable

```text
perfiles reutilizables
múltiples recolectores
normalización estándar
correlación automática
validación de evidencia
historial de sesiones
contrato estable
```

## Fase 3 — Más stacks

Ejemplos:

```text
Spring Boot
Tomcat
Angular
Node
Oracle
MongoDB
microservicios
```

## Fase 4 — Integración madura con STARK

```text
PEPPER export
        ↓
STARK onboarding
        ↓
comparación estático ↔ dinámico
        ↓
contradicciones
        ↓
decisión humana
```

## Fase 5 — Piloto institucional

```text
múltiples legacy
múltiples equipos
perfiles tecnológicos
auditoría
políticas de evidencia
trazabilidad
integración con metodología
```

---

# 36. Propuesta de valor

PEPPER no debe presentarse como:

> “una IA que lee logs.”

La propuesta es:

> **Una herramienta de descubrimiento dinámico para sistemas legacy que reconstruye ambientes ejecutables, captura evidencia de ejecución real, correlaciona eventos y genera conocimiento técnico y funcional respaldado por evidencia para apoyar mantenimiento y reingeniería.**

---

# 37. Valor diferencial

La parte valiosa no es únicamente el agente.

La capacidad real está en:

```text
rehydrate
+
captura
+
normalización
+
correlación
+
reducción de contexto
+
análisis
+
evidencia
+
export
```

El agente interpreta.

PEPPER prepara la realidad que debe interpretar.

---

# 38. Principios del producto

> **Antes de entender cómo se comporta un legacy, primero hay que poder volverlo a poner vivo.**

> **La ejecución es evidencia.**

> **El agente interpreta la evidencia.**

> **El humano decide qué se convierte en conocimiento.**

> **PEPPER observa y estructura; STARK utiliza ese conocimiento para actuar.**

---

# 39. Resumen

PEPPER es un complemento independiente de STARK orientado al descubrimiento dinámico de sistemas legacy.

Su flujo completo es:

```text
Código / WAR / JAR / dist / backup
                ↓
         0. REHYDRATE
   reconstruye entorno ejecutable
                ↓
           1. OBSERVE
      captura ejecución real
                ↓
          2. CORRELATE
     estructura la evidencia
                ↓
       PAQUETE CONTROLADO
                ↓
      Claude Code / Codex
                ↓
          3. DISCOVER
 flujos + reglas candidatas + evidencia
                ↓
           4. EXPORT
                ↓
              STARK
```

PEPPER puede comenzar desde los propios restos técnicos de un legacy.

No exige que el sistema llegue perfectamente documentado ni ejecutándose.

Cuando existen suficientes artefactos:

```text
rehidrata
→ observa
→ correlaciona
→ descubre
→ exporta
```

Cuando no existen:

```text
detecta
→ documenta faltantes
→ bloquea con evidencia
```

El objetivo final es convertir un sistema legacy difícil de entender en un conjunto de **flujos observados, reglas candidatas, dependencias, contradicciones y evidencia estructurada** que pueda utilizarse de manera confiable durante mantenimiento y reingeniería.
