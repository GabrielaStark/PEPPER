"""Herramientas del núcleo para los agentes: `pepper detect` y `pepper validate`."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pepper.detect import detect  # noqa: E402
from pepper.validate import guess_schema, validate_file  # noqa: E402

FIXTURE = ROOT / "examples" / "legacy-demo"

try:
    import jsonschema  # noqa: F401
except ImportError:  # pragma: no cover
    jsonschema = None


class DetectTest(unittest.TestCase):
    def test_fixture_artifacts_match_the_java_profile(self):
        results = detect(FIXTURE / "artifacts")
        [java] = [r for r in results if r["profile_id"] == "java-wildfly-postgres"]
        self.assertTrue(java["applicable"], java)
        hits = {m["pattern"] for m in java["matches"]}
        self.assertIn("pom.xml", hits)
        self.assertIn("standalone*.xml", hits)
        self.assertIn("jdbc:postgresql", hits)
        self.assertNotIn("*.war", hits, "el fixture trae código, no un WAR")

    def test_unrelated_artifacts_do_not_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "index.php").write_text("<?php echo 'hola';", encoding="utf-8")
            (root / "composer.json").write_text("{}", encoding="utf-8")
            results = detect(root)
            self.assertFalse(any(r["applicable"] for r in results))

    def test_un_artefacto_se_reconoce_por_su_contenido_no_por_su_extension(self):
        """`app.jar.original` es lo que el plugin de Spring Boot deja junto al jar real,
        y es un artefacto como cualquier otro. Decidir "¿es un archivo comprimido?" por la
        extensión lo volvía invisible: detect no lo abría, no veía su configuración y
        concluía "ningún perfil cubre este stack" sin haber mirado nada (2026-09-15).
        """
        import zipfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("servicio-web-1.0-SNAPSHOT.jar.original", "front", "respaldo-sin-extension"):
                with zipfile.ZipFile(root / name, "w") as z:
                    z.writestr("application.yml",
                               "spring:\n  datasource:\n    url: jdbc:postgresql://10.0.0.2:5432/negocio\n")
            results = detect(root)
            hits = {m["pattern"]: m["hit"] for r in results for m in r["matches"]}
            self.assertIn("jdbc:postgresql", hits,
                          "no abrió ningún artefacto: decide por extensión, no por contenido")
            self.assertIn("!application.yml", hits["jdbc:postgresql"])

    def test_un_perfil_de_war_no_aplica_a_un_sistema_de_jars(self):
        """Tres JARs y un dist comparten con un WAR casi todas las señales (pom.xml,
        application.yml, jdbc:postgresql) y sumaban 8 sobre un mínimo de 4: el perfil
        de WAR "aplicaba" a un sistema que no tiene ninguno, y rehydrate reventaba
        después. La señal que define al stack va marcada `required`.
        """
        import zipfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("api-core.jar", "batch.jar"):
                with zipfile.ZipFile(root / name, "w") as z:
                    z.writestr("BOOT-INF/classes/application.yml",
                               "spring:\n  datasource:\n    url: jdbc:postgresql://10.0.0.2:5432/negocio\n")
                    z.writestr("BOOT-INF/classes/META-INF/maven/org/app/pom.xml",
                               "<project><parent><artifactId>spring-boot-starter-parent</artifactId></parent></project>")
            (root / "dist").mkdir()
            (root / "dist" / "index.html").write_text("<html></html>", encoding="utf-8")
            (root / "respaldo.dump").write_bytes(b"PGDMP")

            results = detect(root)
            for result in results:
                self.assertFalse(result["applicable"],
                                 f"{result['profile_id']} dice aplicar a un sistema sin WAR: {result}")
            # el que sumaba de más era el de springboot: 8 sobre un mínimo de 4, sin un solo WAR.
            [springboot] = [r for r in results if r["profile_id"] == "java-springboot-jsf-postgres"]
            self.assertGreaterEqual(springboot["score"], springboot["min_score"],
                                    "la prueba no vale si el puntaje no llegaba al mínimo")
            self.assertEqual(springboot["missing_required"], ["extension '*.war'"], springboot)

    def test_tool_dirs_are_ignored_when_pepper_sits_on_top_of_the_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude" / "commands").mkdir(parents=True)
            (root / ".claude" / "commands" / "pepper.md").write_text("---\ndescription: x\n---\n", encoding="utf-8")
            (root / "examples" / "demo").mkdir(parents=True)
            (root / "examples" / "demo" / "pom.xml").write_text("<project/>", encoding="utf-8")
            (root / "examples" / "demo" / "standalone.xml").write_text("urn:jboss:domain", encoding="utf-8")
            (root / "index.php").write_text("<?php", encoding="utf-8")
            results = detect(root)
            self.assertFalse(any(r["applicable"] for r in results), "el fixture de la herramienta no es el legacy")

    def test_signals_are_found_inside_deployable_archives(self):
        import zipfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with zipfile.ZipFile(root / "app.war", "w") as war:
                war.writestr("META-INF/maven/gob/app/pom.xml", "<parent><artifactId>spring-boot-starter-parent</artifactId></parent>")
                war.writestr("WEB-INF/classes/application-prod.yml", "spring:\n  datasource:\n    url: jdbc:postgresql://db:5432/x\n")
                war.writestr("WEB-INF/jboss-web.xml", "<jboss-web/>")
            (root / "respaldo.dump").write_bytes(b"PGDMP")
            results = detect(root)
            [spring] = [r for r in results if r["profile_id"] == "java-springboot-jsf-postgres"]
            self.assertTrue(spring["applicable"], spring)
            hits = {m["hit"] for m in spring["matches"]}
            self.assertIn("app.war!META-INF/maven/gob/app/pom.xml", hits)
            self.assertIn("app.war!WEB-INF/classes/application-prod.yml", hits)
            self.assertIn("app.war!WEB-INF/jboss-web.xml", hits)

    def test_signals_are_found_inside_tarballs(self):
        # La herramienta es para CUALQUIER legacy: un dist entregado como tar.gz
        # (PHP, Node, binarios sueltos) se inspecciona igual que un WAR.
        import tarfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = root / "_payload"
            payload.mkdir()
            inner = payload / "META-INF/maven/gob/app"
            inner.mkdir(parents=True)
            (inner / "pom.xml").write_text(
                "<parent><artifactId>spring-boot-starter-parent</artifactId></parent>", encoding="utf-8")
            with tarfile.open(root / "dist.tar.gz", "w:gz") as tar:
                tar.add(inner / "pom.xml", arcname="META-INF/maven/gob/app/pom.xml")
            import shutil
            shutil.rmtree(payload)
            results = detect(root)
            [spring] = [r for r in results if r["profile_id"] == "java-springboot-jsf-postgres"]
            hits = {m["hit"] for m in spring["matches"]}
            self.assertIn("dist.tar.gz!META-INF/maven/gob/app/pom.xml", hits)

    def test_missing_directory_fails_clearly(self):
        with self.assertRaises(FileNotFoundError):
            detect(Path("/no/existe"))


class ValidateTest(unittest.TestCase):
    def setUp(self):
        if jsonschema is None:
            self.skipTest("jsonschema no instalado")

    def test_guesses_schema_from_filename(self):
        self.assertEqual(guess_schema(Path("x/profile.json")), "profile")
        self.assertEqual(guess_schema(Path("x/parsers/wildfly.json")), "parser")
        self.assertEqual(guess_schema(Path("x/events.jsonl")), "event")
        self.assertIsNone(guess_schema(Path("x/cualquier.json")))

    def test_repo_instances_are_valid(self):
        files = [
            ROOT / "profiles" / "java-wildfly-postgres" / "profile.json",
            *sorted((ROOT / "profiles" / "java-wildfly-postgres" / "parsers").glob("*.json")),
            FIXTURE / "raw-evidence" / "session.json",
            FIXTURE / "expected" / "funcional.json",
        ]
        for path in files:
            self.assertEqual(validate_file(path), [], path)

    def test_invalid_profile_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile.json"
            path.write_text(json.dumps({"id": "MAL", "name": "x"}), encoding="utf-8")
            errors = validate_file(path)
            self.assertTrue(errors)
            self.assertTrue(any("required" in e or "pattern" in e for e in errors), errors)

    def test_unknown_filename_requires_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cosa.json"
            path.write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                validate_file(path)
            self.assertTrue(validate_file(path, "profile"))


if __name__ == "__main__":
    unittest.main()


class RepoDeLaHerramientaTest(unittest.TestCase):
    """El repo de la herramienta no puede contaminarse con el sistema que alguien analice.

    Quien usa PEPPER clona este repo y trabaja adentro: si `.gitignore` no cubriera el
    producto, un `git add -A && git push` publicaría el mapa, el entorno y el funcional.md
    del sistema de un cliente en un repositorio público. Lo que protegía esas rutas era
    `.git/info/exclude`, que NO viaja en el clon (auditoría 2026-09-14).
    """

    RUTAS = [
        ("legacy/sistema.war", "artefacto del sistema"),
        ("legacy/respaldo.dump", "respaldo de la base"),
        ("legacy/NOTAS.md", "notas del humano"),
        ("evidence/explore-001/http.jsonl", "evidencia capturada"),
        ("pepper-out/rehydrate/.env", "compose y credenciales"),
        ("pepper-out/explore.json", "claves de usuario y contraseña de prueba"),
        ("docs/pepper/funcional.md", "qué hace el sistema del cliente"),
        ("docs/pepper/system-map.json", "mapa del sistema del cliente"),
        ("docs/pepper/map/catalogs.md", "catálogos del cliente"),
        ("docs/analysis/funcional.md", "la entrega a stark"),
    ]

    def test_gitignore_bloquea_todo_lo_del_legacy(self):
        if shutil.which("git") is None:
            self.skipTest("git no disponible")
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            shutil.copy2(ROOT / ".gitignore", repo / ".gitignore")
            for rel, _ in self.RUTAS:
                path = repo / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("contenido del legacy", encoding="utf-8")
            listo = subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                                   capture_output=True, text=True, check=True).stdout
            visibles = [line for line in listo.splitlines() if ".gitignore" not in line]
            self.assertEqual(visibles, [], f"esto se subiría al repo de la herramienta: {visibles}")

    def test_cada_ruta_por_separado_esta_ignorada(self):
        if shutil.which("git") is None:
            self.skipTest("git no disponible")
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            shutil.copy2(ROOT / ".gitignore", repo / ".gitignore")
            for rel, que_es in self.RUTAS:
                path = repo / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x", encoding="utf-8")
                ignorado = subprocess.run(["git", "check-ignore", "-q", rel], cwd=repo).returncode == 0
                self.assertTrue(ignorado, f"{rel} ({que_es}) NO está ignorado")

    def test_el_nucleo_avisa_si_el_remoto_de_la_herramienta_sigue_puesto(self):
        from pepper.cli import tool_remote_warning

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            config = root / ".git" / "config"

            # sin remoto de la herramienta: nada que decir
            config.write_text('[remote "origin"]\n\turl = git@github.com:cliente/su-sistema.git\n', encoding="utf-8")
            (root / "legacy").mkdir()
            (root / "legacy" / "sistema.war").write_text("x", encoding="utf-8")
            self.assertIsNone(tool_remote_warning(root))

            # remoto de la herramienta + legacy dentro: avisa y dice cómo cerrarlo
            config.write_text('[remote "origin"]\n\turl = https://github.com/GabrielaStark/PEPPER.git\n', encoding="utf-8")
            aviso = tool_remote_warning(root)
            self.assertIsNotNone(aviso)
            self.assertIn("git remote remove origin", aviso)

    def test_no_avisa_mientras_se_desarrolla_la_herramienta(self):
        from pepper.cli import tool_remote_warning

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / ".git" / "config").write_text(
                '[remote "origin"]\n\turl = https://github.com/GabrielaStark/PEPPER.git\n', encoding="utf-8")
            self.assertIsNone(tool_remote_warning(root), "sin legacy/ no hay nada que filtrar")
            (root / "legacy").mkdir()
            self.assertIsNone(tool_remote_warning(root), "legacy/ vacío tampoco")
