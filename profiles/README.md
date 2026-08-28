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

- `status: "draft"` = redactado (a menudo por el agente durante Inspect), sin validar; **nunca corre sin supervisión humana**.
- `status: "validated"` = probado con un legacy real; habilita el escalón 1 automatizado.
- Un perfil nunca requiere cambios en el núcleo. Si parece necesitarlos, el defecto está en el núcleo.
- Fidelidad primero: las recetas reproducen versiones originales, no modernizan.

## Perfiles

| id | estado | nota |
|---|---|---|
| [java-wildfly-postgres](java-wildfly-postgres/) | draft | primer perfil; prueba la tubería completa |
