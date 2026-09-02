"""Colector genérico de contenedores: la ventana se copia igual cada vez.

Docker se finge con un ejecutable local que devuelve logs enlatados y anota
cómo fue invocado: así se prueba el colector sin daemon y sin salir de la
máquina, incluyendo que --since/--until llevan el margen declarado.
"""

import json
import os
import stat
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pepper.observe import collect  # noqa: E402

FAKE_DOCKER = r'''#!/usr/bin/env python3
import json, os, sys
with open(os.environ["FAKE_DOCKER_LOG"], "a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\n")
name = sys.argv[-1]
if name.endswith("ingress-1"):
    sys.stdout.write('{"ts":"2026-09-02T10:00:01-06:00","direction":"request","method":"GET","path":"/x","correlation_id":"req-1","client":"10.0.0.9"}\n')
    sys.stdout.write('{"ts":"2026-09-02T10:00:01-06:00","direction":"response","method":"GET","path":"/x","status":200,"duration_ms":9,"correlation_id":"req-1"}\n')
    sys.stderr.write("pepper-proxy: 0.0.0.0:8080 -> app:8080\n")
elif name == "db-real":
    sys.stderr.write("2026-09-02 16:00:01.000 UTC [77] u@d LOG:  statement: SELECT 1\n")
elif name.endswith("app-1"):
    sys.stdout.write("INFO arranque\n")
elif name.endswith("fantasma-1"):
    sys.stderr.write("Error response from daemon: No such container\n")
    sys.exit(1)
'''

COMPOSE = {
    "name": "demo",
    "services": {
        "ingress": {"image": "python:3-alpine"},
        "db": {"image": "postgres:16", "container_name": "db-real"},
        "app": {"image": "x"},
        "restore": {"image": "postgres:17", "profiles": ["restore"]},
        "fantasma": {"image": "y"},
    },
}

TZ = timezone(timedelta(hours=-6))
START = datetime(2026, 9, 2, 10, 0, 0, tzinfo=TZ)
END = datetime(2026, 9, 2, 10, 5, 0, tzinfo=TZ)


class CollectTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.docker = self.tmp / "docker"
        self.docker.write_text(FAKE_DOCKER, encoding="utf-8")
        self.docker.chmod(self.docker.stat().st_mode | stat.S_IEXEC)
        self.calls_log = self.tmp / "calls.jsonl"
        os.environ["FAKE_DOCKER_LOG"] = str(self.calls_log)
        self.out = self.tmp / "evidence"

    def tearDown(self):
        os.environ.pop("FAKE_DOCKER_LOG", None)
        self._tmp.cleanup()

    def _collect(self, session_id="flow-001"):
        return collect(COMPOSE, session_id, START, END, self.out, docker_bin=str(self.docker))

    def test_layout_y_contenido(self):
        summary = self._collect()
        http = (self.out / "flow-001" / "http.jsonl").read_text(encoding="utf-8")
        self.assertEqual(len(http.strip().splitlines()), 2)
        self.assertIn('"correlation_id":"req-1"', http)
        # el banner del proxy (stderr) no contamina el http.jsonl
        self.assertNotIn("pepper-proxy", http)
        db = (self.out / "flow-001" / "containers" / "db.err.log").read_text(encoding="utf-8")
        self.assertIn("SELECT 1", db)
        app = (self.out / "flow-001" / "containers" / "app.log").read_text(encoding="utf-8")
        self.assertIn("arranque", app)
        captured = {item["service"] for item in summary if "file" in item}
        self.assertEqual(captured, {"ingress", "db", "app"})

    def test_saltados_con_razon(self):
        summary = self._collect()
        skipped = {item["service"]: item["skipped"] for item in summary if "skipped" in item}
        self.assertIn("restore", skipped)          # servicio bajo demanda (profiles)
        self.assertIn("profiles", skipped["restore"])
        self.assertIn("fantasma", skipped)         # docker logs falló
        self.assertIn("No such container", skipped["fantasma"])

    def test_ventana_con_margen_en_since_until(self):
        self._collect()
        calls = [json.loads(line) for line in self.calls_log.read_text().splitlines()]
        since = calls[0][calls[0].index("--since") + 1]
        until = calls[0][calls[0].index("--until") + 1]
        self.assertEqual(since, (START - timedelta(seconds=30)).isoformat())
        self.assertEqual(until, (END + timedelta(seconds=30)).isoformat())

    def test_nombres_de_contenedor(self):
        self._collect()
        calls = [json.loads(line) for line in self.calls_log.read_text().splitlines()]
        names = {call[-1] for call in calls}
        self.assertIn("demo-ingress-1", names)     # <proyecto>-<servicio>-1
        self.assertIn("db-real", names)            # container_name explícito gana

    def test_timestamps_de_docker_salvo_el_ingress(self):
        # los legacies loggean sin fecha (WildFly); el prefijo RFC3339 de Docker se lo da.
        # El ingress queda puro: su stdout ES http.jsonl y ya trae ts propio.
        self._collect()
        calls = [json.loads(line) for line in self.calls_log.read_text().splitlines()]
        by_name = {call[-1]: call for call in calls}
        self.assertNotIn("--timestamps", by_name["demo-ingress-1"])
        self.assertIn("--timestamps", by_name["db-real"])
        self.assertIn("--timestamps", by_name["demo-app-1"])

    def test_avisa_si_la_ventana_aun_no_termina(self):
        # docker logs solo devuelve lo ya emitido: correr antes de end+margen deja
        # la captura incompleta en silencio (pasó en la primera corrida real).
        future_end = datetime.now(TZ) + timedelta(seconds=45)
        summary = collect(COMPOSE, "flow-futuro", START, future_end, self.out,
                          docker_bin=str(self.docker))
        warnings = [item["warning"] for item in summary if "warning" in item]
        self.assertEqual(len(warnings), 1)
        self.assertIn("vuelve a correr collect", warnings[0])

    def test_sin_aviso_cuando_la_ventana_ya_paso(self):
        summary = self._collect()
        self.assertFalse(any("warning" in item for item in summary))

    def test_no_sobrescribe_una_sesion(self):
        self._collect()
        with self.assertRaises(FileExistsError):
            self._collect()

    def test_session_id_con_ruta_falla(self):
        # "../escape" escribía fuera de evidence/ (auditoría H-02)
        for malo in ("../escape", "a/b", "/abs", ".oculto"):
            with self.assertRaises(ValueError, msg=malo):
                collect(COMPOSE, malo, START, END, self.out, docker_bin=str(self.docker))

    def test_ventana_sin_zona_horaria_falla_claro(self):
        naive = datetime(2026, 9, 2, 10, 0, 0)
        with self.assertRaises(ValueError):
            collect(COMPOSE, "flow-002", naive, END, self.out, docker_bin=str(self.docker))


if __name__ == "__main__":
    unittest.main()
