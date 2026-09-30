"""`rehydrate.datasource` con lectores genéricos de FORMATO: clave=valor, JSON y XML, y `url_pattern`.

Hasta la auditoría 2026-09-29 el núcleo sabía leer dos frameworks (YAML de Spring, Groovy
compilado) y una forma de URL (`jdbc:`); un `.env` de Laravel, un `appsettings.json`, un
`Web.config`, un `database.yml` de Rails o el `standalone.xml` de WildFly respondían BLOCKED
con un motivo falso. Hermético: artefactos y archivos escritos por la prueba; sin Docker.
"""

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pepper.inspect.readers import configfiles  # noqa: E402
from pepper.profiles import Profile  # noqa: E402
from pepper.rehydrate import Blocked, datasource_facts, discover_datasource, make_plan  # noqa: E402
from tests.test_sqldump import MYSQL_APP  # noqa: E402
from tests.test_systemmap import TABLES, write_custom_dump  # noqa: E402

ENV_LARAVEL = """# Laravel
APP_NAME="Citas Médicas"
APP_URL=http://citas.institucion.example
export DB_CONNECTION=mysql
DB_HOST=10.7.0.9
DB_PORT=3306
DB_DATABASE=citas_prod
DB_USERNAME=citas
DB_PASSWORD="s3c#ret"   # con comilla se respeta el #
MAIL_HOST=smtp.correo.example
MAIL_PORT=587
"""

DATABASE_YML = """default: &default
  adapter: postgresql
  encoding: unicode

development:
  <<: *default
  database: app_dev

production:
  <<: *default
  host: db.interno.example
  database: app_prod
  username: app
  password: <%= ENV['DB_PASSWORD'] %>
"""

APPSETTINGS = """{
  "Logging": {"LogLevel": {"Default": "Information"}},
  "ConnectionStrings": {
    "Default": "Server=10.7.0.4,1433;Database=Ventas;User Id=app;Password=p4ss",
    "Auditoria": "Server=10.7.0.5;Database=Audit;User Id=aud;Password=x"
  },
  "Servicios": {"Renapo": {"Url": "https://renapo.institucion.example/api"}}
}
"""

WEB_CONFIG = """<?xml version="1.0"?>
<configuration>
  <connectionStrings>
    <add name="Default" connectionString="Server=srv-sql-01;Database=Ventas;User Id=app;Password=p4ss" providerName="System.Data.SqlClient" />
    <add name="Otra" connectionString="Server=x;Database=y;User Id=u;Password=p" />
  </connectionStrings>
  <appSettings>
    <add key="SmtpHost" value="smtp.correo.example" />
  </appSettings>
</configuration>
"""

STANDALONE = """<subsystem xmlns="urn:jboss:domain:datasources:4.0">
    <datasources>
        <datasource jndi-name="java:jboss/datasources/SolicitudesDS" pool-name="SolicitudesDS" enabled="true">
            <connection-url>jdbc:postgresql://srv-bd-01:5432/solicitudes</connection-url>
            <driver>postgresql</driver>
            <security>
                <user-name>solicitudes_app</user-name>
                <password>******</password>
            </security>
        </datasource>
    </datasources>
</subsystem>
"""

MYSQL_DB = {"engine": "mysql", "engine_aliases": ["mariadb"], "default_port": 3306,
            "images": {"*": "mysql:{version}"}, "tool_images": {"mysqldump:*": "mysql:{version}"},
            "dump": {"format": "sql_text", "suffixes": [".sql"]}, "localhost": "blocked", "subnet_hint": "10.100.0",
            "probe": {"client": ["mysql", "-e", "{sql}"], "tables_sql": "x", "marker_sql": "y"}}
PG_DB = {"engine": "postgresql", "default_port": 5432, "images": {"*": "postgres:{major}"},
         "dump": {"format": "pg_dump_custom", "suffixes": [".dump"]}, "localhost": "blocked",
         "probe": {"client": ["psql", "-c", "{sql}"], "tables_sql": "x", "marker_sql": "y"}}


def _perfil(recipe):
    return Profile(id="perfil-de-prueba", dir=ROOT / "profiles" / "java-springboot-jsf-postgres",
                   data={"id": "perfil-de-prueba", "rehydrate": recipe})


class ClaveValorTest(unittest.TestCase):
    def test_env_properties_ini_y_yaml_plano(self):
        cfg = configfiles.parse_key_value(ENV_LARAVEL)
        self.assertEqual(cfg["APP_NAME"], "Citas Médicas")
        self.assertEqual(cfg["APP_URL"], "http://citas.institucion.example", "el separador es el que aparece primero: `=`")
        self.assertEqual(cfg["DB_CONNECTION"], "mysql", "`export` no forma parte de la clave")
        self.assertEqual(cfg["DB_PASSWORD"], "s3c#ret", "las comillas protegen el #")
        self.assertEqual(cfg["MAIL_PORT"], "587")
        ini = configfiles.parse_key_value("; comentario\n[database]\nhost = 10.1.1.1\nport=5432\n[mail]\nhost=smtp.x\n")
        self.assertEqual((ini["database.host"], ini["database.port"], ini["mail.host"]), ("10.1.1.1", "5432", "smtp.x"))
        yml = configfiles.parse_key_value(DATABASE_YML)
        self.assertEqual(yml["production.host"], "db.interno.example")
        self.assertEqual(yml["production.database"], "app_prod")
        self.assertEqual(yml["production.password"], "<%= ENV['DB_PASSWORD'] %>")
        self.assertEqual(yml["development.database"], "app_dev")
        self.assertNotIn("production.<<", yml, "los anclajes YAML no son claves")
        self.assertEqual(configfiles.parse_key_value("url: jdbc:mysql://h/db # prod\n")["url"], "jdbc:mysql://h/db")


class JsonYXmlTest(unittest.TestCase):
    def test_json_aplanado_con_puntos_e_indices(self):
        flat = configfiles.parse_json_flat(APPSETTINGS)
        self.assertEqual(flat["ConnectionStrings.Default"], "Server=10.7.0.4,1433;Database=Ventas;User Id=app;Password=p4ss")
        self.assertEqual(flat["Servicios.Renapo.Url"], "https://renapo.institucion.example/api")
        self.assertEqual(configfiles.parse_json_flat('{"a": [{"b": 1}, "x"], "c": true}'), {"a.0.b": "1", "a.1": "x", "c": "true"})
        with self.assertRaisesRegex(configfiles.ConfigError, "no es JSON válido"):
            configfiles.parse_json_flat("{no", "appsettings.json")

    def test_xml_ruta_con_predicado_atributo_y_espacio_de_nombres(self):
        path = "configuration/connectionStrings/add[@name=Default]/@connectionString"
        self.assertEqual(configfiles.lookup_xml(WEB_CONFIG, path), "Server=srv-sql-01;Database=Ventas;User Id=app;Password=p4ss")
        self.assertEqual(configfiles.lookup_xml(WEB_CONFIG, "configuration/connectionStrings/add[@name=Otra]/@connectionString"),
                         "Server=x;Database=y;User Id=u;Password=p")
        self.assertEqual(configfiles.lookup_xml(WEB_CONFIG, "configuration/appSettings/add[@key=SmtpHost]/@value"), "smtp.correo.example")
        self.assertIsNone(configfiles.lookup_xml(WEB_CONFIG, "configuration/connectionStrings/add[@name=NoExiste]/@connectionString"))
        self.assertIsNone(configfiles.lookup_xml(WEB_CONFIG, "otraRaiz/x"))
        # el espacio de nombres de WildFly no estorba: se compara el nombre local
        self.assertEqual(configfiles.lookup_xml(STANDALONE, "subsystem/datasources/datasource[@pool-name=SolicitudesDS]/connection-url"),
                         "jdbc:postgresql://srv-bd-01:5432/solicitudes")
        self.assertEqual(configfiles.lookup_xml(STANDALONE, "subsystem/datasources/datasource/security/user-name"), "solicitudes_app")
        # `//` busca el primer segmento en cualquier nivel: el mismo perfil sirve para un standalone.xml completo y un fragmento
        completo = "<server><profile>" + STANDALONE + "</profile></server>"
        self.assertEqual(configfiles.lookup_xml(completo, "//datasources/datasource/connection-url"),
                         "jdbc:postgresql://srv-bd-01:5432/solicitudes")
        self.assertEqual(configfiles.lookup_xml(STANDALONE, "//datasources/datasource/connection-url"),
                         "jdbc:postgresql://srv-bd-01:5432/solicitudes")
        self.assertIsNone(configfiles.lookup_xml(completo, "//nada/connection-url"))
        # dos <datasource> (ExampleDS y el de la app): el primero no es "el" datasource; se pide un predicado
        dos = STANDALONE.replace("</datasource>", "</datasource><datasource pool-name=\"ExampleDS\"><connection-url>jdbc:h2:mem:x</connection-url></datasource>")
        with self.assertRaisesRegex(configfiles.ConfigError, "casa 2 elementos.*predicado"):
            configfiles.lookup_xml(dos, "//datasources/datasource/connection-url", "standalone.xml")
        self.assertEqual(configfiles.lookup_xml(dos, "//datasources/datasource[@pool-name=SolicitudesDS]/connection-url"),
                         "jdbc:postgresql://srv-bd-01:5432/solicitudes")
        flat = configfiles.parse_xml_flat(WEB_CONFIG)
        self.assertEqual(flat["configuration/connectionStrings/add/@name"], "Default")
        self.assertEqual(flat["configuration/connectionStrings/add[1]/@name"], "Otra", "los hermanos repetidos se indexan")
        with self.assertRaisesRegex(configfiles.ConfigError, "no es XML válido"):
            configfiles.lookup_xml("<a><b></a>", "a/b", "Web.config")
        with self.assertRaisesRegex(configfiles.ConfigError, "segmento de ruta XML"):
            configfiles.lookup_xml(WEB_CONFIG, "configuration/add[name=x]")


class UrlPatternTest(unittest.TestCase):
    def test_sin_url_pattern_sigue_la_jdbc_de_siempre(self):
        self.assertEqual(configfiles.parse_datasource_url("jdbc:postgresql://10.42.7.2:5432/nominas_prod"),
                         {"engine": "postgresql", "host": "10.42.7.2", "port": "5432", "db": "nominas_prod"})
        self.assertEqual(configfiles.parse_datasource_url("jdbc:mysql://localhost/app?x=1"),
                         {"engine": "mysql", "host": "localhost", "db": "app"})
        with self.assertRaisesRegex(configfiles.ConfigError, "no entiendo la URL.*url_pattern"):
            configfiles.parse_datasource_url("postgres://u:p@h:5432/db")

    def test_url_pattern_del_perfil_reemplaza_a_la_jdbc(self):
        casos = [
            ("postgres://u:p@h:5432/db", r"(?P<engine>postgres)://(?P<user>[^:]+):(?P<password>[^@]*)@(?P<host>[^:/]+)(?::(?P<port>\d+))?/(?P<db>\w+)",
             {"engine": "postgres", "user": "u", "password": "p", "host": "h", "port": "5432", "db": "db"}),
            ("Server=h;Database=db;User Id=u;Password=p", r"Server=(?P<host>[^;,]+)(?:,(?P<port>\d+))?;Database=(?P<db>[^;]+);User Id=(?P<user>[^;]+);Password=(?P<password>[^;]*)",
             {"host": "h", "db": "db", "user": "u", "password": "p"}),
            ("mysql:host=h;dbname=db", r"(?P<engine>mysql):host=(?P<host>[^;]+)(?:;port=(?P<port>\d+))?;dbname=(?P<db>\w+)",
             {"engine": "mysql", "host": "h", "db": "db"}),
        ]
        for url, pattern, expected in casos:
            self.assertEqual(configfiles.parse_datasource_url(url, pattern), expected, url)
        with self.assertRaisesRegex(configfiles.ConfigError, "no entiendo la URL del datasource con la url_pattern"):
            configfiles.parse_datasource_url("jdbc:oracle:thin:@h:1521:x", casos[0][1])

    def test_una_url_pattern_sin_host_o_db_es_error_del_perfil(self):
        with self.assertRaisesRegex(configfiles.ConfigError, "faltan: db"):
            configfiles.compile_url_pattern(r"(?P<host>\w+)")
        with self.assertRaisesRegex(configfiles.ConfigError, "no significan nada"):
            configfiles.compile_url_pattern(r"(?P<host>\w+)/(?P<db>\w+)/(?P<servidor>\w+)")
        with self.assertRaisesRegex(configfiles.ConfigError, "inválida"):
            configfiles.compile_url_pattern(r"(?P<host>(")

    def test_facts_combina_url_y_claves_sueltas_sin_adivinar(self):
        spec = {"url_pattern": r"Server=(?P<host>[^;,]+)(?:,(?P<port>\d+))?;Database=(?P<db>[^;]+);User Id=(?P<user>[^;]+);Password=(?P<password>[^;]*)"}
        facts = datasource_facts({"url": "Server=10.7.0.4,1433;Database=Ventas;User Id=app;Password=p4ss"}, spec,
                                 {"engine": "sqlserver", "default_port": 1433})
        self.assertEqual((facts.engine, facts.host, facts.port, facts.db, facts.user, facts.password),
                         ("sqlserver", "10.7.0.4", 1433, "Ventas", "app", "p4ss"))
        self.assertTrue(any("no declara el motor" in n for n in facts.notes), "el motor lo puso el perfil y queda dicho")
        # una clave suelta manda sobre lo que la URL no trae; el puerto cae al default del perfil
        facts = datasource_facts({"url": "Server=h;Database=db;User Id=u;Password=", "engine": "mssql"}, spec, {"engine": "sqlserver", "default_port": 1433})
        self.assertEqual((facts.engine, facts.port, facts.password), ("mssql", 1433, ""))
        self.assertEqual(facts.notes, [])
        # url_pattern con spring_config: la URL de Spring puede no ser jdbc:motor://host/base
        spring = {"url_pattern": r"jdbc:(?P<engine>sqlserver)://(?P<host>[^:;]+)(?::(?P<port>\d+))?;databaseName=(?P<db>\w+)"}
        facts = datasource_facts({"url": "jdbc:sqlserver://h:1433;databaseName=db", "username": "u", "password": "p"}, spring, {"engine": "sqlserver"})
        self.assertEqual((facts.engine, facts.host, facts.port, facts.db, facts.user), ("sqlserver", "h", 1433, "db", "u"))
        with self.assertRaisesRegex(Blocked, "no trae el usuario"):
            datasource_facts({"url": "jdbc:mysql://h/db", "password": "p"}, {}, {"engine": "mysql"})
        with self.assertRaisesRegex(Blocked, "no trae la contraseña"):
            datasource_facts({"url": "jdbc:mysql://h/db", "username": "u"}, {}, {"engine": "mysql"})
        with self.assertRaisesRegex(Blocked, "no dice el host"):
            datasource_facts({"url": "", "db": "x", "username": "u", "password": "p"}, {}, {"engine": "mysql"})
        with self.assertRaisesRegex(Blocked, "no es un número"):
            datasource_facts({"url": "", "host": "h", "port": "abc", "db": "x", "username": "u", "password": "p"}, {}, {"engine": "mysql"})


class LectoresGenericosTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.legacy = self.root / "legacy"
        self.legacy.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _zip(self, name, members):
        path = self.legacy / name
        with zipfile.ZipFile(path, "w") as z:
            for member, body in members.items():
                z.writestr(member, body)
        return path

    ENV_SPEC = {"mechanism": "key_value", "files": [".env"],
                "keys": {"engine": "DB_CONNECTION", "host": "DB_HOST", "port": "DB_PORT", "db": "DB_DATABASE",
                         "user": "DB_USERNAME", "password": "DB_PASSWORD"}}

    def test_key_value_lee_un_env_de_laravel(self):
        app = self._zip("citas.zip", {".env": ENV_LARAVEL, "public/index.php": "<?php"})
        name, cfg, deviations, creds = discover_datasource(app, {"datasource": self.ENV_SPEC})
        self.assertEqual(name, ".env")
        self.assertEqual(creds, {"engine": "mysql", "host": "10.7.0.9", "port": "3306", "db": "citas_prod",
                                 "username": "citas", "password": "s3c#ret", "url": ""})
        self.assertEqual(cfg["MAIL_HOST"], "smtp.correo.example", "la configuración plana alimenta los hosts externos")
        self.assertEqual(deviations, [])

    def test_key_value_al_plan_completo(self):
        self._zip("citas.zip", {".env": ENV_LARAVEL, "public/index.php": "<?php"})
        (self.legacy / "respaldo.sql").write_text(MYSQL_APP, encoding="utf-8")
        (self.legacy / "NOTAS.md").write_text("corre en apache 2\n", encoding="utf-8")
        recipe = {"artifact_suffixes": [".zip"], "datasource": self.ENV_SPEC, "database": MYSQL_DB,
                  "descriptors": {"apache": ["public/index.php"]}, "server_images": {"apache": {"2": "php:7.4-apache"}}}
        plan = make_plan(self.legacy, _perfil(recipe))
        self.assertEqual((plan.db_engine, plan.db_ip, plan.db_port, plan.db_name, plan.db_user, plan.db_password),
                         ("mysql", "10.7.0.9", 3306, "citas_prod", "citas", "s3c#ret"))
        self.assertEqual(plan.db_url, "", "no hay URL: la configuración vino en claves sueltas")
        self.assertEqual(plan.spring_profile, ".env")
        self.assertEqual(plan.external_hosts, ["citas.institucion.example", "smtp.correo.example"])
        self.assertIn("587", plan.stub_ports.split(","))
        self.assertEqual((plan.server, plan.server_image, plan.db_image), ("apache", "php:7.4-apache", "mysql:5.7.36"))
        self.assertNotIn("s3c#ret", " ".join(plan.deviations + plan.notes))

    def test_lo_que_falta_se_nombra_con_archivo_y_clave(self):
        app = self._zip("citas.zip", {".env": ENV_LARAVEL.replace("DB_HOST=10.7.0.9\n", "")})
        with self.assertRaisesRegex(Blocked, r"en citas\.zip!\.env falta `DB_HOST` \(host de la base\)"):
            discover_datasource(app, {"datasource": self.ENV_SPEC})
        with self.assertRaisesRegex(Blocked, r"ninguno de los archivos.*\.env.*existe en otra\.zip"):
            discover_datasource(self._zip("otra.zip", {"config.php": "<?php"}), {"datasource": self.ENV_SPEC})
        sin_files = {"mechanism": "key_value", "keys": self.ENV_SPEC["keys"]}
        with self.assertRaisesRegex(Blocked, "no `datasource.files`"):
            discover_datasource(app, {"datasource": sin_files})
        sin_user = {"mechanism": "key_value", "files": [".env"], "keys": {"host": "DB_HOST", "db": "DB_DATABASE"}}
        with self.assertRaisesRegex(Blocked, "no `user` y `password`"):
            discover_datasource(app, {"datasource": sin_user})
        campo_raro = {"mechanism": "key_value", "files": [".env"], "keys": {"servidor": "DB_HOST"}}
        with self.assertRaisesRegex(Blocked, "campos que el plan no conoce: servidor"):
            discover_datasource(app, {"datasource": campo_raro})
        with self.assertRaisesRegex(Blocked, "--config-profile no aplica al mecanismo key_value"):
            discover_datasource(app, {"datasource": self.ENV_SPEC}, override="prod")

    def test_dos_archivos_con_valores_distintos_es_ambiguedad_no_el_ultimo_gana(self):
        app = self._zip("citas.zip", {".env": ENV_LARAVEL, ".env.production": ENV_LARAVEL.replace("citas_prod", "citas_qa")})
        spec = dict(self.ENV_SPEC, files=[".env*"])
        with self.assertRaisesRegex(Blocked, r"`DB_DATABASE`.*valores distintos.*citas_prod.*citas_qa") as caught:
            discover_datasource(app, {"datasource": spec})
        self.assertNotIn("s3c#ret", str(caught.exception))
        # dos archivos que coinciden (o se complementan) no son ambigüedad
        app = self._zip("citas2.zip", {".env": ENV_LARAVEL.replace("DB_PASSWORD=\"s3c#ret\"   # con comilla se respeta el #\n", ""),
                                       ".env.secrets": "DB_PASSWORD=otra\n"})
        name, _, _, creds = discover_datasource(app, {"datasource": spec})
        self.assertEqual((name, creds["password"]), (".env+.env.secrets", "otra"))

    def test_una_referencia_sin_resolver_en_la_contrasena_es_blocked(self):
        self._zip("citas.zip", {".env": ENV_LARAVEL.replace('DB_PASSWORD="s3c#ret"', "DB_PASSWORD=<%= ENV['DB_PASSWORD'] %>"),
                                "public/index.php": "<?php"})
        (self.legacy / "respaldo.sql").write_text(MYSQL_APP, encoding="utf-8")
        recipe = {"artifact_suffixes": [".zip"], "datasource": self.ENV_SPEC, "database": MYSQL_DB,
                  "descriptors": {"apache": ["public/index.php"]}, "server_images": {"apache": {"2": "php:7.4-apache"}}}
        with self.assertRaisesRegex(Blocked, "sin resolver"):
            make_plan(self.legacy, _perfil(recipe), notes_path=self.root / "no-hay-notas.md")

    def test_key_value_lee_un_database_yml_de_rails_por_entorno(self):
        app = self._zip("app.zip", {"config/database.yml": DATABASE_YML.replace("<%= ENV['DB_PASSWORD'] %>", "clave")})
        spec = {"mechanism": "key_value", "files": ["config/database.yml"],
                "keys": {"engine": "production.adapter", "host": "production.host", "db": "production.database",
                         "user": "production.username", "password": "production.password"}}
        with self.assertRaisesRegex(Blocked, r"falta `production\.adapter`"):
            discover_datasource(app, {"datasource": spec})   # `<<: *default` no se expande: el anclaje no es una clave
        spec["keys"].pop("engine")
        _, _, _, creds = discover_datasource(app, {"datasource": spec})
        facts = datasource_facts(creds, spec, PG_DB)
        self.assertEqual((facts.engine, facts.host, facts.port, facts.db, facts.user, facts.password),
                         ("postgresql", "db.interno.example", 5432, "app_prod", "app", "clave"))
        self.assertTrue(facts.notes, "el motor lo aportó el perfil, y se dice")

    def test_json_con_url_pattern(self):
        app = self._zip("ventas.zip", {"appsettings.json": APPSETTINGS, "Ventas.dll": "MZ"})
        spec = {"mechanism": "json", "files": ["appsettings*.json"], "keys": {"url": "ConnectionStrings.Default"},
                "url_pattern": r"Server=(?P<host>[^;,]+)(?:,(?P<port>\d+))?;Database=(?P<db>[^;]+);User Id=(?P<user>[^;]+);Password=(?P<password>[^;]*)"}
        name, cfg, _, creds = discover_datasource(app, {"datasource": spec})
        self.assertEqual(name, "appsettings.json")
        self.assertEqual(creds["url"], "Server=10.7.0.4,1433;Database=Ventas;User Id=app;Password=p4ss")
        self.assertEqual(cfg["Servicios.Renapo.Url"], "https://renapo.institucion.example/api")
        facts = datasource_facts(creds, spec, {"engine": "sqlserver", "default_port": 1433})
        self.assertEqual((facts.engine, facts.host, facts.port, facts.db, facts.user, facts.password),
                         ("sqlserver", "10.7.0.4", 1433, "Ventas", "app", "p4ss"))
        with self.assertRaisesRegex(Blocked, r"falta `ConnectionStrings\.Principal`"):
            discover_datasource(app, {"datasource": dict(spec, keys={"url": "ConnectionStrings.Principal"})})
        roto = self._zip("roto.zip", {"appsettings.json": "{no es json"})
        with self.assertRaisesRegex(Blocked, "no es JSON válido"):
            discover_datasource(roto, {"datasource": spec})

    def test_xml_web_config_con_predicado(self):
        app = self._zip("ventas.zip", {"Web.config": WEB_CONFIG})
        spec = {"mechanism": "xml", "files": ["Web.config"],
                "keys": {"url": "configuration/connectionStrings/add[@name=Default]/@connectionString"},
                "url_pattern": r"Server=(?P<host>[^;,]+);Database=(?P<db>[^;]+);User Id=(?P<user>[^;]+);Password=(?P<password>[^;]*)"}
        _, cfg, _, creds = discover_datasource(app, {"datasource": spec})
        self.assertEqual(creds["url"], "Server=srv-sql-01;Database=Ventas;User Id=app;Password=p4ss")
        self.assertEqual(cfg["configuration/appSettings/add/@value"], "smtp.correo.example")
        facts = datasource_facts(creds, spec, {"engine": "sqlserver", "default_port": 1433})
        self.assertEqual((facts.host, facts.db, facts.user), ("srv-sql-01", "Ventas", "app"))
        with self.assertRaisesRegex(Blocked, r"falta `configuration/connectionStrings/add\[@name=Nada\]/@connectionString`"):
            discover_datasource(app, {"datasource": dict(spec, keys={"url": "configuration/connectionStrings/add[@name=Nada]/@connectionString"})})

    def test_xml_standalone_de_wildfly_junto_al_war_en_legacy(self):
        # el datasource de WildFly no vive en el WAR: vive en el standalone.xml del servidor, que la persona deja en legacy/
        war = self._zip("solicitudes.war", {"WEB-INF/jboss-web.xml": "<jboss-web/>", "WEB-INF/classes/x.txt": "x"})
        (self.legacy / "configuration").mkdir()
        (self.legacy / "configuration" / "standalone-fragment.xml").write_text(STANDALONE, encoding="utf-8")
        write_custom_dump(self.legacy / "respaldo.dump", TABLES)
        (self.legacy / "NOTAS.md").write_text("producción es wildfly 21\n", encoding="utf-8")
        ds = "subsystem/datasources/datasource[@pool-name=SolicitudesDS]"
        spec = {"mechanism": "xml", "files": ["configuration/standalone*.xml"],
                "keys": {"url": f"{ds}/connection-url", "user": f"{ds}/security/user-name", "password": f"{ds}/security/password"}}
        name, _, _, creds = discover_datasource(war, {"datasource": spec}, legacy_dir=self.legacy)
        self.assertEqual(name, "standalone-fragment.xml")
        self.assertEqual(creds, {"url": "jdbc:postgresql://srv-bd-01:5432/solicitudes", "username": "solicitudes_app", "password": "******"})
        with self.assertRaisesRegex(Blocked, r"existe en solicitudes\.war ni en "):
            discover_datasource(war, {"datasource": spec}, legacy_dir=self.root)
        recipe = {"datasource": spec, "database": PG_DB, "descriptors": {"wildfly": ["WEB-INF/jboss-web.xml"]},
                  "server_images": {"wildfly": {"21": "jboss/wildfly:21.0.2.Final"}}}
        plan = make_plan(self.legacy, _perfil(recipe))
        self.assertEqual((plan.db_engine, plan.db_name, plan.db_user, plan.db_port, plan.db_alias), ("postgresql", "solicitudes", "solicitudes_app", 5432, "srv-bd-01"))
        self.assertEqual(plan.db_url, "jdbc:postgresql://srv-bd-01:5432/solicitudes")
        self.assertEqual(plan.server_image, "jboss/wildfly:21.0.2.Final")

    def test_un_mecanismo_desconocido_nombra_los_cinco(self):
        app = self._zip("x.zip", {".env": ENV_LARAVEL})
        with self.assertRaisesRegex(Blocked, "desconocido.*spring_config | groovy_config | key_value | json | xml"):
            discover_datasource(app, {"datasource": {"mechanism": "toml"}})


if __name__ == "__main__":
    unittest.main()
