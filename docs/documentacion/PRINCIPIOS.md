# Principios de PEPPER

PEPPER es una herramienta de Gabriela Stark (@iamgabstark_), complemento
independiente de stark para el descubrimiento dinámico de sistemas legacy.
Donde stark lee código y documentación, PEPPER enciende el sistema y observa
qué hace de verdad.

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

## 3. Lo determinístico no se delega al agente

Parsear, correlacionar, reducir ruido y validar contratos lo hace el núcleo,
igual cada vez, auditable. El agente interpreta la realidad que PEPPER preparó;
nunca busca a ojo en gigabytes de logs. El agente es la cabeza; las
herramientas son las manos.

## 4. El núcleo no conoce tecnologías

Todo lo específico de un stack entra como datos — un perfil, con sus parsers
declarativos. Si una línea del núcleo dejaría de funcionar con otro stack,
pertenece a un perfil. Así PEPPER sirve para cualquier legacy sin crecer por
tecnología.

## 5. Ningún legacy recibe "no soportado"

Hay perfil → pipeline completo. No hay perfil pero el sistema corre → se
observa con colectores genéricos. Ni siquiera corre → inspección con reporte
de faltantes y borrador de perfil. Los tres escalones producen un entregable,
y cada legacy nuevo alimenta la librería de perfiles.

## 6. El legacy es solo lectura

PEPPER descubre; no repara. Lee, levanta contenedores desechables, observa,
correlaciona, reporta. No modifica artefactos, ni datos, ni configuración del
sistema original; no corrige defectos; no hace commit ni push en su repo.

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

PEPPER observa y estructura; el agente interpreta; cada fase termina en un gate
humano. Lo que PEPPER entrega a stark entra como `inferida` o como pregunta
abierta — solo una persona con nombre promueve una regla a `confirmada`.

## El principio que aplica a PEPPER mismo

Una herramienta que existe para no adivinar no puede construirse adivinando lo
que hará falta. Hasta que el ciclo completo demuestre valor con legacies
reales, PEPPER no construye: dashboard, Kubernetes, observabilidad continua,
orquestación multi-agente, RAG ni base vectorial, remediación automática,
monitoreo productivo, soporte universal prometido, modernización automática.
Primero: artefactos → runtime → evidencia → correlación → discovery → export.
