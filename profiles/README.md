# Perfiles

Todo el conocimiento específico de un stack vive aquí, como datos. El núcleo consume perfiles vía [`schemas/profile.schema.json`](../schemas/profile.schema.json) y nunca conoce tecnologías directamente. Concepto y ciclo de vida: [PERFILES.md](../docs/documentacion/PERFILES.md); cómo redactar uno: skill [`perfil-stack`](../.claude/skills/perfil-stack/SKILL.md).

## Estructura de un perfil

```text
profiles/<id>/
├── profile.json          contrato (detección, receta, colectores, validaciones)
├── compose.template.yml  plantilla de orquestación referida por la receta
├── parsers/              normalizadores de cada fuente a event.schema.json
└── README.md             notas del perfil
```

## Reglas

- `status: "draft"` = redactado (a menudo por el agente al inspeccionar), sin que una persona lo haya promovido; **corre igual**, y `environment.json` / `funcional.md` lo declaran.
- `status: "validated"` = una persona lo vio correr de punta a punta contra un legacy real y lo marcó.
- Un perfil nunca requiere cambios en el núcleo. Si parece necesitarlos, el defecto está en el núcleo.
- Fidelidad primero: las recetas reproducen versiones originales, no modernizan.

## Perfiles

| id | estado | nota |
|---|---|---|
| [java-springboot-jsf-postgres](java-springboot-jsf-postgres/) | draft | corrió el ciclo entero contra un legacy real: extractores del mapa, receta de rehydrate, lectura de formularios, parsers |
| [java-wildfly-postgres](java-wildfly-postgres/) | draft | el primero; parsers de WildFly y PostgreSQL, sin extractores |
