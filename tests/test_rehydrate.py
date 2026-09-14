"""`pepper rehydrate`: del artefacto y el respaldo al plan y al compose, sin Docker.

Hermético: un WAR sintético con su configuración embebida y descriptores, un
respaldo custom escrito por el test, y el perfil real (sus plantillas)."""

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pepper.profiles import load_profile  # noqa: E402
from pepper.rehydrate import Blocked, env_line, make_plan, parse_config, render, write_environment  # noqa: E402
from pepper.validate import validate_file  # noqa: E402
from tests.test_systemmap import TABLES, write_custom_dump  # noqa: E402

PROFILE = load_profile("java-springboot-jsf-postgres")

CONFIG_PROD = """server:
  port: 8089
spring:
  datasource:
    #local
    url: jdbc:postgresql://10.42.7.2:5432/nominas_prod
    username: nominas
    password: s3cr3t
    #develop
    #url: jdbc:postgresql://10.200.28.7:5432/nominas_dev
bus:
  url: https://bus.institucion.example/
  psw: otra
externo:
  restUrl: http://10.250.40.142:8080/
  editorUrl: https://editor.institucion.example:9980/cool.html
mail:
  smtp: smtp.correo.example
  port: 587
app:
  rutaArchivos: /archivos/app
"""


def _make_war(path, with_descriptor=True):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\nStart-Class: gob.demo.nominas.Application\n")
        z.writestr("WEB-INF/classes/application.yml", "spring:\n  profiles:\n    active: prod\n")
        z.writestr("WEB-INF/classes/application-prod.yml", CONFIG_PROD)
        z.writestr("WEB-INF/classes/application-local.yml", "spring:\n  datasource:\n    url: jdbc:postgresql://localhost:5432/x\n")
        if with_descriptor:
            z.writestr("WEB-INF/jboss-web.xml", "<jboss-web/>")


class ParseConfigTest(unittest.TestCase):
    def test_yaml_anidado_sin_pyyaml(self):
        cfg = parse_config(CONFIG_PROD)
        self.assertEqual(cfg["spring.datasource.url"], "jdbc:postgresql://10.42.7.2:5432/nominas_prod")
        self.assertEqual(cfg["spring.datasource.password"], "s3cr3t")
        self.assertNotIn("spring.datasource.#url", cfg)
        self.assertEqual(cfg["mail.port"], "587")

    def test_properties(self):
        cfg = parse_config("a.b=1\n# c\nspring.datasource.url=jdbc:postgresql://h/db\n")
        self.assertEqual(cfg["spring.datasource.url"], "jdbc:postgresql://h/db")


class PlanTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.legacy = Path(self._tmp.name) / "legacy"
        self.legacy.mkdir()
        _make_war(self.legacy / "nominas-2.3.war")
        write_custom_dump(self.legacy / "respaldo.dump", TABLES)
        (self.legacy / "NOTAS.md").write_text("aplicaciones es un wildfly 21\nbase de datos postgres 16\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_el_artefacto_dicta_el_ambiente(self):
        plan = make_plan(self.legacy, PROFILE, notes_path=self.legacy / "NOTAS.md")
        self.assertEqual(plan.spring_profile, "prod")
        self.assertEqual((plan.db_ip, plan.db_port, plan.db_name, plan.db_user, plan.db_password),
                         ("10.42.7.2", 5432, "nominas_prod", "nominas", "s3cr3t"))
        self.assertEqual(plan.subnet, "10.42.7.0/24")
        self.assertTrue(plan.app_ip.startswith("10.42.7.") and plan.app_ip != plan.db_ip)
        self.assertEqual(plan.server, "wildfly")
        self.assertEqual(plan.server_image, "jboss/wildfly:21.0.2.Final")
        self.assertEqual(plan.postgres_version, "10")   # el respaldo, no la nota
        self.assertEqual(plan.pg_restore_version, "17")
        self.assertEqual(plan.external_hosts, ["bus.institucion.example", "editor.institucion.example", "smtp.correo.example"])
        self.assertEqual(plan.external_by_ip, ["10.250.40.142:8080"])
        self.assertIn("9980", plan.stub_ports.split(","))
        self.assertIn("587", plan.stub_ports.split(","))
        self.assertEqual(plan.app_package_env, "GOB_DEMO_NOMINAS")
        self.assertEqual(plan.files_root, "/archivos/app")
        self.assertTrue(any("PostgreSQL 16" in d for d in plan.deviations), plan.deviations)
        self.assertTrue(any("base_origen" in d for d in plan.deviations), plan.deviations)
        self.assertIn("owner", plan.create_roles)

    def test_render_llena_las_plantillas_y_aisla(self):
        plan = make_plan(self.legacy, PROFILE, notes_path=self.legacy / "NOTAS.md")
        out = Path(self._tmp.name) / "rehydrate"
        written = render(plan, PROFILE, out)
        compose = (out / "docker-compose.yml").read_text(encoding="utf-8")
        import re
        self.assertEqual(re.findall(r"\{\{\w+\}\}", compose), [], "toda variable sustituida")
        self.assertIn("ipv4_address: 10.42.7.2", compose)
        self.assertIn("internal: true", compose)
        self.assertIn("aliases: [bus.institucion.example, editor.institucion.example, smtp.correo.example]", compose)
        self.assertIn("image: jboss/wildfly:21.0.2.Final", compose)
        # .env solo lleva la credencial, entrecomillada y con $ escapado; la imagen va en el compose
        self.assertEqual((out / ".env").read_text(encoding="utf-8").strip(), 'DB_PASSWORD="s3cr3t"')
        self.assertIn("s3cr3t", (out / ".env").read_text(encoding="utf-8"))
        self.assertNotIn("s3cr3t", compose, "la credencial va en .env, no en el compose")
        restore = (out / "restore.sh").read_text(encoding="utf-8")
        self.assertIn('CREATE ROLE \\"owner\\"', restore)
        self.assertIn("SET host '10.42.7.3'", restore)
        self.assertTrue((out / "proxy" / "proxy.py").is_file() and (out / "stub" / "stub.py").is_file())
        self.assertEqual({w.name for w in written}, {"docker-compose.yml", "restore.sh", "proxy.py", "stub.py", ".env"})
        # el compose rendido pasa el chequeo estático de aislamiento (sin Docker: pyyaml no está → UNKNOWN es aceptable,
        # pero nunca FAILED)
        try:
            from pepper.isolate import check_static, resolve_compose
            compose_dict, resolved = resolve_compose(out / "docker-compose.yml")
        except RuntimeError:
            self.skipTest("sin docker compose ni pyyaml para resolver el compose")
        report = check_static(compose_dict, plan.external_hosts, "ingress", resolved=resolved, compose_dir=out)
        self.assertNotEqual(report.verdict, "FAILED", [f.check for f in report.errors])

    def test_blocked_sin_respaldo(self):
        (self.legacy / "respaldo.dump").unlink()
        with self.assertRaises(Blocked):
            make_plan(self.legacy, PROFILE)

    def test_blocked_sin_configuracion_completa(self):
        (self.legacy / "nominas-2.3.war").unlink()
        with zipfile.ZipFile(self.legacy / "nominas-2.3.war", "w") as z:
            z.writestr("WEB-INF/classes/application-prod.yml", "spring:\n  datasource:\n    url: jdbc:postgresql://10.1.1.1/db\n")
            z.writestr("WEB-INF/jboss-web.xml", "<jboss-web/>")
        with self.assertRaises(Blocked) as ctx:
            make_plan(self.legacy, PROFILE)
        self.assertIn("datasource", str(ctx.exception))

    def test_sin_descriptor_ni_nota_es_blocked(self):
        (self.legacy / "nominas-2.3.war").unlink()
        _make_war(self.legacy / "nominas-2.3.war", with_descriptor=False)
        with self.assertRaises(Blocked):
            make_plan(self.legacy, PROFILE, notes_path=None)


if __name__ == "__main__":
    unittest.main()


def _make_war_with(path, prod_config, base_config="spring:\n  profiles:\n    active: prod\n", descriptor=True, extra=None):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\nStart-Class: gob.demo.nominas.Application\n")
        z.writestr("WEB-INF/classes/application.yml", base_config)
        if prod_config is not None:
            z.writestr("WEB-INF/classes/application-prod.yml", prod_config)
        for name, body in (extra or {}).items():
            z.writestr(name, body)
        if descriptor:
            z.writestr("WEB-INF/jboss-web.xml", "<jboss-web/>")


class AuditoriaRehydrateTest(unittest.TestCase):
    """Lo que salió en la auditoría del 2026-09-11 sobre rehydrate, fijado para siempre."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _legacy(self, prod_config=CONFIG_PROD, notes="aplicaciones es un wildfly 21\n", **kw):
        legacy = self.root / "legacy"; legacy.mkdir(exist_ok=True)
        _make_war_with(legacy / "nominas-2.3.war", prod_config, **kw)
        write_custom_dump(legacy / "respaldo.dump", TABLES)
        (legacy / "NOTAS.md").write_text(notes, encoding="utf-8")
        return legacy

    def test_env_line_escapa_lo_que_compose_interpola(self):
        # `ab$cd` se volvía `ab` al pasar por .env: compose interpola `$cd`
        self.assertEqual(env_line("DB_PASSWORD", 'ab$cd#ef gh "x"'), 'DB_PASSWORD="ab$$cd#ef gh \\"x\\""')

    def test_el_proyecto_lleva_un_hash_del_respaldo(self):
        plan = make_plan(self._legacy(), PROFILE)
        self.assertRegex(plan.stack_name, r"^nominas-[0-9a-f]{8}$")
        self.assertEqual(plan.stack_name.split("-")[1], plan.dump_sha[:8])

    def test_una_nota_sobre_la_base_no_convierte_al_app_en_postgres(self):
        plan = make_plan(self._legacy(notes="La base es postgres 12 en producción.\n"), PROFILE, notes_path=self.root / "legacy" / "NOTAS.md")
        self.assertEqual(plan.server, "wildfly")
        self.assertTrue(plan.server_image.startswith("jboss/wildfly") or "wildfly" in plan.server_image)

    def test_datasource_en_localhost_es_blocked(self):
        cfg = CONFIG_PROD.replace("10.42.7.2", "localhost")
        with self.assertRaisesRegex(Blocked, "localhost"):
            make_plan(self._legacy(prod_config=cfg), PROFILE)

    def test_contrasena_sin_resolver_es_blocked(self):
        cfg = CONFIG_PROD.replace("password: s3cr3t", "password: ${DB_PASS}")
        with self.assertRaisesRegex(Blocked, "sin resolver"):
            make_plan(self._legacy(prod_config=cfg), PROFILE)

    def test_files_root_nunca_es_la_raiz_ni_un_context_path(self):
        cfg = CONFIG_PROD.replace("  rutaArchivos: /archivos/app\n", "  context-path: /\n  redirectPath: /login\n")
        plan = make_plan(self._legacy(prod_config=cfg), PROFILE)
        self.assertEqual(plan.files_root, "/data")

    def test_datasource_por_nombre_da_alias_a_la_base_y_gateway_propio(self):
        cfg = CONFIG_PROD.replace("10.42.7.2", "dbprod.institucion.example")
        plan = make_plan(self._legacy(prod_config=cfg), PROFILE)
        self.assertEqual(plan.db_alias, "dbprod.institucion.example")
        self.assertTrue(plan.gateway_ip and plan.gateway_ip not in (plan.db_ip, plan.stub_ip, plan.app_ip, plan.dns_sink))
        out = self.root / "out"
        render(plan, PROFILE, out)
        compose = (out / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("aliases: [dbprod.institucion.example]", compose)
        self.assertIn(f"gateway: {plan.gateway_ip}", compose)
        restore = (out / "restore.sh").read_text(encoding="utf-8")
        self.assertIn("restore.status", restore)
        self.assertIn("PEPPER_RESTORE status=", restore)
        self.assertIn(f"pepper:restored:{plan.dump_sha}", restore)
        env = (out / ".env").read_text(encoding="utf-8")
        self.assertEqual(env.strip(), 'DB_PASSWORD="s3cr3t"')

    def test_multidocumento_y_spring_profiles_active_mandan(self):
        base = ("spring:\n  profiles:\n    active: nomina\n"
                "---\nspring:\n  profiles: qa\n  datasource:\n    url: jdbc:postgresql://10.42.7.5:5432/qa\n    username: q\n    password: q\n"
                "---\nspring:\n  profiles: nomina\n  datasource:\n    url: jdbc:postgresql://10.42.7.2:5432/nomina\n    username: s\n    password: s\n")
        plan = make_plan(self._legacy(prod_config=None, base_config=base), PROFILE)
        self.assertEqual((plan.spring_profile, plan.db_name), ("nomina", "nomina"))

    def test_varios_perfiles_completos_sin_active_se_declara(self):
        base = "spring:\n  application:\n    name: x\n"
        extra = {"WEB-INF/classes/application-qa.yml": CONFIG_PROD.replace("nominas_prod", "qa_db")}
        plan = make_plan(self._legacy(base_config=base, extra=extra), PROFILE)
        self.assertEqual(plan.spring_profile, "prod")
        self.assertTrue(any("varios perfiles" in d for d in plan.deviations), plan.deviations)

    def test_dependencia_por_ip_dentro_de_la_subred_no_desaparece(self):
        cfg = CONFIG_PROD.replace("http://10.250.40.142:8080/", "http://10.42.7.50:8080/")
        plan = make_plan(self._legacy(prod_config=cfg), PROFILE)
        self.assertIn("10.42.7.50:8080", plan.external_by_ip)

    def test_environment_json_valida_en_todos_los_estados(self):
        plan = make_plan(self._legacy(), PROFILE)
        for status in ("READY", "PARTIAL", "FAILED"):
            docs = self.root / f"docs-{status}"
            env_path, _ = write_environment(plan, PROFILE, status, [{"check": "x", "result": "pass", "detail": "y"}],
                                            [{"missing": "m", "recommended_evidence": "r"}] if status == "PARTIAL" else [],
                                            docs, self.root / "out")
            self.assertEqual(validate_file(env_path, "environment"), [], status)
