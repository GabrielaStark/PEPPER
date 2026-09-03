# Tests

```bash
python3 -m unittest discover -s tests          # todo
python3 -m unittest discover -s tests -v       # con detalle
```

Solo biblioteca estándar (`unittest`); `jsonschema` habilita las comprobaciones de forma contra los contratos (sin él, esos tests se saltan).

## Qué cubren

**`test_correlate.py`** — Correlate contra el fixture `examples/legacy-demo`:

- las cuentas de la clave de respuestas: 46 líneas crudas, 0 sin parsear, 17 eventos conservados, 2 peticiones, 1 sin asignar;
- el ruido desaparece (sondeos de salud, `SELECT 1`, validación de pool);
- la evidencia protegida sobrevive: el WARN de rechazo, los dos INSERT, la respuesta 409;
- **dos SQL idénticos con parámetros distintos no se deduplican** (regresión de un bug real encontrado al escribirlo);
- `events.jsonl` y `flow.json` validan contra sus schemas;
- las dos peticiones de la ventana no se mezclan, y la base de cada enlace es explícita;
- el evento de arranque queda como *sin asignar*, no descartado;
- todo evento resuelve a una línea cruda existente;
- **determinismo**: dos corridas producen bytes idénticos;
- una fuente sin parser falla con un mensaje claro.

**`test_export.py`** — Package y Export:

- el paquete tiene todo lo que el agente necesita y se niega a sobrescribir;
- la salida de referencia (`expected/runtime-discovery.json`) es aceptada y publicada con sus derivados;
- se rechazan: referencias a evidencia inexistente, `raw_ref` fuera de rango, conclusiones sin evidencia, confianzas fuera del vocabulario, sesión equivocada, salida ausente — y una salida rechazada **no se publica**.

**`test_isolate.py`** — el entorno rehidratado no alcanza nada externo:

- los casos de fuga reales del primer legacy (red sin `internal`, `network_mode: host`, red no declarada, servicio sin `networks`, `extra_hosts` externo, host del artefacto sin alias al stub);
- ningún contenedor tiene salida, incluido el ingress; el host entra por un puerto publicado en loopback;
- el ingress rechaza `entrypoint`, comandos shell, upstreams que no correspondan a una dependencia interna y cualquier montaje distinto al proxy verificado (`:ro`).

**`test_sensitive.py`** — el gate previo a agentes remotos: bloquea credenciales y CURP sin imprimir valores; exige reconocer binarios no inspeccionados; registra toda excepción en el manifest; marca el modo local para impedir que Claude Code lo abra por accidente.

**`test_proxy.py`** — el proxy HTTP del núcleo (todo en `127.0.0.1`, nada sale de la máquina):

- reenvía intacto (Host del cliente, redirects, HTML) e inyecta `X-Pepper-Correlation-Id` con el mismo id en petición, respuesta y app;
- captura cuerpos JSON y formularios, **redacta credenciales** (campos password/clave/secret/token) y jamás registra headers Authorization/Cookie;
- upstream caído → 502 registrado con nota;
- el `http.jsonl` que emite lo lee el `HttpProxyParser` del núcleo sin una línea sin parsear.

**`test_tools.py`** — `pepper detect` y `pepper validate`: señales dentro de WARs **y de tarballs** (la herramienta no asume stack), modo encima-del-repo, errores claros.

**`test_collect.py`** — el colector genérico de contenedores, con un `docker` fingido que devuelve logs enlatados y anota sus invocaciones:

- layout de salida (`http.jsonl` del ingress sin contaminar por su stderr; `containers/*.log` y `*.err.log`);
- `--since`/`--until` llevan el margen declarado; `container_name` explícito gana al nombre `<proyecto>-<servicio>-1`;
- servicios bajo demanda (`profiles`) y contenedores inexistentes quedan **saltados con razón**, nunca en silencio;
- no sobrescribe una sesión; ventana sin zona horaria falla claro.

## Lo que falta probar (cuando exista)

- Parsers de más stacks: cada perfil nuevo trae sus líneas de log como fixture.
- Ventanas concurrentes: dos peticiones traslapadas resueltas por afinidad, y el caso ambiguo que debe quedar sin asignar.
- La integración completa contra el legacy-demo **levantado de verdad**, comparando evidencia real contra la sintética.
