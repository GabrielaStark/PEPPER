"""Correlate contra el fixture legacy-demo: reducción, protección de evidencia, correlación y determinismo."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pepper.correlate import MissingParsers, run  # noqa: E402
from pepper.correlate.events import read_jsonl  # noqa: E402

FIXTURE = ROOT / "examples" / "legacy-demo" / "raw-evidence"

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None


class CorrelateFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name) / "correlated"
        cls.summary = run(FIXTURE, cls.out)
        cls.events = read_jsonl(cls.out / "events.jsonl")
        cls.flow = json.loads((cls.out / "flow.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_counts_match_the_answer_key(self):
        self.assertEqual(self.summary["raw_lines"], 46)
        self.assertEqual(self.summary["unparsed"], 0)
        self.assertEqual(self.summary["kept"], 17)
        self.assertEqual(self.summary["traces"], 2)
        self.assertEqual(self.summary["unassigned"], 1)

    def test_noise_is_gone(self):
        messages = [event.get("message", "") for event in self.events]
        self.assertFalse(any("/health" in m for m in messages))
        self.assertFalse(any(m.strip() == "SELECT 1" for m in messages))
        self.assertFalse(any("Periodic validation" in m for m in messages))

    def test_protected_evidence_survives(self):
        warns = [e for e in self.events if e.get("severity") == "warn" and "rejected" in e.get("message", "")]
        self.assertEqual(len(warns), 1, "el WARN de rechazo nunca debe descartarse")
        inserts = [e for e in self.events if e["event_type"] == "sql" and e["operation"] == "INSERT"]
        self.assertEqual({e["metadata"]["table"] for e in inserts}, {"application", "application_history"})
        rejected = [e for e in self.events if e["event_type"] == "http_response" and e["metadata"]["status"] == 409]
        self.assertEqual(len(rejected), 1)

    def test_identical_sql_with_different_parameters_is_not_deduplicated(self):
        selects = [e for e in self.events if e["event_type"] == "sql" and e["metadata"].get("table") == "citizen"]
        self.assertEqual([e["metadata"]["parameters"]["$1"] for e in selects], ["1003", "1001"])

    def test_events_validate_against_contract(self):
        if jsonschema is None:
            self.skipTest("jsonschema no instalado")
        schema = json.loads((ROOT / "schemas" / "event.schema.json").read_text(encoding="utf-8"))
        validator = jsonschema.Draft202012Validator(schema)
        for event in self.events:
            validator.validate(event)
        flow_schema = json.loads((ROOT / "schemas" / "flow.schema.json").read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(flow_schema).validate(self.flow)

    def test_requests_are_not_mixed(self):
        traces = {trace["correlation_id"]: trace for trace in self.flow["traces"]}
        self.assertEqual(set(traces), {"req-8171", "req-8172"})
        self.assertEqual(traces["req-8171"]["request"]["status"], 409)
        self.assertEqual(traces["req-8172"]["request"]["status"], 201)
        rejected_ids = {e["event_id"] for e in traces["req-8171"]["events"]}
        accepted_ids = {e["event_id"] for e in traces["req-8172"]["events"]}
        self.assertFalse(rejected_ids & accepted_ids)
        accepted_summaries = " ".join(e["summary"] for e in traces["req-8172"]["events"])
        self.assertIn("INSERT INTO application ", accepted_summaries)
        self.assertIn("INSERT INTO application_history", accepted_summaries)
        self.assertNotIn("INSERT", " ".join(e["summary"] for e in traces["req-8171"]["events"]))

    def test_correlation_basis_is_explicit(self):
        by_id = {e["event_id"]: e for e in self.events}
        for trace in self.flow["traces"]:
            for item in trace["events"]:
                event = by_id[item["event_id"]]
                if event.get("correlation_id"):
                    self.assertEqual(item["basis"], "correlation_id")
                else:
                    self.assertEqual(event["metadata"]["inferred_correlation_id"], trace["correlation_id"])
                    self.assertEqual(event["metadata"]["correlation_basis"], item["basis"])
                    self.assertIn("ventana temporal", item["basis"])

    def test_startup_event_is_reported_as_unassigned_not_dropped(self):
        [item] = self.flow["unassigned"]
        self.assertIn("Bound data source", item["summary"])
        self.assertIn("fuera de toda petición", item["reason"])

    def test_every_event_traces_back_to_a_raw_line(self):
        for event in self.events:
            file_name, _, line = event["raw_ref"].rpartition(":")
            raw = (self.out / "raw" / file_name).read_text(encoding="utf-8").splitlines()
            self.assertTrue(1 <= int(line) <= len(raw), event["raw_ref"])

    def test_is_deterministic(self):
        with tempfile.TemporaryDirectory() as other:
            again = Path(other) / "correlated"
            run(FIXTURE, again)
            for name in ("events.jsonl", "flow.json", "flow.md", "reduction.md"):
                self.assertEqual((self.out / name).read_bytes(), (again / name).read_bytes(), name)


class CorrelateErrorsTest(unittest.TestCase):
    def test_unknown_source_without_parser_fails_clearly(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp) / "evidence"
            evidence.mkdir()
            (evidence / "weird.log").write_text("x\n", encoding="utf-8")
            (evidence / "session.json").write_text(json.dumps({
                "session_id": "s", "observed_start": "2026-01-01T00:00:00Z", "observed_end": "2026-01-01T01:00:00Z",
                "collectors": [{"source": "iis", "file": "weird.log"}],
            }), encoding="utf-8")
            with self.assertRaises(MissingParsers) as raised:
                run(evidence, Path(tmp) / "out", profile_ref="java-wildfly-postgres")
            self.assertIn("iis", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
