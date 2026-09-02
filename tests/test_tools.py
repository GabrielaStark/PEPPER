"""Herramientas del núcleo para los agentes: `pepper detect` y `pepper validate`."""

import json
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

    def test_tool_dirs_are_ignored_when_pepper_sits_on_top_of_the_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude" / "commands").mkdir(parents=True)
            (root / ".claude" / "commands" / "pepper-init.md").write_text("---\ndescription: x\n---\n", encoding="utf-8")
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
            FIXTURE / "expected" / "runtime-discovery.json",
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
