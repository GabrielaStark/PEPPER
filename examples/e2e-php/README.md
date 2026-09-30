# E2E de la familia PHP

Dos archivos de PHP clásico (`index.php`, `consulta.php`) que leen su `.env` y consultan MySQL por PDO. `scripts/e2e_docker.py --profile php-apache-mysql` los copia a `legacy/ventanilla/`, escribe el `.env` y `NOTAS.md`, genera el respaldo con `profiles/php-apache-mysql/fixtures/synthesize.py`, y PEPPER lo levanta con Docker: la carpeta viaja como `legacy.tar`, MySQL se restaura, el usuario del `.env` se crea, el contenedor de PHP compila `pdo_mysql`, Apache sirve por el ingress en `127.0.0.1`, y `isolate --live` verifica el aislamiento. CI lo corre en cada cambio.

No imita un sistema real ni lo pretende: existe para que un cambio en el núcleo o en el perfil que rompa el levantamiento de la primera familia que no es JVM se caiga en CI, no contra un legacy de verdad.
