"""`pepper rehydrate`: del artefacto y el respaldo al plan y al compose, sin Docker.

Hermético: un WAR sintético con su configuración embebida y descriptores, un
respaldo custom escrito por el test, y el perfil real (sus plantillas)."""

import json
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
        self.assertEqual((out / ".env").read_text(encoding="utf-8").splitlines()[0], 'DB_PASSWORD="s3cr3t"')
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
        (self.legacy / "NOTAS.md").unlink()
        _make_war(self.legacy / "nominas-2.3.war", with_descriptor=False)
        with self.assertRaises(Blocked):
            make_plan(self.legacy, PROFILE)


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
        plan = make_plan(self._legacy(notes="La base es postgres 12 en producción. El app corre en wildfly 21.\n"), PROFILE, notes_path=self.root / "legacy" / "NOTAS.md")
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
        self.assertEqual(env.splitlines()[0], 'DB_PASSWORD="s3cr3t"')

    def test_multidocumento_y_spring_profiles_active_mandan(self):
        base = ("spring:\n  profiles:\n    active: nomina\n"
                "---\nspring:\n  profiles: qa\n  datasource:\n    url: jdbc:postgresql://10.42.7.5:5432/qa\n    username: q\n    password: q\n"
                "---\nspring:\n  profiles: nomina\n  datasource:\n    url: jdbc:postgresql://10.42.7.2:5432/nomina\n    username: s\n    password: s\n")
        plan = make_plan(self._legacy(prod_config=None, base_config=base), PROFILE)
        self.assertEqual((plan.spring_profile, plan.db_name), ("nomina", "nomina"))

    def test_varios_perfiles_completos_sin_active_es_blocked(self):
        # P1-02 (auditoría 2026-09-21): elegir `prod` por costumbre era adivinar el ambiente
        base = "spring:\n  application:\n    name: x\n"
        extra = {"WEB-INF/classes/application-qa.yml": CONFIG_PROD.replace("nominas_prod", "qa_db")}
        with self.assertRaisesRegex(Blocked, "varios perfiles.*--config-profile"):
            make_plan(self._legacy(base_config=base, extra=extra), PROFILE)

    def test_la_persona_elige_el_perfil_y_queda_registrado(self):
        base = "spring:\n  application:\n    name: x\n"
        extra = {"WEB-INF/classes/application-qa.yml": CONFIG_PROD.replace("nominas_prod", "qa_db")}
        plan = make_plan(self._legacy(base_config=base, extra=extra), PROFILE, config_profile="qa")
        self.assertEqual((plan.spring_profile, plan.db_name), ("qa", "qa_db"))
        self.assertTrue(any("elegido por la persona" in d for d in plan.deviations), plan.deviations)
        with self.assertRaisesRegex(Blocked, "no es un perfil completo"):
            make_plan(self._legacy(base_config=base, extra=extra), PROFILE, config_profile="inventado")

    # --- P1 (revisión 2026-09-22): el perfil activo se respeta entero o se bloquea; nunca se sustituye ---

    def test_perfil_activo_incompleto_no_se_sustituye_por_otro_completo(self):
        base = "spring:\n  profiles:\n    active: production\n"
        extra = {"WEB-INF/classes/application-production.yml": "spring:\n  datasource:\n    url: jdbc:postgresql://10.42.7.9:5432/prod_db\n",
                 "WEB-INF/classes/application-qa.yml": CONFIG_PROD.replace("nominas_prod", "qa_db")}
        with self.assertRaisesRegex(Blocked, r"spring\.profiles\.active = production .*fuera del artefacto"):
            make_plan(self._legacy(prod_config=None, base_config=base, extra=extra), PROFILE)
        # y sin ningún documento para el activo, tampoco: qa completo no lo reemplaza
        with self.assertRaisesRegex(Blocked, r"sin documento en el artefacto: production"):
            make_plan(self._legacy(prod_config=None, base_config=base,
                                   extra={"WEB-INF/classes/application-qa.yml": CONFIG_PROD.replace("nominas_prod", "qa_db")}), PROFILE)

    def test_varios_perfiles_activos_se_combinan_y_manda_el_ultimo(self):
        base = "spring:\n  profiles:\n    active: prod,qa\n"
        extra = {"WEB-INF/classes/application-qa.yml": "spring:\n  datasource:\n    url: jdbc:postgresql://10.42.7.5:5432/qa_db\n"}
        plan = make_plan(self._legacy(base_config=base, extra=extra), PROFILE)
        self.assertEqual((plan.spring_profile, plan.db_name, plan.db_ip), ("prod,qa", "qa_db", "10.42.7.5"))
        self.assertEqual(plan.db_user, "nominas")   # lo que qa no redefine viene de prod
        out = self.root / "out"
        render(plan, PROFILE, out)
        self.assertIn("SPRING_PROFILES_ACTIVE: prod,qa", (out / "docker-compose.yml").read_text(encoding="utf-8"))

    def test_el_override_se_valida_antes_que_cualquier_salida(self):
        from pepper.rehydrate import choose_spring_profile
        base_only = {"default": {"spring.datasource.url": "jdbc:postgresql://10.42.7.2:5432/x",
                                 "spring.datasource.username": "u", "spring.datasource.password": "p"}}
        with self.assertRaisesRegex(Blocked, "no es un perfil completo"):
            choose_spring_profile(base_only, override="inventado")
        name, cfg, deviations = choose_spring_profile(base_only, override="default")
        self.assertEqual((name, cfg["spring.datasource.username"]), ("default", "u"))
        self.assertTrue(any("elegido por la persona" in d for d in deviations), deviations)
        # un override que nombra un documento incompleto tampoco pasa
        configs = {"default": {}, "qa": {"spring.datasource.url": "jdbc:postgresql://h:5432/qa"}}
        with self.assertRaisesRegex(Blocked, "no es un perfil completo"):
            choose_spring_profile(configs, override="qa")

    def test_perfil_activo_sin_documento_usa_la_base_y_queda_declarado(self):
        from pepper.rehydrate import choose_spring_profile
        configs = {"default": {"spring.profiles.active": "production",
                               "spring.datasource.url": "jdbc:postgresql://10.42.7.2:5432/x",
                               "spring.datasource.username": "u", "spring.datasource.password": "p"}}
        name, cfg, deviations = choose_spring_profile(configs)
        self.assertEqual(name, "production")
        self.assertEqual(cfg["spring.datasource.url"], "jdbc:postgresql://10.42.7.2:5432/x")
        self.assertTrue(any("no trae documento para: production" in d for d in deviations), deviations)

    def test_sin_perfil_activo_el_unico_completo_queda_declarado_y_la_base_completa_cuenta(self):
        from pepper.rehydrate import choose_spring_profile
        configs = {"default": {}, "prod": {"spring.datasource.url": "jdbc:postgresql://h:5432/p",
                                           "spring.datasource.username": "u", "spring.datasource.password": "p"}}
        name, _, deviations = choose_spring_profile(configs)
        self.assertEqual(name, "prod")
        self.assertTrue(any("único perfil completo" in d for d in deviations), deviations)
        # base completa + otro completo, sin activo: dos ambientes posibles → BLOCKED
        configs["default"] = {"spring.datasource.url": "jdbc:postgresql://h:5432/d",
                              "spring.datasource.username": "u", "spring.datasource.password": "p"}
        with self.assertRaisesRegex(Blocked, r"varios perfiles.*default, prod"):
            choose_spring_profile(configs)

    def test_el_cliente_que_restaura_depende_de_la_herramienta_y_no_se_inventa(self):
        # prueba real 2026-09-22: mariadb-dump 10.11.14 contra MySQL 5.7.36 → `mysql:10.11.14-MariaDB`, que no existe
        from pepper.rehydrate import _db_image
        db = {"engine": "mysql", "images": {"5.7.36": "mysql:5.7.36", "*": "mysql:{version}"},
              "tool_images": {"mysqldump:*": "mysql:{version}", "mariadb-dump:*": "mariadb:{version}"}}
        self.assertEqual(_db_image(db, {}, "10.11.14", key="tool_images", tool="mariadb-dump"), "mariadb:10.11.14")
        self.assertEqual(_db_image(db, {}, "8.0.36", key="tool_images", tool="mysqldump"), "mysql:8.0.36")
        # sin tool_images, la comodín de `images` no sustituye una versión que no es numérica
        self.assertEqual(_db_image(db, {}, "10.11.14-MariaDB"), "")
        self.assertEqual(_db_image(db, {}, "10.11.14"), "mysql:10.11.14")
        # postgres: pg_dump 17.2 sin tool_images sigue cayendo a la tabla del motor
        self.assertEqual(_db_image({"engine": "postgresql", "images": {"*": "postgres:{major}"}}, {}, "17.2", key="tool_images", tool="pg_dump"), "")
        self.assertEqual(_db_image({"engine": "postgresql", "images": {"*": "postgres:{major}"}}, {}, "17.2", tool="pg_dump"), "postgres:17")

    def test_la_plataforma_la_dicta_la_imagen_exigida_no_la_maquina(self):
        # prueba real 2026-09-22: mysql:5.7.36 solo existe para amd64 y en una Mac arm64 el pull fallaba
        from pepper.rehydrate import apply_platform, choose_platform
        catalog = {"mysql:5.7.36": ["linux/amd64"], "tomcat:7-jre7": ["linux/386", "linux/amd64", "linux/arm"],
                   "mariadb:10.11.14": ["linux/amd64", "linux/arm64"], "postgres:16": ["linux/amd64", "linux/arm64"],
                   "raro:1": ["linux/s390x"]}
        lookup = lambda image: catalog.get(image)
        self.assertEqual(choose_platform(["postgres:16", "mariadb:10.11.14"], "linux/arm64", lookup), ("linux/arm64", []))
        self.assertEqual(choose_platform(["mysql:5.7.36", "tomcat:7-jre7"], "linux/amd64", lookup), ("linux/amd64", []))
        platform, deviations = choose_platform(["mysql:5.7.36", "mariadb:10.11.14", "tomcat:7-jre7"], "linux/arm64", lookup)
        self.assertEqual(platform, "linux/amd64")
        self.assertEqual(len(deviations), 1)
        self.assertIn("mysql:5.7.36, tomcat:7-jre7", deviations[0])
        self.assertNotIn("mariadb", deviations[0])
        # lo que el registro no sabe decir no cambia nada
        self.assertEqual(choose_platform(["desconocida:9"], "linux/arm64", lookup), ("linux/arm64", []))
        with self.assertRaisesRegex(Blocked, "raro:1 no existe"):
            choose_platform(["raro:1"], "linux/arm64", lookup)
        # render deja la nativa en .env y la plantilla la usa
        plan = make_plan(self._legacy(), PROFILE)
        out = self.root / "out"
        render(plan, PROFILE, out)
        env = (out / ".env").read_text(encoding="utf-8")
        self.assertIn("PEPPER_PLATFORM=linux/", env)
        self.assertEqual(env.count("PEPPER_PLATFORM="), 1)
        self.assertIn("platform: ${PEPPER_PLATFORM}", (out / "docker-compose.yml").read_text(encoding="utf-8"))
        from unittest import mock
        with mock.patch("pepper.rehydrate.image_platforms", side_effect=lookup), \
             mock.patch("pepper.rehydrate.host_platform", return_value="linux/arm64"):
            plan.db_image, plan.db_tool_image = "mysql:5.7.36", "mariadb:10.11.14"
            apply_platform(plan, out)
        env = (out / ".env").read_text(encoding="utf-8")
        self.assertIn("PEPPER_PLATFORM=linux/amd64", env)
        self.assertEqual(env.count("PEPPER_PLATFORM="), 1)
        self.assertTrue(env.startswith('DB_PASSWORD="'), env)
        self.assertTrue(any("emulados" in d for d in plan.deviations), plan.deviations)

    def test_version_del_servidor_que_el_perfil_no_representa_es_blocked(self):
        # antes se usaba "la mayor de la tabla" (26) y se levantaba otro servidor
        with self.assertRaisesRegex(Blocked, "wildfly 999"):
            make_plan(self._legacy(notes="aplicaciones es un wildfly 999\n"), PROFILE, notes_path=self.root / "legacy" / "NOTAS.md")

    def test_servidor_sin_version_en_notas_es_blocked(self):
        with self.assertRaisesRegex(Blocked, "no dice la versión de wildfly"):
            make_plan(self._legacy(notes="corre en wildfly, no sé cuál\n"), PROFILE, notes_path=self.root / "legacy" / "NOTAS.md")

    def test_varios_respaldos_es_blocked_salvo_eleccion_humana(self):
        # P1-06: tomar el más grande levantaba el sistema contra la base equivocada
        legacy = self._legacy()
        write_custom_dump(legacy / "otro.dump", TABLES)
        with self.assertRaises(Blocked) as caught:
            make_plan(legacy, PROFILE)
        self.assertIn("2 respaldos", str(caught.exception))
        self.assertIn("--dump", str(caught.exception))
        plan = make_plan(legacy, PROFILE, dump_choice=legacy / "otro.dump")
        self.assertEqual(plan.dump.name, "otro.dump")
        self.assertTrue(any("elegido por la persona (--dump)" in n for n in plan.notes), plan.notes)
        with self.assertRaisesRegex(Blocked, "no es uno de los respaldos"):
            make_plan(legacy, PROFILE, dump_choice=legacy / "inexistente.dump")

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
    """Clasificar no es levantar: sin `service_template`, `components` termina en BLOCKED.

    Un perfil con `rehydrate.components` dejaba de bloquear y `make_plan` seguía con
    `artifacts[0]`: PEPPER observaba un sistema incompleto sin decirlo (revisión 2026-09-21).
    Desde 2026-09-22 el núcleo sí levanta varias piezas, pero solo si el perfil declara el
    fragmento de compose por pieza; un perfil que solo sabe clasificar sigue bloqueando, y
    la excepción sigue siendo un solo desplegable reconocido como backend.
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
        self.assertIn("service_template", mensaje)
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
        self.assertIn("service_template", str(caught.exception))

    def test_un_solo_backend_sigue_su_camino(self):
        from pepper.rehydrate import Blocked, make_plan

        self._jar("recursos-1.0.jar", "server:\n  port: 8099\n")
        with self.assertRaises(Blocked) as caught:
            make_plan(self.legacy, _profile(self.RECETA))  # se detiene más adelante (sin datasource), no por components
        self.assertNotIn("rehydrate.components", str(caught.exception))
        self.assertNotIn("una sola aplicación", str(caught.exception))


class SondaDeLaBaseTest(unittest.TestCase):
    """La sonda del perfil (`rehydrate.database.probe`) sustituye los marcadores también dentro del SQL."""

    def test_los_marcadores_dentro_del_sql_se_sustituyen(self):
        # prueba real 2026-09-22: `table_schema = '{db_name}'` viajaba literal y la sonda contaba 0 tablas de 157
        from pepper.rehydrate import db_client_command
        probe = {"client": ["mysql", "-uroot", "-N", "-B", "-e", "{sql}"], "env": {"MYSQL_PWD": "{db_password}"}}
        argv, env = db_client_command(probe, {"db_user": "root", "db_name": "openboxes", "db_password": "s3"},
                                      "select count(*) from information_schema.tables where table_schema = '{db_name}'")
        self.assertEqual(argv[-1], "select count(*) from information_schema.tables where table_schema = 'openboxes'")
        self.assertEqual(env, ["MYSQL_PWD=s3"])
        # llaves ajenas dentro del SQL no rompen la sustitución
        argv, _ = db_client_command({"client": probe["client"]}, {"db_name": "x"}, "select '{\"a\": 1}' as j, '{db_name}'")
        self.assertEqual(argv[-1], "select '{\"a\": 1}' as j, 'x'")

    def test_una_sonda_que_falla_no_es_una_base_vacia(self):
        from unittest import mock

        from pepper.rehydrate import Plan, ProbeFailed, _db_query
        plan = mock.Mock(spec=Plan); plan.db_user, plan.db_name, plan.db_password = "root", "openboxes", "s3"
        fake = mock.Mock(returncode=1, stdout="", stderr="ERROR 1045 (28000): Access denied for user 'root' (using password: s3)")
        with mock.patch("pepper.rehydrate._compose", return_value=fake):
            with self.assertRaises(ProbeFailed) as caught:
                _db_query(Path("/tmp"), plan, {"client": ["mysql", "-e", "{sql}"]}, "select 1")
        self.assertIn("Access denied", str(caught.exception))
        self.assertNotIn("s3", str(caught.exception))


class ConfiguracionExternaTest(unittest.TestCase):
    """Lo que el ambiente original resolvía fuera del artefacto entra como datos del perfil y queda declarado."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_los_parametros_que_el_motor_rechaza_se_quitan_y_quedan_declarados(self):
        from pepper.rehydrate import strip_url_params
        rules = [{"param": "sessionVariables", "why": "MySQL 5.7.36 no conoce storage_engine"}]
        url, dev = strip_url_params("jdbc:mysql://localhost:3306/app?autoReconnect=true&sessionVariables=storage_engine=InnoDB&zeroDateTimeBehavior=convertToNull", rules)
        self.assertEqual(url, "jdbc:mysql://localhost:3306/app?autoReconnect=true&zeroDateTimeBehavior=convertToNull")
        self.assertEqual(len(dev), 1); self.assertIn("storage_engine", dev[0]); self.assertIn("5.7.36", dev[0])
        self.assertEqual(strip_url_params("jdbc:mysql://h/app?sessionVariables=x", rules)[0], "jdbc:mysql://h/app")
        # sin el parámetro no hay desviación; sin reglas no se toca nada
        self.assertEqual(strip_url_params("jdbc:mysql://h/app?a=1", rules), ("jdbc:mysql://h/app?a=1", []))
        self.assertEqual(strip_url_params("jdbc:mysql://h/app?sessionVariables=x", []), ("jdbc:mysql://h/app?sessionVariables=x", []))

    def test_los_archivos_extra_del_perfil_se_renderizan_y_quedan_como_desviacion(self):
        import shutil
        from pepper.profiles import Profile
        legacy = self.root / "legacy"; legacy.mkdir()
        _make_war_with(legacy / "nominas-2.3.war", CONFIG_PROD)
        write_custom_dump(legacy / "respaldo.dump", TABLES)
        (legacy / "NOTAS.md").write_text("aplicaciones es un wildfly 21\n", encoding="utf-8")
        pdir = self.root / "perfil"; shutil.copytree(PROFILE.dir, pdir)
        (pdir / "externo.template.properties").write_text("datasource.url={{db_url}}\nbase={{db_name}}\n", encoding="utf-8")
        data = json.loads(json.dumps(PROFILE.data))
        data["rehydrate"]["extra_templates"] = [{"template": "externo.template.properties", "target": "externo.properties",
                                                 "deviation": "el servidor original lo tenía fuera del WAR"}]
        profile = Profile(id=PROFILE.id, dir=pdir, data=data)
        plan = make_plan(legacy, profile, notes_path=legacy / "NOTAS.md")
        self.assertEqual(plan.db_url, "jdbc:postgresql://10.42.7.2:5432/nominas_prod")
        out = self.root / "out"
        written = render(plan, profile, out)
        self.assertIn(out / "externo.properties", written)
        text = (out / "externo.properties").read_text(encoding="utf-8")
        self.assertEqual(text, "datasource.url=jdbc:postgresql://10.42.7.2:5432/nominas_prod\nbase=nominas_prod\n")
        self.assertEqual(oct((out / "externo.properties").stat().st_mode & 0o777), "0o600")
        self.assertTrue(any("externo.properties" in d and "fuera del WAR" in d for d in plan.deviations), plan.deviations)
        # un destino con ruta no se acepta: los archivos van junto al compose
        data["rehydrate"]["extra_templates"][0]["target"] = "../fuera.properties"
        with self.assertRaisesRegex(Blocked, "junto al compose"):
            render(plan, Profile(id=PROFILE.id, dir=pdir, data=data), self.root / "out2")


class LevantarVariasPiezasTest(unittest.TestCase):
    """Un sistema de varios desplegables se levanta como varios servicios (D32).

    Hermético: fat jars y un dist sintéticos, un respaldo escrito por el test, y el perfil real
    `java-springboot-fatjar-postgres` con sus plantillas. Sin Docker.
    """

    PROFILE = load_profile("java-springboot-fatjar-postgres")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.legacy = self.root / "legacy"; self.legacy.mkdir()
        write_custom_dump(self.legacy / "respaldo.dump", TABLES)

    def tearDown(self):
        self._tmp.cleanup()

    def _jar(self, name, config):
        with zipfile.ZipFile(self.legacy / name, "w") as z:
            z.writestr("BOOT-INF/lib/spring-core.jar", "x")
            z.writestr("BOOT-INF/classes/application.yml", config)

    def _front(self, name):
        with zipfile.ZipFile(self.legacy / name, "w") as z:
            z.writestr("dist/index.html", "<html></html>")

    DATASOURCE = ("server:\n  port: 8099\nspring:\n  datasource:\n"
                  "    url: jdbc:postgresql://10.100.0.2:5432/nominas_prod\n    username: nominas\n    password: s3cr3t\n")

    def _sistema(self):
        self._jar("descubrimiento-1.0.jar", "server:\n  port: 8097\neureka:\n  server:\n    enabled: true\n")
        self._jar("puerta-1.0.jar", "server:\n  port: 8098\nspring:\n  cloud.gateway:\n    enabled: true\n")
        self._jar("recursos-1.0.jar", self.DATASOURCE)
        self._front("front.zip")

    def test_el_plan_reparte_las_piezas_y_el_datasource_sale_del_backend(self):
        self._sistema()
        plan = make_plan(self.legacy, self.PROFILE)
        papeles = {c.name: (c.role, c.engine, c.port) for c in plan.components}
        self.assertEqual(papeles["descubrimiento"], ("discovery", "java", 8097))
        self.assertEqual(papeles["puerta"], ("gateway", "java", 8098))
        self.assertEqual(papeles["recursos"], ("backend", "java", 8099))
        self.assertEqual(papeles["front"], ("frontend", "static", 80))
        # la base sale de la pieza backend, no del primer artefacto por orden alfabético
        self.assertEqual((plan.db_name, plan.db_user, plan.db_ip), ("nominas_prod", "nominas", "10.100.0.2"))
        self.assertEqual(plan.artifact.name, "recursos-1.0.jar")
        # el ingress entra por la puerta de enlace (ingress_role del perfil)
        self.assertEqual((plan.entry_component, plan.entry_ip, plan.entry_port), ("puerta", papeles_ip(plan, "puerta"), 8098))
        self.assertTrue(any("4 piezas" in n and "entra por `puerta`" in n for n in plan.notes), plan.notes)
        # cada pieza tiene su IP, distinta de la de la base y de las demás
        ips = [c.ip for c in plan.components]
        self.assertEqual(len(set(ips)), 4)
        self.assertNotIn(plan.db_ip, ips)

    def test_el_compose_trae_un_servicio_por_pieza_y_el_ingress_apunta_a_la_puerta(self):
        import re
        self._sistema()
        plan = make_plan(self.legacy, self.PROFILE)
        out = self.root / "rehydrate"
        render(plan, self.PROFILE, out)
        compose = (out / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"\{\{\w+\}\}", compose), [], "toda variable sustituida, también en las piezas")
        for name in ("descubrimiento", "puerta", "recursos", "front"):
            self.assertIn(f"\n  {name}:\n", compose, f"falta el servicio de `{name}`")
            self.assertIn(f"aliases: [{name}]", compose, f"`{name}` debe ser alcanzable por su nombre")
        # cada motor usa su plantilla: el front va con httpd y sin SPRING_PROFILES_ACTIVE
        self.assertIn("image: httpd:2.4-alpine", compose)
        self.assertEqual(compose.count("SPRING_PROFILES_ACTIVE"), 3, "solo las piezas java")
        self.assertIn("htdocs/front.zip:ro", compose)
        # el ingress entra por la puerta, no por el backend
        self.assertRegex(compose, r'--upstream", "10\.100\.0\.\d+:8098"')
        self.assertNotIn(":8099\"]", compose)

    def test_el_compose_de_varias_piezas_sigue_aislado(self):
        self._sistema()
        plan = make_plan(self.legacy, self.PROFILE)
        out = self.root / "rehydrate"
        render(plan, self.PROFILE, out)
        try:
            from pepper.isolate import check_static, resolve_compose
            compose_dict, resolved = resolve_compose(out / "docker-compose.yml")
        except RuntimeError:
            self.skipTest("sin docker compose ni pyyaml para resolver el compose")
        report = check_static(compose_dict, plan.external_hosts, "ingress", resolved=resolved, compose_dir=out)
        self.assertNotEqual(report.verdict, "FAILED", [f.check for f in report.errors])

    def test_environment_json_declara_cada_pieza_y_valida(self):
        self._sistema()
        plan = make_plan(self.legacy, self.PROFILE)
        out = self.root / "rehydrate"; render(plan, self.PROFILE, out)
        env_path, _ = write_environment(plan, self.PROFILE, "READY", [], [], self.root / "docs", out)
        env = json.loads(env_path.read_text(encoding="utf-8"))
        nombres = [c["name"] for c in env["components"]]
        for name in ("descubrimiento", "puerta", "recursos", "front", "db", "ingress", "stub"):
            self.assertIn(name, nombres)
        self.assertNotIn("app", nombres, "con varias piezas no hay un servicio llamado `app`")
        puerta = next(c for c in env["components"] if c["name"] == "puerta")
        self.assertEqual(puerta["role"], "proxy", "el papel se dice en el vocabulario del contrato")
        self.assertIn("gateway", puerta["engine"], "y el papel original no se pierde")
        self.assertIn("← ingress", puerta["endpoint"])
        problemas = validate_file(env_path, "environment")
        self.assertEqual(problemas, [], problemas)

    def test_una_pieza_sin_papel_detiene_el_plan(self):
        self._sistema()
        (self.legacy / "misterio.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)
        with self.assertRaisesRegex(Blocked, "no reconoce 1 de los 5"):
            make_plan(self.legacy, self.PROFILE)

    def test_dos_backends_no_se_adivinan(self):
        self._sistema()
        self._jar("otro-1.0.jar", self.DATASOURCE.replace("nominas_prod", "otra_base"))
        with self.assertRaisesRegex(Blocked, "varias piezas con el papel 'backend'"):
            make_plan(self.legacy, self.PROFILE)

    def test_sin_la_pieza_que_habla_con_la_base_se_detiene(self):
        self._jar("puerta-1.0.jar", "server:\n  port: 8098\nspring:\n  cloud.gateway:\n    enabled: true\n")
        self._front("front.zip")
        with self.assertRaisesRegex(Blocked, "ninguna pieza tiene el papel 'backend'"):
            make_plan(self.legacy, self.PROFILE)

    def test_la_puerta_de_entrada_no_se_adivina_cuando_hay_varias(self):
        from pepper.rehydrate import Component, ingress_component
        piezas = [Component(name=n, artifact=Path(f"{n}.jar"), role=r, engine="java", image="i",
                            ip=f"10.100.0.1{i}", port=8080 + i)
                  for i, (n, r) in enumerate([("a", "frontend"), ("b", "frontend"), ("c", "backend")])]
        with self.assertRaisesRegex(Blocked, "varias piezas con el papel 'frontend'"):
            ingress_component(piezas, {})
        self.assertEqual(ingress_component(piezas, {"ingress_role": "backend"}).name, "c")
        # `ingress_role` es preferencia, no requisito: sin gateway se entra por lo que haya, anotado
        notas = []
        with self.assertRaisesRegex(Blocked, "varias piezas con el papel 'frontend'"):
            ingress_component(piezas, {"ingress_role": "gateway"}, notas)
        solo_backend = [p for p in piezas if p.role == "backend"]
        self.assertEqual(ingress_component(solo_backend, {"ingress_role": "gateway"}, notas).name, "c")
        self.assertTrue(any("no tiene ninguna" in n for n in notas), notas)


def papeles_ip(plan, name):
    return next(c.ip for c in plan.components if c.name == name)
