"""Parsers del perfil contra líneas con el formato de la primera corrida real.

Las líneas son inventadas (sistema ficticio) pero byte a byte con la forma real:
prefijo RFC3339 de `docker logs --timestamps` (nanosegundos), códigos ANSI de
WildFly, log de PostgreSQL con %m [%p] %u@%d y DETAIL de parámetros.
"""

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pepper.correlate.parsers import PatternParser  # noqa: E402
from pepper.session import Session, parse_datetime  # noqa: E402

PARSERS = ROOT / "profiles/java-springboot-jsf-postgres/parsers"
ESC = chr(27)
TZ = timezone(timedelta(hours=-6))


def _session():
    return Session(session_id="flow-test", flow_name="prueba",
                   observed_start=datetime(2026, 9, 2, 12, 0, tzinfo=TZ),
                   observed_end=datetime(2026, 9, 2, 13, 0, tzinfo=TZ),
                   tz=TZ, collectors=[])


def _parse(parser_file, lines):
    parser = PatternParser.from_file(PARSERS / parser_file)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "sample.log"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return parser.parse_file(path, "sample.log", _session())


class ParseDatetimeTest(unittest.TestCase):
    def test_nanosegundos_de_docker_timestamps(self):
        parsed = parse_datetime("2026-09-02T18:36:30.218782839Z", TZ)
        self.assertEqual(parsed.microsecond, 218782)
        self.assertEqual(parsed.utcoffset(), timedelta(0))


class WildflyParserTest(unittest.TestCase):
    LINES = [
        f"2026-09-02T18:34:53.476123456Z {ESC}[0m{ESC}[0m18:34:53,476 INFO  [gob.demo.app.Arranque] (ServerService Thread Pool -- 78) The following profiles are active: qa",
        f"2026-09-02T18:36:30.218782839Z {ESC}[0m{ESC}[31m18:36:30,213 ERROR [io.undertow.request] (default task-1) UT005023: Exception handling request to /Vista/Login.jsf: javax.servlet.ServletException: no se pudo",
        f"2026-09-02T18:36:30.218900000Z \tat javax.faces.webapp.FacesServlet.service(FacesServlet.java:451)",
        f"2026-09-02T18:36:30.219000000Z Caused by: javax.faces.application.ViewExpiredException: viewId:/Vista/Login.jsf",
        "2026-09-02T18:36:30.219024797Z ",
        f"2026-09-02T18:36:31.100000000Z {ESC}[0m18:36:31,100 DEBUG [org.hibernate.SQL] (default task-2) select u.* from ctusuario u where u.dsusuario=?",
    ]

    def test_parsea_todo_con_docker_prefix_y_ansi(self):
        events, unparsed = _parse("wildfly-server.json", self.LINES)
        self.assertEqual(unparsed, [])
        self.assertEqual(len(events), 3)  # INFO + ERROR(con continuación) + SQL

    def test_el_timestamp_autoritativo_es_el_de_docker_en_utc(self):
        events, _ = _parse("wildfly-server.json", self.LINES)
        error = events[1]
        self.assertEqual(error.timestamp.utcoffset(), timedelta(0))
        self.assertEqual((error.timestamp.hour, error.timestamp.minute), (18, 36))

    def test_error_es_exception_y_conserva_thread(self):
        events, _ = _parse("wildfly-server.json", self.LINES)
        error = events[1]
        self.assertEqual(error.event_type, "exception")
        self.assertEqual(error.severity, "error")
        self.assertEqual(error.metadata["thread"], "default task-1")
        # el stack trace y la línea vacía con prefijo quedaron anexados, no sin parsear
        self.assertIn("ViewExpiredException", error.message)

    def test_hibernate_sql_es_evento_sql(self):
        events, _ = _parse("wildfly-server.json", self.LINES)
        sql = events[2]
        self.assertEqual(sql.event_type, "sql")
        self.assertEqual(sql.operation, "SELECT")
        self.assertEqual(sql.metadata.get("table"), "ctusuario")


class PostgresParserTest(unittest.TestCase):
    LINES = [
        "2026-09-02T18:37:00.161673214Z 2026-09-02 18:37:00.161 UTC [117] appuser@demodb LOG:  execute S_2: select c.* from ctcita c where c.dsestatus=$1",
        "2026-09-02T18:37:00.161679589Z 2026-09-02 18:37:00.161 UTC [117] appuser@demodb DETAIL:  parameters: $1 = 'AGENDADA'",
    ]

    def test_docker_prefix_es_el_timestamp_y_no_hay_corrimiento_de_zona(self):
        events, unparsed = _parse("postgresql-log.json", self.LINES)
        self.assertEqual(unparsed, [])
        [event] = events  # el DETAIL se fusiona en la sentencia
        # regresión: el parser viejo asumía la zona de la sesión (-06:00) y corría 6h
        self.assertEqual(event.timestamp.utcoffset(), timedelta(0))
        self.assertEqual(event.timestamp.hour, 18)

    def test_detail_fusiona_parametros_por_pid(self):
        events, _ = _parse("postgresql-log.json", self.LINES)
        [event] = events
        self.assertEqual(event.event_type, "sql")
        self.assertEqual(event.metadata["parameters"], {"$1": "AGENDADA"})
        self.assertEqual(event.metadata["pg_ts"], "2026-09-02 18:37:00.161")


if __name__ == "__main__":
    unittest.main()
