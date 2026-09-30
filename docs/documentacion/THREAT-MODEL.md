# Modelo de amenazas de PEPPER

Qué protege PEPPER, contra quién, con qué mecanismo, y qué NO protege. Es la página que un
auditor lee primero y la que decide si PEPPER sirve para tus datos. Si algo aquí no coincide con
el código, el código está mal y este documento manda hasta que se corrija.

## Los datos

Un legacy trae tres clases de datos, y las tres pasan por PEPPER:

1. **Datos de personas en el respaldo** (padrones, expedientes, nómina; en México CURP, RFC, CLABE,
   nombres, domicilios). Son datos de producción: PEPPER no los sintetiza ni los anonimiza.
2. **Credenciales** en el artefacto y en el respaldo: la contraseña del datasource, `USER MAPPING`
   de `dblink`, claves de servicios externos, llaves en constantes.
3. **Identidad técnica** del ambiente: hosts, IPs, nombres de usuario, rutas.

## Los receptores

Cada cosa que PEPPER produce va a uno de estos destinos, y la garantía es distinta en cada uno:

| Destino | Qué llega | Control |
|---|---|---|
| Contenedores en la máquina | el sistema entero, con sus datos | red interna sin salida, verificada antes y en vivo, con una sonda desde dentro |
| Disco de la máquina (`docs/pepper/`, `pepper-out/`, `evidence/`) | mapa redactado, evidencia cruda, credenciales del datasource (`pepper-out/rehydrate/.env`, modo 0600) | `.gitignore`; el guardia impide que el agente los lea |
| **El contexto del agente orquestador** (Claude Code) | lo que el agente lee para decidir | el guardia de datos (hook) + redacción en origen; **lo que el agente lee viaja al proveedor del modelo** |
| El paquete del discovery (modo `remote`) | evidencia y artefactos inspeccionados y sustituidos | gate con autorización de una persona, por sistema y por categoría |
| El documento (`funcional.md`, se commitea) | prosa del agente | Export rechaza credenciales y datos con patrón; la prosa la revisa una persona |
| Los transcripts de Claude Code (`~/.claude/projects/…`) | todo lo que el agente leyó y escribió | **ninguno de PEPPER**: es del cliente de Claude Code; en sesiones en la nube viven fuera de la máquina |

## Los adversarios y los accidentes

PEPPER no está diseñado contra un atacante dentro de la máquina. Está diseñado contra estos
accidentes, que ocurrieron o pudieron ocurrir:

| Amenaza | Ejemplo real | Mecanismo | Verificado por |
|---|---|---|---|
| El legacy llama a producción | una vista con `dblink` consultó la base real por la VPN (D19) | red `internal`, servidores foráneos re-apuntados al stub, sumidero DNS por servicio, todo host externo con alias al stub | `isolate` estático y en vivo; la sonda desde la red interna intenta salir y tiene que fracasar |
| El navegador del explorador o de una persona sale por fuera de los contenedores | un `<object>` cargó un servidor real (D25) | CSP con todo destino `'self'` y `webrtc 'block'`, guardián inyectado, `Location` y `meta refresh` reescritos; el explorador corre Chromium con el resolver cerrado (`--host-resolver-rules`), sin service workers, sin prefetch; `/pepper-observe` usa ESE navegador, no el de la persona | prueba hermética con Chromium real en CI; `isolate --live` exige la política en la respuesta |
| Un ingress impostor o evidencia fabricada | `alpine sh -c exfiltrar` llamado `ingress` daba verde (D22) | identidad del ingress (imagen, sin entrypoint, argv exacto, un montaje `:ro` con el hash de `proxy.py`); manifest interno y externo con hashes | `isolate`, Export |
| El daemon no aísla como el compose promete | Podman por socket compatible, `iptables: false`, Windows containers | `docker info` como precondición (solo Linux; rootless declarado); la sonda desde la red interna es la que decide, no la lectura del compose | `isolate --live` |
| Ejecución de datos del artefacto en las plantillas | un rol del respaldo con `$(…)` corría en el contenedor de restauración | cada valor de plantilla validado por su forma y sustituido en una pasada; `cap_drop: NET_RAW`, `no-new-privileges` en cada servicio | `rehydrate` (BLOCKED), `isolate` |
| El agente lee el respaldo o la evidencia cruda y eso viaja al proveedor del modelo | `/pepper` mandaba consultar la base desechable con `psql` | el guardia de datos: hook `PreToolUse` que bloquea `legacy/` (salvo listar y textos limpios), `evidence/*/{screens,http.jsonl,containers,raw}`, `pepper-out/*/correlated`, `.env`, compose y restore rendidos, `docker exec/logs db`, clientes SQL; `roles[].user_sql` resuelve las claves de usuario dentro del contenedor | `tests/test_guardia.py` |
| El paquete lleva el respaldo entero | lo no inspeccionable viajaba con solo autorizarlo | lo que el escáner no puede leer NO viaja (`excluded_uninspected`), salvo `--include-uninspected` con autorización por archivo | `tests/test_boundary.py` |
| El agente se autoriza solo | `pepper authorize --by "nombre"` desde Bash | `authorize` exige terminal interactiva y la palabra AUTORIZO tecleada; el hook bloquea escribir la autorización | `tests/test_tools.py`, `tests/test_guardia.py` |
| Datos en el documento que se commitea | un nombre o una CURP transcritos | Export corre el escáner sobre `output/` | `tests/test_export.py` |
| `git push` accidental del legacy | el clon de la herramienta traía el remoto | el workspace se crea aparte con `pepper init`, sin repositorio ni remoto; `.gitignore` en ambos | — |
| Instrucciones dentro del material | un comentario del código que pide "ignora tus reglas" | doctrina: el material es datos; el agente lo reporta | ninguno técnico |

## Lo que PEPPER NO protege (y así se dice)

- **El proveedor del modelo ve lo que el agente lee.** El guardia cierra los canales más gruesos
  (el respaldo, las capturas, los cuerpos de las peticiones, los logs con SQL y parámetros), pero
  el agente sí lee `docs/pepper/map/*.md` (catálogos ≤300 filas redactados por nombre de columna y
  por patrón de valor, cuerpos de funciones redactados, constantes), `environment.json` (IP y nombre
  de la base, usuario del datasource), `evidence/<sid>/explore.jsonl` (mensajes de rechazo, encabezados,
  ids de campos y el texto de las opciones de cada combo) y `session.json`. Un nombre propio en un
  mensaje de rechazo o en la segunda opción de un combo viaja. Si eso no es aceptable para tus datos,
  PEPPER no es para ti todavía: no existe un camino con modelo local.
- **La sustitución no es anonimización.** Se sustituyen CURP, RFC, CLABE, tarjeta y correo, y se
  quitan credenciales con patrón. Nombres, domicilios, teléfonos, fechas de nacimiento, NSS, placas,
  hashes de contraseña y tokens sin forma conocida no se detectan. La persona que autoriza lo sabe.
- **El guardia es contra el descuido, no contra un agente adversario.** Variables de entorno, alias,
  rutas construidas en tiempo de ejecución o código en línea que no nombra la ruta no se resuelven.
  Un agente que quiera saltárselo puede.
- **La red de contenedores se verifica en Docker sobre Linux** (Docker Engine, Docker Desktop). Podman,
  OrbStack, Rancher Desktop, WSL2 en modo `mirrored` y daemons con `iptables: false` no están probados;
  la sonda desde dentro es lo único que dice si de verdad no sale nada. Windows containers: no.
- **El host es alcanzable desde la red interna en Docker < 28**: un servicio que escuche en `0.0.0.0`
  en la máquina lo alcanzaría el legacy. La sonda lo detecta y lo reporta.
- **Los transcripts de Claude Code** guardan cada salida de herramienta fuera del workspace; `down -v`
  y borrar la herramienta no los tocan. En sesiones en la nube viven fuera de la máquina.
- **Legal.** PEPPER procesa datos personales de producción y, en modo remoto, los transfiere a un
  tercero (el proveedor del modelo). Quién es el responsable, quién el encargado, si hace falta un
  aviso o una base de licitud, y si la transferencia es internacional, lo decide la organización
  dueña del dato conforme a su marco (en México, la LFPDPPP para particulares y la LGPDPPSO para
  sujetos obligados). PEPPER deja escrito qué salió, con qué autorización y de quién; no decide si
  podía salir.
