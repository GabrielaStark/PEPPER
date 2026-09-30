# Seguridad

PEPPER existe para levantar un sistema con datos de producción sin que nada salga de la máquina.
Una fuga es el defecto más grave que puede tener, y no queremos leerla en un issue público.

## Reportar una vulnerabilidad

Abre un **aviso de seguridad privado** en GitHub (`Security` → `Report a vulnerability` en
https://github.com/GabrielaStark/PEPPER), no un issue público, con:

- qué sale y hacia dónde (un contenedor, el navegador, el contexto del agente, el paquete);
- cómo reproducirlo (un compose, un HTML, un comando, un artefacto sintético);
- qué versión (`python3 -m pepper --version`, o el commit) y sobre qué daemon (`docker info`: versión,
  sistema, rootless).

Respondemos en cinco días hábiles con una evaluación y, si procede, una corrección y una nota en
`CHANGELOG.md`. Si el aviso privado no está disponible, escribe a la autora por el contacto de su
perfil de GitHub antes de publicar nada.

## Qué cuenta como vulnerabilidad

- Un contenedor del legacy que alcance internet, la VPN o el host.
- El navegador del explorador o de `/pepper-observe` que contacte otro origen.
- `isolate` que dé VERIFICADO sin haber comprobado algo.
- El agente orquestador leyendo el respaldo, la evidencia cruda o la base desechable por un camino que
  el guardia no cierra.
- Un paquete `remote` que lleve algo fuera de lo autorizado, o sin autorización.
- Una credencial o un dato con patrón que llegue a `docs/pepper/` o al documento.

Lo que no protege PEPPER, y por qué, está en `docs/documentacion/THREAT-MODEL.md`: un reporte que
caiga ahí sigue siendo bienvenido, pero probablemente sea una limitación declarada.

## Lo que hacemos por nuestro lado

- Cada bypass encontrado se convierte en una prueba de regresión antes de cerrarse.
- CI corre la suite, la prueba hermética con un Chromium real y un E2E con Docker de verdad en
  cada cambio; en CI nada de eso se salta.
- El historial de auditorías está en `docs/documentacion/DECISIONES.md` y en
  `docs/documentacion/AUDITORIA-2026-09-29.md`.
