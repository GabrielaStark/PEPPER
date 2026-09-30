#!/usr/bin/env python3
"""E2E de Docker: PEPPER levanta un entorno aislado de verdad y lo sirve por el ingress.

Lo que comprueba, con contenedores reales y sin red más allá del registro de imágenes:

    plan → compose → PostgreSQL restaurado desde un respaldo real → la aplicación arranca →
    el ingress responde en 127.0.0.1 → `isolate --live` en verde → apagado sin dejar volúmenes.

El "legacy" es mínimo y sintético, uno por familia (`--profile`):

- `java-springboot-fatjar-postgres` (default): un servicio HTTP de 40 líneas (`examples/e2e-docker/Servicio.java`)
  compilado aquí mismo con `javac` y empaquetado como un jar ejecutable con la forma de un fat jar de
  Spring Boot, más un respaldo en formato custom de `pg_dump`.
- `php-apache-mysql`: dos archivos de PHP clásico (`examples/e2e-php/`) que leen su `.env` y consultan
  MySQL por PDO, entregados como CARPETA (rehydrate.artifact_kind = directory), más un respaldo
  mysqldump sintético (`profiles/php-apache-mysql/fixtures/synthesize.py`). Es la primera familia que
  no es JVM: si esto levanta, el perfil hecho solo con datos levanta.

Ninguno imita un sistema real ni lo pretende: existen para que un cambio en el núcleo que rompa el
levantamiento se caiga en CI, en vez de descubrirse a mano tres semanas después contra un legacy de verdad.

    python3 scripts/e2e_docker.py                              # levanta, comprueba y apaga (JVM)
    python3 scripts/e2e_docker.py --profile php-apache-mysql   # la familia PHP
    python3 scripts/e2e_docker.py --keep                       # deja el entorno arriba para mirarlo

Necesita: Docker con Compose v2, `jsonschema`, y para el perfil JVM un JDK (`javac`). Sin Docker se
salta con código 0 y lo dice (en CI falla): un entorno que no se puede levantar no es una falla del código.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DB_NAME, DB_USER, DB_PASSWORD, DB_IP, DB_PORT = "demo_prod", "demo", "e2e-secreto", "10.100.0.2", 5432
APP_PORT = 8099

# Por familia: cómo se construye el legacy sintético, qué piezas debe declarar environment.json,
# qué texto sirve la raíz y en qué puerto de loopback publica el ingress.
FAMILIAS = {
    "java-springboot-fatjar-postgres": {
        "construir": "construir_legacy_jvm", "piezas": ("db", "servicio", "ingress", "stub"),
        "marca": "Servicio del E2E", "puerto": 18099, "espera": "300",
    },
    "php-apache-mysql": {
        "construir": "construir_legacy_php", "piezas": ("db", "app", "ingress", "stub"),
        "marca": "Ventanilla del E2E", "puerto": 18098, "espera": "480",
    },
}

CONFIG = f"""server:
  port: {APP_PORT}
spring:
  datasource:
    url: jdbc:postgresql://{DB_IP}:{DB_PORT}/{DB_NAME}
    username: {DB_USER}
    password: {DB_PASSWORD}
"""


def paso(texto: str) -> None:
    print(f"\n=== {texto}", flush=True)


def construir_legacy_jvm(legacy: Path) -> None:
    """Compila el servicio, lo empaqueta como fat jar y escribe el respaldo."""
    from tests.test_systemmap import TABLES, write_custom_dump

    legacy.mkdir(parents=True, exist_ok=True)
    clases = legacy.parent / "clases"
    clases.mkdir(exist_ok=True)
    javac = shutil.which("javac")
    if not javac:
        raise SystemExit("e2e: hace falta un JDK con `javac` en el PATH")
    # release 8: la imagen del perfil es un JRE 8 (server_images.java)
    compilar = subprocess.run([javac, "--release", "8", "-d", str(clases),
                               str(ROOT / "examples" / "e2e-docker" / "Servicio.java")],
                              capture_output=True, text=True)
    if compilar.returncode != 0:
        raise SystemExit(f"e2e: javac falló:\n{compilar.stderr}")

    jar = legacy / "servicio-1.0.jar"
    with zipfile.ZipFile(jar, "w") as z:
        z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\nMain-Class: Servicio\n\n")
        for clase in sorted(clases.rglob("*.class")):
            z.write(clase, str(clase.relative_to(clases)))
        # La forma de un fat jar de Spring Boot: así lo reconocen las reglas de `classify` del perfil.
        z.writestr("BOOT-INF/lib/marcador.jar", "")
        z.writestr("BOOT-INF/classes/application.yml", CONFIG)
    write_custom_dump(legacy / "respaldo.dump", TABLES)
    (legacy / "NOTAS.md").write_text(
        "# Notas del legacy (E2E sintético de PEPPER)\n\n"
        "No es un sistema real: un servicio HTTP mínimo para comprobar que el levantamiento funciona.\n",
        encoding="utf-8")
    print(f"  legacy sintético: {jar.name} ({jar.stat().st_size // 1024} KB) + respaldo.dump")


def construir_legacy_php(legacy: Path) -> None:
    """Copia la ventanilla de PHP clásico como CARPETA, escribe su .env y genera el respaldo mysqldump."""
    fuente = legacy / "ventanilla"
    shutil.copytree(ROOT / "examples" / "e2e-php", fuente)
    (fuente / "README.md").unlink()   # el README explica el fixture; no es parte del "sistema"
    (fuente / ".env").write_text(
        "APP_NAME=Ventanilla-E2E\n"
        "DB_CONNECTION=mysql\n"
        "DB_HOST=127.0.0.1\n"           # la base vivía en la misma máquina: el app comparte la pila de red de db
        "DB_PORT=3306\n"
        "DB_DATABASE=tramites\n"
        f"DB_USERNAME=app_tramites\n"
        f"DB_PASSWORD={DB_PASSWORD}\n"
        "MAIL_HOST=smtp.ejemplo.gob\n"   # un host externo: resuelve al stub
        "MAIL_PORT=587\n",
        encoding="utf-8")
    sintetizar = subprocess.run([sys.executable, str(ROOT / "profiles" / "php-apache-mysql" / "fixtures" / "synthesize.py"),
                                 str(legacy / "respaldo.sql")], capture_output=True, text=True)
    if sintetizar.returncode != 0:
        raise SystemExit(f"e2e: synthesize.py falló:\n{sintetizar.stderr}")
    (legacy / "NOTAS.md").write_text(
        "# Notas del legacy (E2E sintético de PEPPER, familia PHP)\n\n"
        "No es un sistema real: una ventanilla mínima en PHP clásico para comprobar que la familia levanta.\n\n"
        "Producción corre PHP 8.2 sobre Apache 2.4; MySQL 5.7.\n",
        encoding="utf-8")
    archivos = sum(1 for p in fuente.rglob("*") if p.is_file())
    print(f"  legacy sintético: carpeta {fuente.name}/ ({archivos} archivos) + respaldo.sql")


def correr(argv: list, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, **kw)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", choices=sorted(FAMILIAS), default="java-springboot-fatjar-postgres",
                        help="qué familia levantar (default: la JVM)")
    parser.add_argument("--keep", action="store_true", help="no apagar el entorno al terminar")
    parser.add_argument("--work", type=Path, help="directorio de trabajo (default: uno temporal)")
    args = parser.parse_args()

    # En CI el E2E es obligatorio: un runner sin Docker apagaría la única prueba real del levantamiento
    # sin que nadie lo notara (auditoría 2026-09-29). Fuera de CI, sin Docker se salta y lo dice.
    en_ci = bool(os.environ.get("PEPPER_CI"))
    if not shutil.which("docker") or correr(["docker", "compose", "version"]).returncode != 0:
        print("e2e: sin Docker con Compose v2 — " + ("FALLA: en CI el E2E es obligatorio" if en_ci else "se salta (no es una falla del código)"))
        return 1 if en_ci else 0
    if correr(["docker", "info"]).returncode != 0:
        print("e2e: el daemon de Docker no responde — " + ("FALLA: en CI el E2E es obligatorio" if en_ci else "se salta (no es una falla del código)"))
        return 1 if en_ci else 0

    import tempfile
    temporal = None
    if args.work:
        work = args.work
        work.mkdir(parents=True, exist_ok=True)
    else:
        temporal = tempfile.TemporaryDirectory()
        work = Path(temporal.name)
    legacy, out, docs = work / "legacy", work / "rehydrate", work / "docs"
    compose = out / "docker-compose.yml"
    fallos: list = []
    familia = FAMILIAS[args.profile]
    host_port = familia["puerto"]

    try:
        paso(f"construyendo el legacy sintético ({args.profile})")
        globals()[familia["construir"]](legacy)

        paso("levantando el entorno con `pepper rehydrate --up`")
        levantar = correr([sys.executable, "-m", "pepper", "rehydrate", str(legacy), "--profile", args.profile,
                           "--out", str(out), "--docs", str(docs), "--port", str(host_port), "--wait", familia["espera"], "--up"],
                          cwd=ROOT)
        print(levantar.stdout[-3000:] or levantar.stderr[-2000:])
        estado = json.loads((docs / "environment.json").read_text(encoding="utf-8"))["status"] \
            if (docs / "environment.json").is_file() else "SIN environment.json"
        if estado not in ("READY", "PARTIAL"):
            fallos.append(f"rehydrate terminó en {estado} (se esperaba READY o PARTIAL)")
            return informe(fallos)

        paso("comprobando lo que el entorno promete")
        entorno = json.loads((docs / "environment.json").read_text(encoding="utf-8"))
        piezas = {c["name"]: c for c in entorno["components"]}
        for esperada in familia["piezas"]:
            if esperada not in piezas:
                fallos.append(f"environment.json no declara la pieza `{esperada}` (declara: {', '.join(piezas)})")
        arrancadas = [v for v in entorno["validations"] if "arrancó" in v["check"]]
        if not arrancadas or any(v["result"] != "pass" for v in arrancadas):
            fallos.append(f"la aplicación no arrancó: {[v for v in arrancadas]}")
        restaurada = [v for v in entorno["validations"] if "restaurados" in v["check"]]
        if not restaurada or restaurada[0]["result"] != "pass":
            fallos.append(f"la base no quedó restaurada: {restaurada}")

        # El ingress sirve la aplicación en loopback, y solo en loopback.
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{host_port}/", timeout=20) as respuesta:
                cuerpo = respuesta.read().decode("utf-8", "replace")
            if respuesta.status != 200 or familia["marca"] not in cuerpo:
                fallos.append(f"el ingress respondió {respuesta.status} sin la página de la aplicación ({familia['marca']!r} no aparece): "
                              + cuerpo[:300].replace("\n", " "))
            else:
                print(f"  el ingress responde 200 y sirve la aplicación ({len(cuerpo)} bytes)")
        except (urllib.error.URLError, OSError) as error:
            fallos.append(f"el ingress no respondió en 127.0.0.1:{host_port}: {error}")

        # El proxy deja su línea de evidencia: sin eso, Observe no vería nada.
        registro = correr(["docker", "compose", "-f", str(compose), "logs", "--no-log-prefix", "ingress"]).stdout
        if '"direction":"request"' not in registro.replace(" ", ""):
            fallos.append("el ingress no emitió la línea de petición en http.jsonl")
        else:
            print("  el ingress emitió su evidencia (http.jsonl por stdout)")

        paso("verificando el aislamiento en vivo")
        aislado = correr([sys.executable, "-m", "pepper", "isolate", str(compose), "--live"], cwd=ROOT)
        print(aislado.stdout[-1500:] or aislado.stderr[-800:])
        if aislado.returncode != 0 or "AISLADO (verificado)" not in aislado.stdout:
            fallos.append("`isolate --live` no dio el entorno por aislado")

        return informe(fallos)
    finally:
        if compose.is_file() and not args.keep:
            paso("apagando (y borrando el volumen con los datos)")
            correr(["docker", "compose", "-f", str(compose), "down", "-v", "--remove-orphans"])
        elif args.keep:
            print(f"\nel entorno queda arriba: docker compose -f {compose} down -v")
        if temporal and args.keep:
            temporal._finalizer.detach()   # no borrar el directorio si el entorno sigue vivo
        elif temporal:
            temporal.cleanup()


def informe(fallos: list) -> int:
    print()
    if fallos:
        print("❌ E2E de Docker: el entorno no cumple lo que promete")
        for f in fallos:
            print(f"   - {f}")
        return 1
    print("✅ E2E de Docker: entorno levantado, base restaurada, aplicación servida por el ingress y aislamiento verificado")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
