"""El guardia de datos (scripts/guardia_datos.py): el agente no lee el legacy ni la evidencia cruda."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARDIA = ROOT / "scripts" / "guardia_datos.py"


class GuardiaDatosTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        # el hook importa pepper.sensitive del workspace: el workspace ES una copia de la herramienta
        (self.project / "pepper").symlink_to(ROOT / "pepper")
        (self.project / "legacy").mkdir()
        (self.project / "legacy" / "sistema.war").write_bytes(b"PK\x03\x04\x00\x00")
        (self.project / "legacy" / "respaldo.sql").write_text("INSERT INTO persona VALUES ('GOCG950101MDFRRB09');\n", encoding="utf-8")
        (self.project / "legacy" / "pom.xml").write_text("<project><properties><java.version>8</java.version></properties></project>\n", encoding="utf-8")
        (self.project / "legacy" / "application.yml").write_text("spring:\n  datasource:\n    password: SecretoProd123\n", encoding="utf-8")
        (self.project / "legacy" / "NOTAS.md").write_text("# Notas\nproducción es WildFly 21\n", encoding="utf-8")
        for rel in ("evidence/explore-001/screens", "evidence/explore-001/containers", "pepper-out/explore-001/correlated/raw",
                    "pepper-out/explore-001/package/evidence", "pepper-out/rehydrate", "docs/pepper"):
            (self.project / rel).mkdir(parents=True)
        (self.project / "evidence/explore-001/http.jsonl").write_text("{}\n", encoding="utf-8")
        (self.project / "evidence/explore-001/explore.jsonl").write_text("{}\n", encoding="utf-8")
        (self.project / "evidence/explore-001/screens/001.png").write_bytes(b"\x89PNG")
        (self.project / "pepper-out/rehydrate/.env").write_text('DB_PASSWORD="x"\n', encoding="utf-8")
        (self.project / "pepper-out/rehydrate/docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
        (self.project / "pepper-out/data-boundary.json").write_text("{}\n", encoding="utf-8")
        (self.project / "pepper-out/explore-001/package/evidence/flow.md").write_text("# flujo\n", encoding="utf-8")
        (self.project / "docs/pepper/system-map.json").write_text("{}\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def run_hook(self, tool, payload, env=None):
        event = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": payload, "cwd": str(self.project)}
        environment = dict(os.environ, CLAUDE_PROJECT_DIR=str(self.project))
        environment.pop("PEPPER_GUARDIA_DEV", None)
        environment.update(env or {})
        result = subprocess.run([sys.executable, str(GUARDIA)], input=json.dumps(event), capture_output=True, text=True,
                                cwd=str(self.project), env=environment)
        return result.returncode, result.stderr

    def assertBlocked(self, tool, payload, fragment=None):
        code, stderr = self.run_hook(tool, payload)
        self.assertEqual(code, 2, f"{tool} {payload} debió bloquearse; stderr: {stderr}")
        self.assertIn("guardia de datos", stderr)
        if fragment:
            self.assertIn(fragment, stderr)

    def assertAllowed(self, tool, payload):
        code, stderr = self.run_hook(tool, payload)
        self.assertEqual(code, 0, f"{tool} {payload} debió pasar; stderr: {stderr}")

    # ---- Bash
    def test_bash_no_lee_el_legacy(self):
        for command in ("cat legacy/respaldo.sql", "head -n 5 legacy/application.yml", "grep -r password legacy/",
                        "strings legacy/sistema.war", "cp legacy/respaldo.sql /tmp/x", "python3 -c \"print(open('legacy/respaldo.sql').read())\"",
                        "cat < legacy/respaldo.sql", "sed -n 1,5p ./legacy/NOTAS.md", "unzip -p legacy/sistema.war WEB-INF/web.xml",
                        "tar xzf legacy/x.tgz", "find legacy -name '*.sql' -exec cat {} \\;", "cd legacy && cat respaldo.sql",
                        f"cat {self.project}/legacy/respaldo.sql", "cat pepper-out/../legacy/respaldo.sql"):
            with self.subTest(command=command):
                self.assertBlocked("Bash", {"command": command})

    def test_bash_si_lista_el_legacy(self):
        for command in ("ls -la legacy/", "find legacy -type f", "unzip -l legacy/sistema.war", "wc -l legacy/respaldo.sql",
                        "sha256sum legacy/sistema.war", "file legacy/sistema.war", "du -sh legacy", "stat legacy/respaldo.sql"):
            with self.subTest(command=command):
                self.assertAllowed("Bash", {"command": command})

    def test_bash_el_nucleo_pasa(self):
        for command in ("python3 -m pepper map legacy/sistema.war --profile x --dump legacy/respaldo.sql",
                        "python3 -m pepper rehydrate legacy/ --profile x --up",
                        "python3 -m pepper package pepper-out/explore-001/correlated --legacy legacy/ --authorization pepper-out/data-boundary.json --out p",
                        "docker compose -f pepper-out/rehydrate/docker-compose.yml up -d",
                        "docker compose -f pepper-out/rehydrate/docker-compose.yml logs app",
                        "docker compose -f pepper-out/rehydrate/docker-compose.yml down -v"):
            with self.subTest(command=command):
                self.assertAllowed("Bash", {"command": command})

    def test_bash_no_abre_la_base_desechable(self):
        for command in ("docker compose -f pepper-out/rehydrate/docker-compose.yml exec db psql -U app -d base -Atc 'select 1'",
                        "docker compose -f pepper-out/rehydrate/docker-compose.yml logs db",
                        "docker exec rehydrate-db-1 mysql -e 'select 1'", "docker exec app-1 psql", "psql -h 127.0.0.1",
                        "docker cp rehydrate-app-1:/var/log/x.log ."):
            with self.subTest(command=command):
                self.assertBlocked("Bash", {"command": command})

    def test_bash_no_abre_evidencia_cruda_ni_credenciales_rendidas(self):
        for command in ("cat evidence/explore-001/http.jsonl", "cat pepper-out/rehydrate/.env",
                        "cat pepper-out/rehydrate/docker-compose.yml", "ls evidence/explore-001/screens && cat evidence/explore-001/screens/001.png",
                        "cat pepper-out/explore-001/correlated/events.jsonl", "cat pepper-out/data-boundary.json"):
            with self.subTest(command=command):
                self.assertBlocked("Bash", {"command": command})

    def test_bash_si_lee_lo_declarado(self):
        for command in ("cat evidence/explore-001/explore.jsonl", "cat evidence/explore-001/session.json",
                        "cat pepper-out/explore-001/package/evidence/flow.md", "cat docs/pepper/system-map.json",
                        "grep -rn 'def ' pepper/", "git status", "ls"):
            with self.subTest(command=command):
                self.assertAllowed("Bash", {"command": command})

    def test_bash_no_escribe_en_el_legacy_ni_la_autorizacion(self):
        for command in ("echo x > legacy/NOTAS.md", "rm legacy/respaldo.sql", "touch legacy/x", "cp a.json pepper-out/data-boundary.json",
                        "echo '{}' > pepper-out/data-boundary.json", "rm pepper-out/data-boundary.abc.seudonimos.key"):
            with self.subTest(command=command):
                self.assertBlocked("Bash", {"command": command})

    def test_bash_comando_ininterpretable_que_menciona_legacy_bloquea(self):
        self.assertBlocked("Bash", {"command": "cat 'legacy/respaldo.sql"})   # comilla sin cerrar

    def test_bash_no_apaga_al_guardia(self):
        self.assertBlocked("Bash", {"command": "rm .claude/settings.json"})
        self.assertBlocked("Bash", {"command": "echo '{}' > .claude/settings.json"})
        self.assertBlocked("Bash", {"command": "sed -i 's/x/y/' scripts/guardia_datos.py"})
        code, _ = self.run_hook("Bash", {"command": "rm .claude/settings.json"}, env={"PEPPER_GUARDIA_DEV": "1"})
        self.assertEqual(code, 0)   # solo quien lanza el proceso puede levantar la veda

    # ---- Read / Grep / Glob
    def test_read_del_legacy_solo_texto_limpio(self):
        self.assertAllowed("Read", {"file_path": str(self.project / "legacy" / "pom.xml")})
        self.assertAllowed("Read", {"file_path": "legacy/NOTAS.md"})
        self.assertBlocked("Read", {"file_path": "legacy/application.yml"}, "credential")
        self.assertBlocked("Read", {"file_path": "legacy/respaldo.sql"}, "respaldo")
        self.assertBlocked("Read", {"file_path": "legacy/sistema.war"})

    def test_read_de_evidencia_cruda_bloquea(self):
        self.assertBlocked("Read", {"file_path": "evidence/explore-001/http.jsonl"})
        self.assertBlocked("Read", {"file_path": "evidence/explore-001/screens/001.png"})
        self.assertBlocked("Read", {"file_path": "pepper-out/rehydrate/.env"})
        self.assertBlocked("Read", {"file_path": "pepper-out/rehydrate/docker-compose.yml"})
        self.assertBlocked("Read", {"file_path": "pepper-out/explore-001/correlated/raw/x.log"})
        self.assertAllowed("Read", {"file_path": "evidence/explore-001/explore.jsonl"})
        self.assertAllowed("Read", {"file_path": "pepper-out/explore-001/package/evidence/flow.md"})
        self.assertAllowed("Read", {"file_path": "/etc/hostname"})

    def test_grep_acotado(self):
        self.assertBlocked("Grep", {"pattern": "password", "path": "legacy"})
        self.assertBlocked("Grep", {"pattern": "x", "path": "evidence/explore-001/containers"})
        self.assertBlocked("Grep", {"pattern": "x"})                       # todo el workspace con legacy/ presente
        self.assertBlocked("Grep", {"pattern": "x", "path": str(self.project)})
        self.assertAllowed("Grep", {"pattern": "x", "path": "docs/pepper"})
        self.assertAllowed("Grep", {"pattern": "x", "path": "pepper-out/explore-001/package"})
        self.assertAllowed("Glob", {"pattern": "legacy/**"})

    def test_grep_sobre_todo_pasa_sin_legacy_presente(self):
        import shutil
        shutil.rmtree(self.project / "legacy")
        shutil.rmtree(self.project / "evidence")
        self.assertAllowed("Grep", {"pattern": "x"})

    # ---- Edit / Write
    def test_no_se_escribe_en_el_legacy_ni_en_la_autorizacion_ni_en_el_guardia(self):
        self.assertBlocked("Write", {"file_path": "legacy/NOTAS.md", "content": "x"})
        self.assertBlocked("Edit", {"file_path": "legacy/pom.xml", "old_string": "a", "new_string": "b"})
        self.assertBlocked("Write", {"file_path": "pepper-out/data-boundary.json", "content": "{}"})
        self.assertBlocked("Write", {"file_path": ".claude/settings.json", "content": "{}"})
        self.assertBlocked("Edit", {"file_path": "scripts/guardia_datos.py", "old_string": "a", "new_string": "b"})
        self.assertAllowed("Write", {"file_path": "pepper-out/explore.json", "content": "{}"})
        self.assertAllowed("Write", {"file_path": "docs/pepper/funcional.md", "content": "x"})
        code, _ = self.run_hook("Write", {"file_path": ".claude/settings.json", "content": "{}"}, env={"PEPPER_GUARDIA_DEV": "1"})
        self.assertEqual(code, 0)

    def test_entrada_rota_bloquea(self):
        result = subprocess.run([sys.executable, str(GUARDIA)], input="esto no es json", capture_output=True, text=True,
                                cwd=str(self.project), env=dict(os.environ, CLAUDE_PROJECT_DIR=str(self.project)))
        self.assertEqual(result.returncode, 2)

    def test_settings_declara_el_hook_y_las_vedas(self):
        settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
        hooks = settings["hooks"]["PreToolUse"]
        self.assertTrue(any("guardia_datos.py" in h["command"] for group in hooks for h in group["hooks"]))
        self.assertIn("Read(./legacy/**/*.sql)", settings["permissions"]["deny"])
        self.assertIn("Read(./pepper-out/rehydrate/.env)", settings["permissions"]["deny"])


if __name__ == "__main__":
    unittest.main()
