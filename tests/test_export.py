"""Package + Export contra el fixture: la salida de referencia pasa; las salidas rotas se rechazan."""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pepper.correlate import run  # noqa: E402
from pepper.export import publish, validate  # noqa: E402
from pepper.package import assemble  # noqa: E402

FIXTURE = ROOT / "examples" / "legacy-demo"
GOLDEN = FIXTURE / "expected" / "funcional.json"
GOLDEN_MD = FIXTURE / "expected" / "funcional.md"

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None


class ExportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        run(FIXTURE / "raw-evidence", base / "correlated")
        self.package = base / "package"
        # el fixture trae credenciales de juguete a propósito: la excepción es explícita (D24)
        summary = assemble(base / "correlated", self.package, FIXTURE / "artifacts", allow_sensitive=True)
        self.manifest = Path(summary["external_manifest"])
        self.output = self.package / "output" / "funcional.json"
        shutil.copy2(GOLDEN, self.output)
        shutil.copy2(GOLDEN_MD, self.package / "output" / "funcional.md")

    def tearDown(self):
        self.tmp.cleanup()

    def _rewrite(self, mutate):
        discovery = json.loads(self.output.read_text(encoding="utf-8"))
        mutate(discovery)
        self.output.write_text(json.dumps(discovery, ensure_ascii=False), encoding="utf-8")

    def test_package_layout(self):
        for name in ("README.md", "CLAUDE.md", "AGENTS.md", "prompt.md",
                     "session.json", "evidence/events.jsonl", "evidence/flow.json", "evidence/flow.md",
                     "evidence/reduction.md", "evidence/raw/http.jsonl", "legacy/source/pom.xml",
                     "legacy/docs/manual-tecnico.md", "schemas/functional-discovery.schema.json"):
            self.assertTrue((self.package / name).is_file(), name)
        self.assertTrue((self.package / "output").is_dir())
        self.assertFalse((self.package / "map").exists(), "sin --map no hay carpeta map/")
        prompt = (self.package / "prompt.md").read_text(encoding="utf-8")
        self.assertFalse(prompt.startswith("---"), "el prompt viaja sin frontmatter")
        self.assertIn("## 12. Lo que no sé", prompt)
        self.assertIn("No hay mapa del sistema", (self.package / "CLAUDE.md").read_text(encoding="utf-8"))

    def test_package_with_map_and_previous(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp) / "docs"
            docs.mkdir()
            system_map = {"schema_version": "0.2.0", "profile_id": None, "artifact": {"name": "x.war"},
                          "complete": True, "coverage_gaps": [], "entrypoints": [], "jobs": [],
                          "external_dependencies": [], "data_stores": [], "catalogs": [], "distributions": [],
                          "classes": [{"name": "app.Ctl", "kind": "pantalla", "methods": ["guardar()"],
                                       "constants": {}, "strings": [], "evidence": "x"}],
                          "screens": [], "labels": 0, "notes": []}
            (docs / "system-map.json").write_text(json.dumps(system_map), encoding="utf-8")
            shutil.copy2(GOLDEN, docs / "funcional.json")
            out = Path(tmp) / "package"
            summary = assemble(Path(self.tmp.name) / "correlated", out, None, system_map=docs / "system-map.json",
                               previous=docs / "funcional.json")
            self.assertIn("1 clases", summary["map"])
            for name in ("map/system-map.json", "map/surface.md", "map/code.md", "map/screens.md",
                         "map/db.md", "map/catalogs.md", "previous/funcional.json"):
                self.assertTrue((out / name).is_file(), name)
            manifest = json.loads((out / "evidence-manifest.json").read_text(encoding="utf-8"))
            self.assertIn("map/system-map.json", manifest["files"], "el mapa queda amarrado por hash")
            self.assertIn("previous/funcional.json", manifest["files"])
            self.assertIn("extiéndelo", (out / "CLAUDE.md").read_text(encoding="utf-8"))
            # una fuente que cita el mapa se verifica contra él
            shutil.copy2(GOLDEN, out / "output" / "funcional.json")
            shutil.copy2(GOLDEN_MD, out / "output" / "funcional.md")
            discovery = json.loads((out / "output" / "funcional.json").read_text(encoding="utf-8"))
            discovery["sources"].append({"id": "S-900", "kind": "en_codigo", "ref": "map:classes:Ctl"})
            discovery["sources"].append({"id": "S-901", "kind": "en_codigo", "ref": "map:classes:NoExiste"})
            discovery["rules"][0]["sources"] += ["S-900", "S-901"]
            (out / "output" / "funcional.json").write_text(json.dumps(discovery), encoding="utf-8")
            _, report = validate(out, Path(summary["external_manifest"]))
            self.assertTrue(any("S-901" in e and "no existe en map" in e for e in report.errors), report.errors)
            self.assertFalse(any("S-900" in e for e in report.errors), report.errors)

    def test_check_validates_without_publishing(self):
        from pepper.export import check

        report = check(self.package, self.manifest)
        self.assertTrue(report.ok, report.errors)
        self.assertTrue((self.package / "output" / "validation.md").is_file())
        self.assertFalse((Path(self.tmp.name) / "export").exists())

    def test_package_excludes_the_tool_when_pepper_sits_on_top_of_the_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "proyecto"
            for rel in (".claude/commands", "pepper", "docs/documentacion", "docs/pepper", "src", "pepper-out"):
                (project / rel).mkdir(parents=True)
            (project / ".claude" / "commands" / "pepper-init.md").write_text("---\ndescription: x\n---\n", encoding="utf-8")
            (project / "src" / "App.java").write_text("class App {}", encoding="utf-8")
            (project / "docs" / "manual.md").write_text("# manual", encoding="utf-8")
            (project / "docs" / "documentacion" / "PRINCIPIOS.md").write_text("# tool", encoding="utf-8")
            (project / "docs" / "pepper" / "stack-report.md").write_text("# report", encoding="utf-8")
            out = Path(tmp) / "package"
            summary = assemble(Path(self.tmp.name) / "correlated", out, project)
            self.assertEqual(summary["legacy"], ["docs/", "src/"])
            self.assertTrue((out / "legacy" / "src" / "App.java").is_file())
            self.assertTrue((out / "legacy" / "docs" / "manual.md").is_file())
            self.assertFalse((out / "legacy" / "docs" / "documentacion").exists())
            self.assertFalse((out / "legacy" / "docs" / "pepper").exists())
            self.assertFalse((out / "legacy" / "pepper").exists())

    def test_package_refuses_to_overwrite(self):
        with self.assertRaises(FileExistsError):
            assemble(Path(self.tmp.name) / "correlated", self.package, None)

    def test_golden_output_is_accepted(self):
        discovery, report = validate(self.package, self.manifest)
        self.assertIsNotNone(discovery)
        self.assertEqual(report.errors, [])
        self.assertEqual(report.stats["rules"], 4)
        self.assertEqual(report.stats["journeys"], 1)

    def test_publish_writes_session_and_system_documents(self):
        out = Path(self.tmp.name) / "export"
        system = Path(self.tmp.name) / "docs"
        report = publish(self.package, out, self.manifest, system_doc_dir=system)
        self.assertTrue(report.ok, report.errors)
        for name in ("funcional.json", "funcional.md", "validation.md"):
            self.assertTrue((out / name).is_file(), name)
        self.assertEqual((system / "funcional.md").read_bytes(), GOLDEN_MD.read_bytes())
        self.assertTrue((system / "funcional.json").is_file())

    def test_unresolved_source_reference_is_rejected(self):
        self._rewrite(lambda d: d["rules"][0]["sources"].append("S-999"))
        _, report = validate(self.package, self.manifest)
        self.assertTrue(any("S-999" in error for error in report.errors))
        out = Path(self.tmp.name) / "export"
        publish(self.package, out, self.manifest)
        self.assertFalse(out.exists(), "una salida inválida no se publica")
        self.assertTrue((self.package / "output" / "validation.md").is_file())

    def test_observed_source_must_resolve(self):
        self._rewrite(lambda d: d["sources"][0].__setitem__("ref", "http.jsonl:999"))
        _, report = validate(self.package, self.manifest)
        self.assertTrue(any("fuera de rango" in error for error in report.errors))
        self._rewrite(lambda d: d["sources"][0].__setitem__("ref", "E-999"))
        _, report = validate(self.package, self.manifest)
        self.assertTrue(any("E-999" in error for error in report.errors), report.errors)

    def test_observed_source_from_a_previous_session_is_trusted_if_declared(self):
        # el documento es acumulativo: lo observado en otra sesión ya se verificó al exportarla
        def add(d):
            d["sessions"].append({"session_id": "flow-000", "flow_name": "anterior"})
            d["sources"].append({"id": "S-800", "kind": "observado", "ref": "E-999", "session_id": "flow-000"})
            d["sources"].append({"id": "S-801", "kind": "observado", "ref": "E-999", "session_id": "flow-nunca"})
            d["rules"][0]["sources"] += ["S-800", "S-801"]
        self._rewrite(add)
        _, report = validate(self.package, self.manifest)
        self.assertFalse(any("S-800" in e for e in report.errors), report.errors)
        self.assertTrue(any("S-801" in e and "no está declarada" in e for e in report.errors), report.errors)

    def test_code_source_must_exist_in_package(self):
        self._rewrite(lambda d: d["sources"][-1].__setitem__("ref", "legacy/source/NoExiste.java"))
        _, report = validate(self.package, self.manifest)
        self.assertTrue(any("no resuelve" in error for error in report.errors), report.errors)

    def test_claim_without_sources_is_rejected(self):
        if jsonschema is None:
            self.skipTest("jsonschema no instalado")
        self._rewrite(lambda d: d["rules"][0].__setitem__("sources", []))
        _, report = validate(self.package, self.manifest)
        self.assertTrue(any("rules/0/sources" in error for error in report.errors))

    def test_confidence_outside_vocabulary_is_rejected(self):
        if jsonschema is None:
            self.skipTest("jsonschema no instalado")
        self._rewrite(lambda d: d["rules"][0].__setitem__("confidence", "alta"))
        _, report = validate(self.package, self.manifest)
        self.assertTrue(any("confidence" in error for error in report.errors))

    def test_missing_session_is_rejected(self):
        self._rewrite(lambda d: d["sessions"][0].__setitem__("session_id", "otra"))
        _, report = validate(self.package, self.manifest)
        self.assertTrue(any("sessions" in error for error in report.errors))

    def test_empty_unknowns_is_rejected(self):
        self._rewrite(lambda d: d.__setitem__("unknowns", []))
        _, report = validate(self.package, self.manifest)
        self.assertTrue(any("unknowns" in error for error in report.errors))

    def test_missing_md_is_rejected(self):
        (self.package / "output" / "funcional.md").unlink()
        _, report = validate(self.package, self.manifest)
        self.assertTrue(any("funcional.md" in error for error in report.errors))

    def test_missing_output_is_reported(self):
        self.output.unlink()
        discovery, report = validate(self.package, self.manifest)
        self.assertIsNone(discovery)
        self.assertTrue(any("no existe" in error for error in report.errors))


if __name__ == "__main__":
    unittest.main()
