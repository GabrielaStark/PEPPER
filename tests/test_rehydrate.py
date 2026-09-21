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
        self.assertTrue(any("16" in d and "por fidelidad" in d for d in plan.deviations), plan.deviations)
        self.assertEqual((plan.db_engine, plan.db_image, plan.db_tool_image), ("postgresql", "postgres:10", "postgres:17"))
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
        self.assertIn("for role in owner; do", restore, "los dueños del respaldo llegan como datos; el psql lo pone la plantilla")
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


def _profile(recipe):
    """Un perfil mínimo en memoria: solo la receta de rehydrate que la prueba necesita."""
    from pepper.profiles import Profile

    return Profile(id="perfil-de-prueba", dir=PROFILE.dir, data={"id": "perfil-de-prueba", "rehydrate": recipe})


class ComponentesTest(unittest.TestCase):
    """El perfil reparte los artefactos en componentes; el núcleo no sabe qué es un gateway."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.legacy = Path(self._tmp.name) / "legacy"
        self.legacy.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _jar(self, name, config=""):
        with zipfile.ZipFile(self.legacy / name, "w") as z:
            z.writestr("BOOT-INF/lib/spring-core.jar", "x")
            z.writestr("BOOT-INF/classes/application.yml", config or "server:\n  port: 8080\n")

    def _front(self, name):
        with zipfile.ZipFile(self.legacy / name, "w") as z:
            z.writestr("dist/index.html", "<html></html>")

    RECETA = {
        "config_patterns": [r"^BOOT-INF/classes/application.*\.yml$"],
        "server_images": {"java": {"8": "eclipse-temurin:8-jre"}, "static": {"*": "httpd:2.4-alpine"}},
        "components": {"classify": [
            {"role": "discovery", "engine": "java", "when": {"config_contains": "eureka"}},
            {"role": "gateway", "engine": "java", "when": {"config_contains": "cloud.gateway"}},
            {"role": "frontend", "engine": "static", "port": 80, "when": {"member_glob": "*index.html"}},
            {"role": "backend", "engine": "java", "when": {"member_glob": "BOOT-INF/lib/*.jar"}},
        ]},
    }

    def test_cada_artefacto_recibe_su_papel_su_puerto_y_su_ip(self):
        from pepper.rehydrate import classify_components

        self._jar("descubrimiento-1.0.jar", "server:\n  port: 8097\neureka:\n  server:\n    enabled: true\n")
        self._jar("puerta-1.0.jar", "server:\n  port: 8098\nspring:\n  cloud.gateway:\n    enabled: true\n")
        self._jar("recursos-1.0.jar", "server:\n  port: 8099\n")
        self._front("front.zip")
        artifacts = sorted(self.legacy.glob("*"), key=lambda p: p.name)
        componentes, sin_clasificar = classify_components(artifacts, _profile(self.RECETA), "10.100.0")

        self.assertEqual(sin_clasificar, [])
        papeles = {c.name: c.role for c in componentes}
        self.assertEqual(papeles["descubrimiento"], "discovery")
        self.assertEqual(papeles["puerta"], "gateway")
        self.assertEqual(papeles["recursos"], "backend")
        self.assertEqual(papeles["front"], "frontend")
        puertos = {c.name: c.port for c in componentes}
        self.assertEqual(puertos["recursos"], 8099, "el puerto sale de la configuración del propio artefacto")
        self.assertEqual(puertos["front"], 80, "y si el perfil lo fija, manda el perfil")
        self.assertEqual(len({c.ip for c in componentes}), 4, "cada componente con su IP")
        self.assertEqual({c.image for c in componentes if c.engine == "java"}, {"eclipse-temurin:8-jre"})

    def test_lo_que_ninguna_regla_reconoce_se_declara_no_se_inventa(self):
        from pepper.rehydrate import classify_components

        self._jar("recursos-1.0.jar")
        (self.legacy / "misterio.ear").write_bytes(b"PK\x03\x04sin-nada")
        artifacts = sorted(self.legacy.glob("*"), key=lambda p: p.name)
        componentes, sin_clasificar = classify_components(artifacts, _profile(self.RECETA), "10.100.0")
        self.assertEqual(sin_clasificar, ["misterio.ear"])
        self.assertEqual([c.name for c in componentes], ["recursos"])

    def test_sin_reglas_en_el_perfil_no_se_clasifica_nada(self):
        from pepper.rehydrate import classify_components

        self._jar("recursos-1.0.jar")
        componentes, sin_clasificar = classify_components(
            sorted(self.legacy.glob("*")), _profile({"config_patterns": [r"application.*\.yml$"]}), "10.100.0")
        self.assertEqual(componentes, [])
        self.assertEqual(sin_clasificar, ["recursos-1.0.jar"])


class VariosDesplegablesTest(unittest.TestCase):
    """Un sistema de microservicios no se levanta escogiendo el archivo más grande.

    Con tres jars y un front, `find_inputs` devolvía el mayor y callaba los otros: el
    ambiente arrancaba a medias (sin gateway, sin descubrimiento) y todo lo que se
    observara encima sería basura presentada como evidencia (2026-09-15).
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.legacy = Path(self._tmp.name) / "legacy"
        self.legacy.mkdir()
        (self.legacy / "respaldo.dump").write_bytes(b"PGDMP" + b"\0" * 64)

    def tearDown(self):
        self._tmp.cleanup()

    def _jar(self, name, size_mb=1):
        with zipfile.ZipFile(self.legacy / name, "w") as z:
            z.writestr("BOOT-INF/classes/application.yml", "x" * (size_mb * 1024))

    def test_todos_los_desplegables_se_ven(self):
        from pepper.rehydrate import find_all_inputs

        for name in ("api-core.jar", "gateway.jar", "descubrimiento.jar"):
            self._jar(name)
        artifacts, dumps = find_all_inputs(self.legacy)
        self.assertEqual({a.name for a in artifacts}, {"api-core.jar", "gateway.jar", "descubrimiento.jar"})
        self.assertEqual([d.name for d in dumps], ["respaldo.dump"])

    def test_varios_desplegables_sin_perfil_que_los_reparta_es_blocked(self):
        from pepper.rehydrate import Blocked, make_plan

        for name in ("api-core.jar", "gateway.jar", "descubrimiento.jar"):
            self._jar(name)
        profile = _profile({"config_patterns": [r"application.*\.yml$"]})
        with self.assertRaises(Blocked) as caught:
            make_plan(self.legacy, profile)
        mensaje = str(caught.exception)
        self.assertIn("3 desplegables", mensaje)
        for name in ("api-core.jar", "gateway.jar", "descubrimiento.jar"):
            self.assertIn(name, mensaje, "el reporte debe nombrar cada desplegable que vio")

    def test_un_solo_desplegable_sigue_su_camino(self):
        from pepper.rehydrate import Blocked, make_plan

        self._jar("api-core.jar")
        profile = _profile({"config_patterns": [r"application.*\.yml$"]})
        with self.assertRaises(Blocked) as caught:
            make_plan(self.legacy, profile)
        self.assertNotIn("desplegables y el perfil", str(caught.exception),
                         "con uno solo no debe hablar de varios desplegables")


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


class ComponentsTerminanEnBlockedTest(unittest.TestCase):
    """`classify_components` existe, pero el resto del camino levanta UNA aplicación.

    Un perfil con `rehydrate.components` dejaba de bloquear y `make_plan` seguía con
    `artifacts[0]`: PEPPER observaba un sistema incompleto sin decirlo (revisión 2026-09-21).
    Hasta que exista compose, arranque y validación por componente, `components` termina en
    BLOCKED diciendo qué hay — salvo un solo desplegable que la clasificación reconoce como backend.
    """

    RECETA = dict(ComponentesTest.RECETA, artifact_suffixes=[".jar", ".zip"])

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.legacy = Path(self._tmp.name) / "legacy"
        self.legacy.mkdir()
        (self.legacy / "respaldo.dump").write_bytes(b"PGDMP" + b"\0" * 64)

    def tearDown(self):
        self._tmp.cleanup()

    def _jar(self, name, config=""):
        ComponentesTest._jar(self, name, config)

    def _front(self, name):
        ComponentesTest._front(self, name)

    def test_varios_componentes_es_blocked_y_dice_que_hay(self):
        from pepper.rehydrate import Blocked, make_plan

        self._jar("puerta-1.0.jar", "server:\n  port: 8098\nspring:\n  cloud.gateway:\n    enabled: true\n")
        self._jar("recursos-1.0.jar", "server:\n  port: 8099\n")
        self._front("front.zip")
        with self.assertRaises(Blocked) as caught:
            make_plan(self.legacy, _profile(self.RECETA))
        mensaje = str(caught.exception)
        self.assertIn("una sola aplicación", mensaje)
        for pieza in ("puerta-1.0.jar", "gateway", "recursos-1.0.jar", "backend", "front.zip", "frontend"):
            self.assertIn(pieza, mensaje, "el bloqueo nombra cada componente y su papel")

    def test_lo_no_clasificado_tambien_se_nombra(self):
        from pepper.rehydrate import Blocked, make_plan

        self._jar("recursos-1.0.jar", "server:\n  port: 8099\n")
        (self.legacy / "misterio.zip").write_bytes(b"PK\x03\x04sin-nada")
        with self.assertRaises(Blocked) as caught:
            make_plan(self.legacy, _profile(self.RECETA))
        self.assertIn("misterio.zip  → sin clasificar", str(caught.exception))

    def test_un_solo_front_no_es_una_aplicacion(self):
        from pepper.rehydrate import Blocked, make_plan

        self._front("front.zip")
        with self.assertRaises(Blocked) as caught:
            make_plan(self.legacy, _profile(self.RECETA))
        self.assertIn("frontend", str(caught.exception))
        self.assertIn("una sola aplicación", str(caught.exception))

    def test_un_solo_backend_sigue_su_camino(self):
        from pepper.rehydrate import Blocked, make_plan

        self._jar("recursos-1.0.jar", "server:\n  port: 8099\n")
        with self.assertRaises(Blocked) as caught:
            make_plan(self.legacy, _profile(self.RECETA))  # se detiene más adelante (sin datasource), no por components
        self.assertNotIn("rehydrate.components", str(caught.exception))
        self.assertNotIn("una sola aplicación", str(caught.exception))
