"""Cada perfil de profiles/ se demuestra con lo que trae, sin un legacy real (auditoría 2026-09-29).

Por cada carpeta de perfil: profile.json, extractors.json y parsers validan contra sus contratos;
y si trae `fixtures/`, se corren los parsers sobre `fixtures/logs/<source>.log` (0 líneas sin
parsear, o las que `expected.json` declare), `discover_datasource` sobre `fixtures/config/`
(esperando lo que `expected.json` diga), el lector del respaldo sintético (`fixtures/dump/` o
el que `fixtures/synthesize.py` genera al vuelo) y, si `expected.json` trae `map`, los
extractores del perfil sobre un desplegable sintético de fixtures/. El contrato de los fixtures está en
profiles/README.md. Hasta hoy los parsers del único perfil `validated` no tenían prueba alguna.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pepper.correlate.parsers import PatternParser  # noqa: E402
from pepper.inspect.readers import MECHANISMS  # noqa: E402
from pepper.inspect.systemmap import build_map  # noqa: E402
from pepper.profiles import Profile, iter_profiles  # noqa: E402
from pepper.rehydrate import datasource_facts, discover_datasource, read_dump_facts  # noqa: E402
from pepper.session import Session  # noqa: E402
from pepper.validate import validate_file, validate_instance  # noqa: E402

try:
    import jsonschema  # noqa: F401
except ImportError:  # pragma: no cover
    jsonschema = None

TZ = timezone(timedelta(hours=-6))
CON_FIXTURES = ("groovy-grails1-tomcat-mysql", "java-springboot-fatjar-postgres",
                "java-springboot-jsf-postgres", "java-wildfly-postgres", "php-apache-mysql")


def _session() -> Session:
    return Session(session_id="fixture", flow_name="fixture del perfil",
                   observed_start=datetime(2026, 9, 22, 9, 0, tzinfo=TZ),
                   observed_end=datetime(2026, 9, 22, 18, 0, tzinfo=TZ), tz=TZ, collectors=[])


class PerfilesTest(unittest.TestCase):
    """Un método por perfil se agrega abajo, al importar el módulo; así cada perfil falla por su nombre."""

    def test_todos_los_perfiles_del_repo_traen_fixtures(self):
        en_disco = sorted(p.parent.name for p in (ROOT / "profiles").glob("*/profile.json"))
        self.assertEqual(en_disco, sorted(CON_FIXTURES), "un perfil nuevo entra a esta lista con sus fixtures (PERFILES.md, regla 4)")
        for pid in CON_FIXTURES:
            self.assertTrue((ROOT / "profiles" / pid / "fixtures" / "expected.json").is_file(), pid)

    # ---------------------------------------------------------------- contratos

    def _contratos(self, profile: Profile) -> None:
        if jsonschema is None:
            self.skipTest("jsonschema no instalado")
        self.assertEqual(validate_file(profile.dir / "profile.json"), [], profile.id)
        extractors = profile.dir / "extractors.json"
        if extractors.is_file():
            self.assertEqual(validate_file(extractors), [], f"{profile.id}: extractors.json")
            for spec in json.loads(extractors.read_text(encoding="utf-8"))["extractors"]:
                self.assertIn(spec["mechanism"], MECHANISMS, f"{profile.id}: mecanismo sin lector registrado")
        for parser in sorted((profile.dir / "parsers").glob("*.json")):
            self.assertEqual(validate_file(parser), [], f"{profile.id}: {parser.name}")
        for collector in profile.data.get("collectors", []):
            self.assertTrue((profile.dir / collector["parser"]).is_file(), f"{profile.id}: {collector['parser']}")

    # ---------------------------------------------------------------- logs

    def _logs(self, profile: Profile, fixtures: Path, expected: dict) -> None:
        logs = sorted((fixtures / "logs").glob("*.log")) if (fixtures / "logs").is_dir() else []
        declared = expected.get("logs") or {}
        self.assertEqual({p.stem for p in logs}, set(declared),
                         f"{profile.id}: cada logs/<source>.log tiene su entrada en expected.json y viceversa")
        for path in logs:
            source = path.stem
            spec_path = profile.parser_spec_for(source)
            self.assertIsNotNone(spec_path, f"{profile.id}: no hay colector con parser para la fuente {source!r}")
            parser = PatternParser.from_file(spec_path)
            events, unparsed = parser.parse_file(path, f"{source}.log", _session())
            want = declared[source]
            self.assertEqual(len(unparsed), int(want.get("unparsed", 0)),
                             f"{profile.id}/{source}: líneas sin parsear: " + "; ".join(u[1][:100] for u in unparsed))
            if "events" in want:
                self.assertEqual(len(events), want["events"], f"{profile.id}/{source}: eventos")
            self.assertTrue(events, f"{profile.id}/{source}: un fixture sin un solo evento no demuestra nada")
            tables = {e.metadata.get("table") for e in events if e.event_type == "sql"}
            for table in want.get("sql_tables", []):
                self.assertIn(table, tables, f"{profile.id}/{source}: el SQL de `{table}` no se reconoció (tablas: {sorted(t for t in tables if t)})")
            if jsonschema is not None:
                for index, event in enumerate(events[:5], 1):
                    event.event_id = f"E-{index:04d}"   # lo pone `pepper correlate` al numerar; aquí solo se parsea
                    self.assertEqual(validate_instance(event.to_dict(), "event"), [], f"{profile.id}/{source}: evento fuera del contrato")

    # ---------------------------------------------------------------- datasource

    def _datasource(self, profile: Profile, fixtures: Path, expected: dict, tmp: Path) -> None:
        want = expected.get("datasource")
        config = fixtures / "config"
        if want is None:
            self.assertFalse(config.is_dir(), f"{profile.id}: fixtures/config/ sin `datasource` en expected.json")
            return
        self.assertTrue(config.is_dir(), f"{profile.id}: expected.json declara datasource y no hay fixtures/config/")
        recipe = profile.data.get("rehydrate", {})
        suffix = (recipe.get("artifact_suffixes") or [".war"])[0]
        artifact = tmp / f"fixture{suffix}"
        with zipfile.ZipFile(artifact, "w") as z:
            inside = config / "artifact"
            if inside.is_dir():
                for path in sorted(inside.rglob("*")):
                    if path.is_file():
                        z.write(path, path.relative_to(inside).as_posix())
            else:
                z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
        beside = None
        if (config / "junto").is_dir():
            beside = tmp / "junto"
            shutil.copytree(config / "junto", beside)
        name, cfg, deviations, creds = discover_datasource(artifact, recipe, legacy_dir=beside)
        facts = datasource_facts(creds, recipe.get("datasource") or {}, recipe.get("database") or {})
        got = {"name": name, "engine": facts.engine, "host": facts.host, "port": facts.port, "db": facts.db, "username": facts.user}
        for key, value in want.items():
            self.assertEqual(got.get(key), value, f"{profile.id}: datasource.{key}")
        self.assertTrue(facts.password, f"{profile.id}: el fixture debe traer una contraseña (ficticia)")
        self.assertNotIn("password", want, f"{profile.id}: la contraseña, ni ficticia, se escribe en expected.json")

    # ---------------------------------------------------------------- respaldo

    def _dump(self, profile: Profile, fixtures: Path, expected: dict, tmp: Path) -> None:
        want = expected.get("dump")
        if want is None:
            return
        synthesize = fixtures / "synthesize.py"
        if synthesize.is_file():
            out = subprocess.run([sys.executable, str(synthesize), str(tmp / "dump")], capture_output=True, text=True, cwd=str(ROOT))
            self.assertEqual(out.returncode, 0, f"{profile.id}: synthesize.py falló: {out.stderr}")
            dump = tmp / "dump" / want["file"]
        else:
            dump = fixtures / "dump" / want["file"]
        self.assertTrue(dump.is_file(), f"{profile.id}: falta el respaldo {dump}")
        database = (profile.data.get("rehydrate") or {}).get("database") or {}
        self.assertEqual((database.get("dump") or {}).get("format"), want["format"], f"{profile.id}: el formato del fixture es el que el perfil declara")
        facts = read_dump_facts(dump, database)
        self.assertEqual(facts.dbname, want["dbname"], profile.id)
        self.assertEqual(facts.server_version, want["server_version"], profile.id)
        self.assertEqual(facts.tables, len(want["tables"]), profile.id)
        self.assertFalse(facts.system_only, profile.id)
        # y el mecanismo del mapa que lee ese formato, con los patrones del perfil
        mechanism = {"pg_dump_custom": "pg_dump_custom", "sql_text": "sql_dump"}[want["format"]]
        extractors = json.loads((profile.dir / "extractors.json").read_text(encoding="utf-8"))["extractors"]
        spec = next((e for e in extractors if e["mechanism"] == mechanism), None)
        self.assertIsNotNone(spec, f"{profile.id}: extractors.json no declara {mechanism} y el perfil lee ese formato")
        artifact = tmp / "vacio.zip"
        with zipfile.ZipFile(artifact, "w") as z:
            z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
        mapa = build_map(artifact, [spec], profile.id, dump=dump, tools={})
        self.assertFalse(any(g.startswith(mechanism) for g in mapa["coverage_gaps"]), mapa["coverage_gaps"])
        tables = {d["name"] for d in mapa["data_stores"] if d["kind"] == "table"}
        self.assertTrue(set(want["tables"]) <= tables, f"{profile.id}: tablas {sorted(tables)}")
        catalogs = {c["table"] for c in mapa["catalogs"]}
        for table in want.get("catalogs", []):
            self.assertIn(table, catalogs, f"{profile.id}: `{table}` debería volcarse como catálogo")
        for table in want.get("not_catalogs", []):
            self.assertNotIn(table, catalogs, f"{profile.id}: `{table}` no debe volcarse (personas o tabla grande)")
        if jsonschema is not None:
            self.assertEqual(validate_instance(mapa, "system-map"), [], profile.id)

    # ---------------------------------------------------------------- el mapa del fuente

    def _map(self, profile: Profile, fixtures: Path, expected: dict, tmp: Path) -> None:
        """`expected.json › map`: los extractores del perfil (menos los del respaldo) sobre un
        desplegable sintético de fixtures/ —una carpeta de fuente o un zip— deben enumerar lo
        declarado. Es la prueba de que un stack entró con DATOS: si el perfil dice que lee rutas
        Laravel o formularios de PHP clásico, aquí se ve que las lee (auditoría 2026-09-29)."""
        want = expected.get("map")
        if want is None:
            return
        artifact = fixtures / want["artifact"]
        self.assertTrue(artifact.exists(), f"{profile.id}: fixtures/{want['artifact']} no existe")
        extractors = json.loads((profile.dir / "extractors.json").read_text(encoding="utf-8"))["extractors"]
        sin_respaldo = [e for e in extractors if not MECHANISMS[e["mechanism"]].needs_dump]
        mapa = build_map(artifact, sin_respaldo, profile.id, dump=None, tools={})
        # las superficies del respaldo (data_stores, catalogs) las demuestra `_dump`; aquí no hay respaldo y su hueco es esperado
        del_respaldo = set()
        for e in extractors:
            if MECHANISMS[e["mechanism"]].needs_dump:
                del_respaldo |= set(MECHANISMS[e["mechanism"]].covers(e))
        huecos = {g.split(":", 1)[0].strip() for g in mapa["coverage_gaps"]} - del_respaldo
        self.assertEqual(huecos, set(want.get("gaps", [])),
                         f"{profile.id}: huecos del mapa distintos de los declarados: {mapa['coverage_gaps']}")
        eps = want.get("entrypoints") or {}
        paths = {e["path"] for e in mapa["entrypoints"]}
        self.assertGreaterEqual(len(mapa["entrypoints"]), int(eps.get("min", 0)), f"{profile.id}: entrypoints {sorted(paths)}")
        for path in eps.get("paths", []):
            self.assertIn(path, paths, f"{profile.id}: la ruta {path} no salió (rutas: {sorted(paths)})")
        rest = [e for e in mapa["entrypoints"] if e.get("kind") == "rest_endpoint"]
        self.assertGreaterEqual(len(rest), int(eps.get("rest_min", 0)), profile.id)
        jobs = want.get("jobs") or {}
        names = {j["name"] for j in mapa["jobs"]}
        self.assertGreaterEqual(len(mapa["jobs"]), int(jobs.get("min", 0)), f"{profile.id}: jobs {sorted(names)}")
        for name in jobs.get("names", []):
            self.assertIn(name, names, f"{profile.id}: el job {name!r} no salió (jobs: {sorted(names)})")
        externos = {d["name"] for d in mapa["external_dependencies"]}
        for host in want.get("external_dependencies", []):
            self.assertIn(host, externos, f"{profile.id}: el host {host} no salió (hosts: {sorted(externos)})")
        pantallas = {s["path"] for s in mapa["screens"]}
        for screen in want.get("screens", []):
            self.assertIn(screen, pantallas, f"{profile.id}: la pantalla {screen} no salió (pantallas: {sorted(pantallas)})")
        if jsonschema is not None:
            self.assertEqual(validate_instance(mapa, "system-map"), [], profile.id)

    # ---------------------------------------------------------------- todo junto

    def _perfil(self, profile: Profile) -> None:
        self._contratos(profile)
        fixtures = profile.dir / "fixtures"
        if not fixtures.is_dir():
            return
        expected = json.loads((fixtures / "expected.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            self._logs(profile, fixtures, expected)
            self._datasource(profile, fixtures, expected, Path(tmp))
            self._dump(profile, fixtures, expected, Path(tmp))
            self._map(profile, fixtures, expected, Path(tmp))


def _agrega(profile: Profile) -> None:
    def test(self: PerfilesTest) -> None:
        self._perfil(profile)
    test.__name__ = f"test_perfil_{profile.id.replace('-', '_')}"
    test.__doc__ = f"contratos y fixtures del perfil {profile.id}"
    setattr(PerfilesTest, test.__name__, test)


for _profile in iter_profiles():
    _agrega(_profile)


if __name__ == "__main__":
    unittest.main()
