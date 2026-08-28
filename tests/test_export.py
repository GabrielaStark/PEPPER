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
GOLDEN = FIXTURE / "expected" / "runtime-discovery.json"

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
        assemble(base / "correlated", self.package, FIXTURE / "artifacts")
        self.output = self.package / "output" / "runtime-discovery.json"
        shutil.copy2(GOLDEN, self.output)
        (self.package / "output" / "runtime-discovery.md").write_text("# demo\n", encoding="utf-8")

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
                     "legacy/docs/manual-tecnico.md", "schemas/runtime-discovery.schema.json"):
            self.assertTrue((self.package / name).is_file(), name)
        self.assertTrue((self.package / "output").is_dir())
        prompt = (self.package / "prompt.md").read_text(encoding="utf-8")
        self.assertFalse(prompt.startswith("---"), "el prompt viaja sin frontmatter")
        self.assertIn("Comparación runtime ↔ código", prompt)
        self.assertIn("Comparación runtime ↔ código", (self.package / "CLAUDE.md").read_text(encoding="utf-8"))

    def test_check_validates_without_publishing(self):
        from pepper.export import check

        report = check(self.package)
        self.assertTrue(report.ok, report.errors)
        self.assertTrue((self.package / "output" / "validation.md").is_file())
        self.assertFalse((Path(self.tmp.name) / "export").exists())

    def test_package_refuses_to_overwrite(self):
        with self.assertRaises(FileExistsError):
            assemble(Path(self.tmp.name) / "correlated", self.package, None)

    def test_golden_output_is_accepted(self):
        discovery, report = validate(self.package)
        self.assertIsNotNone(discovery)
        self.assertEqual(report.errors, [])
        self.assertEqual(report.stats["candidate_rules"], 3)

    def test_publish_writes_the_contract_and_derived_files(self):
        out = Path(self.tmp.name) / "export"
        report = publish(self.package, out)
        self.assertTrue(report.ok, report.errors)
        for name in ("runtime-discovery.json", "runtime-discovery.md", "validation.md", "flows.json",
                     "candidate-rules.json", "contradictions.json", "unknowns.json", "evidence-map.json",
                     "evidence/events.jsonl", "evidence/flow.json"):
            self.assertTrue((out / name).is_file(), name)
        rules = json.loads((out / "candidate-rules.json").read_text(encoding="utf-8"))
        self.assertEqual(len(rules["candidate_rules"]), 3)

    def test_unresolved_evidence_reference_is_rejected(self):
        self._rewrite(lambda d: d["candidate_rules"][0]["evidence"].append("E-999"))
        _, report = validate(self.package)
        self.assertTrue(any("E-999" in error for error in report.errors))
        out = Path(self.tmp.name) / "export"
        publish(self.package, out)
        self.assertFalse(out.exists(), "una salida inválida no se publica")
        self.assertTrue((self.package / "output" / "validation.md").is_file())

    def test_raw_ref_out_of_range_is_rejected(self):
        self._rewrite(lambda d: d["evidence"][0].__setitem__("raw_ref", "http.jsonl:999"))
        _, report = validate(self.package)
        self.assertTrue(any("fuera de rango" in error for error in report.errors))

    def test_conclusion_without_evidence_is_rejected(self):
        if jsonschema is None:
            self.skipTest("jsonschema no instalado")
        self._rewrite(lambda d: d["candidate_rules"][0].__setitem__("evidence", []))
        _, report = validate(self.package)
        self.assertTrue(any("candidate_rules/0/evidence" in error for error in report.errors))

    def test_confidence_outside_vocabulary_is_rejected(self):
        if jsonschema is None:
            self.skipTest("jsonschema no instalado")
        self._rewrite(lambda d: d["candidate_rules"][0].__setitem__("confidence", "alta"))
        _, report = validate(self.package)
        self.assertTrue(any("confidence" in error for error in report.errors))

    def test_wrong_session_is_rejected(self):
        self._rewrite(lambda d: d["flow"].__setitem__("session_id", "otra"))
        _, report = validate(self.package)
        self.assertTrue(any("session_id" in error for error in report.errors))

    def test_missing_output_is_reported(self):
        self.output.unlink()
        discovery, report = validate(self.package)
        self.assertIsNone(discovery)
        self.assertEqual(len(report.errors), 1)


if __name__ == "__main__":
    unittest.main()
