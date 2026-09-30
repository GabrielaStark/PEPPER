# Principios de PEPPER

PEPPER es una herramienta de Gabriela Stark (@iamgabstark_), complemento
independiente de stark para el descubrimiento dinámico de sistemas legacy.
Donde stark lee código y documentación, PEPPER enciende el sistema y observa
qué hace de verdad. PEPPER se usa sola; stark es a dónde puede seguir el
conocimiento.

Todo agente lee este archivo antes de actuar y respeta estos principios como
reglas duras. Los cuatro principios de stark — no sobre-ingeniería, seguridad
por diseño, despliegue simple, documentación limpia — aplican también a PEPPER
y a todo lo que PEPPER produce.

## 1. Observar primero, inferir después, comparar al final

Ninguna fuente contiene toda la verdad: documentación, código, base de datos,
configuración, runtime, conocimiento tácito. PEPPER agrega el runtime como
evidencia explícita y contrasta lo que dice el código, lo que dice la
documentación y lo que realmente hace el sistema. En ese orden, siempre.

## 2. La ejecución es evidencia; toda conclusión la referencia

Una conclusión sin evidencia señalable no es una conclusión. Cada regla, paso
o contradicción apunta a un evento o a una línea cruda; lo que no se puede
señalar se declara como desconocido. `pepper export` rechaza lo que no resuelve.
Lo que Export comprueba es **trazabilidad**: que cada fuente citada exista. Que la
fuente sostenga la afirmación lo comprueba una persona leyendo el documento;
PEPPER no lo promete.

## 3. Lo determinístico no se delega al agente

Parsear, correlacionar, reducir ruido y validar contratos lo hace el núcleo,
igual cada vez, auditable. El agente interpreta la realidad que PEPPER preparó;
nunca busca a ojo en gigabytes de logs. El agente es la cabeza; las
herramientas son las manos.

## 4. El núcleo conoce formatos, no sistemas

El núcleo trae lectores de **formatos**: bytecode de la JVM (vía `javap`),
Groovy compilado, configuración de Spring Boot, `pg_dump -Fc`, SQL en texto de
MySQL y PostgreSQL, logs de texto, HTML. Un **perfil** parametriza esos
lectores con datos (patrones, prefijos, imágenes, sondas, selectores) para un
sistema concreto: un legacy nuevo dentro de una familia conocida es un perfil,
no código. Un formato nuevo (IL de .NET, un `.bak` de SQL Server, un
`web.config`, JavaScript empacado) es un lector nuevo en Python, en
`pepper/inspect/readers/`, más su entrada en el contrato, y así se dice. Lo que
el núcleo nunca conoce es un sistema: ningún `if` por el nombre de un legacy,
ninguna ruta de un cliente, ningún dominio de negocio.

## 5. Ningún legacy recibe "no soportado"

Hay perfil → pipeline completo. No hay perfil pero el sistema corre → se
observa con colectores genéricos. Ni siquiera corre → inspección con reporte
de faltantes y borrador de perfil. Los tres escalones producen un entregable,
y cada legacy nuevo alimenta la librería de perfiles.

## 6. El legacy es solo lectura

PEPPER descubre; no repara. Lee, levanta contenedores desechables, observa,
correlaciona, reporta. No modifica artefactos, ni datos, ni configuración del
sistema original; no corrige defectos; no hace commit ni push en su repo. El
guardia de datos lo hace cumplir también al agente: `legacy/` no se escribe.

## 7. Fidelidad antes que modernización

Rehydrate reproduce el stack original, con sus versiones. Modernizar es otro
problema, de otra herramienta, después de entender.

## 8. El artefacto dicta el ambiente; BLOCKED es un entregable, no un reflejo

Un WAR y un respaldo, sin código ni configuración, es el caso normal — no el
bloqueado. Lo que el artefacto trae hardcodeado es la especificación del
ambiente que espera, y PEPPER lo fabrica: red, IPs, base, roles, stubs para lo
externo. Cuando de verdad no se puede (sin artefacto, sin respaldo restaurable),
dice con precisión qué falta. No inventa insumos; tampoco declara faltante lo
que el artefacto ya dice.

## 9. El humano decide qué se convierte en conocimiento

PEPPER observa y estructura; el agente interpreta. La herramienta corre sin
preguntar y se detiene solo donde una persona debe decidir: aislamiento en
rojo, insumo faltante, perfil nuevo en borrador, el envío de datos a un modelo
remoto (la persona lo autoriza en su terminal, por sistema y por categoría), un
documento rechazado por Export. Lo que PEPPER entrega a stark entra como
`inferida` o como pregunta abierta — solo una persona con nombre promueve una
regla a `confirmada`.

## 10. El agente que orquesta es un modelo remoto: lo que lee, sale

`/pepper` lo ejecuta Claude Code, y todo lo que ese agente lee viaja al
proveedor del modelo antes de cualquier autorización. Por eso lo que puede leer
está acotado por un guardia técnico, no por una frase: el respaldo, el
desplegable, la evidencia cruda y la base desechable no se abren desde el
agente; el núcleo se los da redactados. Lo que sí lee (el mapa redactado, el
estado del entorno, el registro de acciones del explorador) está escrito en
`docs/documentacion/THREAT-MODEL.md`, y quien tiene datos que no pueden salir
así, no usa PEPPER todavía.

## El principio que aplica a PEPPER mismo

Una herramienta que existe para no adivinar no puede construirse adivinando lo
que hará falta. Hasta que el ciclo completo demuestre valor con legacies
reales, PEPPER no construye: dashboard, Kubernetes, observabilidad continua,
orquestación multi-agente, RAG ni base vectorial, remediación automática,
monitoreo productivo, soporte universal prometido, modernización automática.
Primero: artefactos → runtime → evidencia → correlación → discovery → export.
