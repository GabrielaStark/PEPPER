#!/usr/bin/env python3
"""E2E de Docker: PEPPER levanta un entorno aislado de verdad y lo sirve por el ingress.

Lo que comprueba, con contenedores reales y sin red más allá del registro de imágenes:

    plan → compose → PostgreSQL restaurado desde un respaldo real → la aplicación arranca →
    el ingress responde en 127.0.0.1 → `isolate --live` en verde → apagado sin dejar volúmenes.

El "legacy" es mínimo y sintético: un servicio HTTP de 40 líneas (`examples/e2e-docker/Servicio.java`)
compilado aquí mismo con `javac` y empaquetado como un jar ejecutable con la forma de un fat jar de
Spring Boot, más un respaldo en formato custom de `pg_dump`. No imita un sistema real ni lo pretende:
existe para que un cambio en el núcleo que rompa el levantamiento se caiga en CI, en vez de
descubrirse a mano tres semanas después contra un legacy de verdad.

    python3 scripts/e2e_docker.py            # levanta, comprueba y apaga
    python3 scripts/e2e_docker.py --keep     # deja el entorno arriba para mirarlo

Necesita: Docker con Compose v2, un JDK (`javac`) y `jsonschema`. Sin Docker se salta con código 0
y lo dice: un entorno que no se puede levantar no es una falla del código.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PROFILE_ID = "java-springboot-fatjar-postgres"
DB_NAME, DB_USER, DB_PASSWORD, DB_IP, DB_PORT = "demo_prod", "demo", "e2e-secreto", "10.100.0.2", 5432
APP_PORT = 8099
HOST_PORT = 18099

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


def construir_legacy(legacy: Path) -> None:
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


def correr(argv: list, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, **kw)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--keep", action="store_true", help="no apagar el entorno al terminar")
    parser.add_argument("--work", type=Path, help="directorio de trabajo (default: uno temporal)")
    args = parser.parse_args()

    if not shutil.which("docker") or correr(["docker", "compose", "version"]).returncode != 0:
        print("e2e: sin Docker con Compose v2 — se salta (no es una falla del código)")
        return 0
    if correr(["docker", "info"]).returncode != 0:
        print("e2e: el daemon de Docker no responde — se salta (no es una falla del código)")
        return 0

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

    try:
        paso("construyendo el legacy sintético")
        construir_legacy(legacy)

        paso("levantando el entorno con `pepper rehydrate --up`")
        levantar = correr([sys.executable, "-m", "pepper", "rehydrate", str(legacy), "--profile", PROFILE_ID,
                           "--out", str(out), "--docs", str(docs), "--port", str(HOST_PORT), "--wait", "300", "--up"],
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
        for esperada in ("db", "servicio", "ingress", "stub"):
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
            with urllib.request.urlopen(f"http://127.0.0.1:{HOST_PORT}/", timeout=20) as respuesta:
                cuerpo = respuesta.read().decode("utf-8", "replace")
            if respuesta.status != 200 or "Servicio del E2E" not in cuerpo:
                fallos.append(f"el ingress respondió {respuesta.status} sin la página del servicio")
            else:
                print(f"  el ingress responde 200 y sirve la aplicación ({len(cuerpo)} bytes)")
        except (urllib.error.URLError, OSError) as error:
            fallos.append(f"el ingress no respondió en 127.0.0.1:{HOST_PORT}: {error}")

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
