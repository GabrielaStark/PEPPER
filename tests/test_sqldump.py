"""Respaldos SQL en texto: el lector, la detección del esquema de sistema y su mecanismo en `pepper map`.

Hermético: los respaldos se escriben en el test (mysqldump y pg_dump plano sintéticos).
"""

import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pepper.inspect import sqldump  # noqa: E402
from pepper.inspect.systemmap import build_map  # noqa: E402
from pepper.rehydrate import Blocked, db_client_command, read_dump_facts  # noqa: E402

MYSQL_APP = """-- MySQL dump 10.13  Distrib 5.7.36, for Linux (x86_64)
--
-- Host: 10.0.0.9    Database: almacen_prod
-- ------------------------------------------------------
-- Server version\t5.7.36

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
DROP TABLE IF EXISTS `estatus`;
CREATE TABLE `estatus` (
  `id` int(11) NOT NULL,
  `nombre` varchar(50) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_nombre` (`nombre`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;
INSERT INTO `estatus` VALUES (1,'ABIERTO'),(2,'CERRADO'),(3,'It''s \\'raro\\'');
CREATE TABLE `movimiento` (
  `id` int(11) NOT NULL,
  `status` varchar(20) DEFAULT NULL,
  `fecha_movimiento` datetime DEFAULT NULL,
  `usuario_email` varchar(80) DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB;
INSERT INTO `movimiento` VALUES (1,'ABIERTO','2024-01-05 10:00:00','ana@correo.example'),(2,'CERRADO','2025-03-01 09:00:00',NULL),(3,'ABIERTO','2025-06-01 09:00:00','x@y.example');
CREATE TABLE `usuario` (
  `id` int(11) NOT NULL,
  `username` varchar(80) DEFAULT NULL,
  `password` varchar(80) DEFAULT NULL
) ENGINE=InnoDB;
INSERT INTO `usuario` VALUES (1,'admin','abc');
/*!50001 CREATE ALGORITHM=UNDEFINED */
/*!50013 DEFINER=`app`@`%` SQL SECURITY DEFINER */
/*!50001 VIEW `v_abiertos` AS select `m`.`id` AS `id` from `movimiento` `m` where (`m`.`status` = 'ABIERTO') */;
DELIMITER ;;
/*!50003 CREATE*/ /*!50017 DEFINER=`app`@`%`*/ /*!50003 TRIGGER `trg_mov` BEFORE INSERT ON `movimiento` FOR EACH ROW BEGIN
  SET NEW.status = 'ABIERTO';
END */;;
DELIMITER ;
DELIMITER ;;
CREATE DEFINER=`app`@`%` PROCEDURE `cerrar`(IN p INT)
BEGIN
  UPDATE movimiento SET status = 'CERRADO' WHERE id = p;
END ;;
DELIMITER ;
"""

MYSQL_SYSTEM = """-- MariaDB dump 10.19  Distrib 10.11.14-MariaDB, for debian-linux-gnu (x86_64)
--
-- Host: 10.0.0.9    Database: mysql
-- ------------------------------------------------------
-- Server version\t5.7.36
CREATE TABLE `user` (`Host` char(60) NOT NULL, `User` char(32) NOT NULL);
INSERT INTO `user` VALUES ('%','app');
CREATE TABLE `db` (`Host` char(60) NOT NULL, `Db` char(64) NOT NULL);
INSERT INTO `db` VALUES ('%','almacen_prod');
CREATE TABLE `tables_priv` (`Host` char(60) NOT NULL);
CREATE TABLE `columns_priv` (`Host` char(60) NOT NULL);
CREATE TABLE `proc` (`db` char(64) NOT NULL);
"""

PG_PLAIN = """--
-- PostgreSQL database dump
--
-- Dumped from database version 12.4
-- Dumped by pg_dump version 12.4
\\connect nominas
CREATE TABLE public.tramite (
    id integer NOT NULL,
    estado character varying(20),
    fecha_alta date
);
ALTER TABLE public.tramite OWNER TO dueno;
COPY public.tramite (id, estado, fecha_alta) FROM stdin;
1\tPENDIENTE\t2024-01-01
2\tCERRADO\t\\N
\\.
CREATE FUNCTION public.f_valida() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  RETURN NEW;
END;
$$;
CREATE TRIGGER t_valida BEFORE INSERT ON public.tramite FOR EACH ROW EXECUTE FUNCTION public.f_valida();
"""


class LectorTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name, text):
        p = self.root / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_mysqldump_de_aplicacion(self):
        info = sqldump.read_sql_dump(self._write("app.sql", MYSQL_APP))
        self.assertEqual((info.dialect, info.tool, info.server_version, info.dbname, info.source_host),
                         ("mysql", "mysqldump", "5.7.36", "almacen_prod", "10.0.0.9"))
        self.assertFalse(info.system_only)
        self.assertEqual(info.tables["estatus"].columns, ["id", "nombre"])
        self.assertEqual(info.tables["estatus"].count, 3)
        self.assertEqual(info.tables["estatus"].rows[2], ["3", "It's 'raro'"])
        self.assertEqual(info.tables["movimiento"].count, 3)
        self.assertIsNone(info.tables["movimiento"].rows[1][3], "NULL es None")
        self.assertEqual([v[0] for v in info.views], ["v_abiertos"])
        self.assertEqual([(t[0], t[1], t[2]) for t in info.triggers], [("trg_mov", "BEFORE INSERT", "movimiento")])
        self.assertEqual([(r[0], r[1]) for r in info.routines], [("procedure", "cerrar")])
        self.assertEqual(info.owners, ["app"])

    def test_el_esquema_de_sistema_no_es_la_base_de_la_aplicacion(self):
        info = sqldump.read_sql_dump(self._write("mysql.sql", MYSQL_SYSTEM))
        self.assertEqual((info.tool, info.tool_version), ("mariadb-dump", "10.11.14"), "la versión es numérica; el sabor va en tool")
        self.assertTrue(info.system_only)

    def test_pg_dump_plano(self):
        info = sqldump.read_sql_dump(self._write("nominas.sql", PG_PLAIN))
        self.assertEqual((info.dialect, info.tool, info.server_version, info.tool_version, info.dbname),
                         ("postgresql", "pg_dump", "12.4", "12.4", "nominas"))
        self.assertEqual(info.tables["tramite"].columns, ["id", "estado", "fecha_alta"])
        self.assertEqual(info.tables["tramite"].count, 2)
        self.assertIsNone(info.tables["tramite"].rows[1][2])
        self.assertEqual([r[1] for r in info.routines], ["f_valida"])
        self.assertEqual([t[0] for t in info.triggers], ["t_valida"])
        self.assertEqual(info.owners, ["dueno"])

    def test_on_row_recibe_todas_las_filas_y_keep_rows_acota(self):
        seen = []
        info = sqldump.read_sql_dump(self._write("app.sql", MYSQL_APP), keep_rows=1,
                                     on_row=lambda t, cols, cells: seen.append(t))
        self.assertEqual(seen.count("movimiento"), 3)
        self.assertTrue(info.tables["movimiento"].overflow)
        self.assertEqual(info.tables["movimiento"].rows, [])

    def test_is_sql_dump(self):
        self.assertTrue(sqldump.is_sql_dump(self._write("a.sql", MYSQL_APP)))
        self.assertFalse(sqldump.is_sql_dump(self._write("b.dump", "PGDMP\x00\x00")))


class MecanismoTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.war = self.root / "app.war"
        with zipfile.ZipFile(self.war, "w") as z:
            z.writestr("WEB-INF/classes/x.txt", "x")

    def tearDown(self):
        self._tmp.cleanup()

    def test_sql_dump_da_tablas_catalogos_distribuciones_y_reglas(self):
        dump = self.root / "app.sql"
        dump.write_text(MYSQL_APP, encoding="utf-8")
        m = build_map(self.war, [{"mechanism": "sql_dump", "catalog_max_rows": 2,
                                  "state_column_pattern": "(?i)status", "date_column_pattern": "(?i)^fecha"}], "p", dump=dump, tools={})
        tables = {d["name"]: d for d in m["data_stores"] if d["kind"] == "table"}
        self.assertEqual(tables["movimiento"]["count"], 3)
        self.assertEqual(tables["estatus"]["columns"], ["id", "nombre"])
        self.assertEqual([c["table"] for c in m["catalogs"]], [], "con catalog_max_rows=2 ninguna tabla de 3 filas es catálogo")
        dist = {(d["table"], d["column"]): d for d in m["distributions"]}
        self.assertEqual({v["value"]: v["count"] for v in dist[("movimiento", "status")]["values"]}, {"ABIERTO": 2, "CERRADO": 1})
        self.assertEqual({v["value"]: v["count"] for v in dist[("movimiento", "fecha_movimiento (año)")]["values"]}, {"2024": 1, "2025": 2})
        self.assertNotIn(("usuario", "password"), dist)
        kinds = {(d["kind"], d["name"]) for d in m["data_stores"]}
        self.assertIn(("view", "v_abiertos"), kinds)
        self.assertIn(("trigger", "trg_mov"), kinds)
        self.assertIn(("function", "cerrar"), kinds)
        self.assertFalse(any("sql_dump" in g for g in m["coverage_gaps"]), m["coverage_gaps"])

    def test_catalogo_redactado(self):
        dump = self.root / "app.sql"
        dump.write_text(MYSQL_APP, encoding="utf-8")
        m = build_map(self.war, [{"mechanism": "sql_dump", "catalog_exclude_patterns": ["(?i)usuario"]}], "p", dump=dump, tools={})
        catalogs = {c["table"]: c for c in m["catalogs"]}
        self.assertIn("estatus", catalogs)
        self.assertNotIn("usuario", catalogs, "las tablas de personas no se vuelcan")
        mov = catalogs["movimiento"]
        emails = [row[3] for row in mov["rows"]]
        self.assertNotIn("ana@correo.example", emails, "un correo se redacta aunque la columna no se llame email")

    def test_el_esquema_de_sistema_es_un_hueco_declarado(self):
        dump = self.root / "mysql.sql"
        dump.write_text(MYSQL_SYSTEM, encoding="utf-8")
        m = build_map(self.war, [{"mechanism": "sql_dump"}], "p", dump=dump, tools={})
        self.assertFalse(m["complete"])
        self.assertTrue(any("esquema de SISTEMA" in g for g in m["coverage_gaps"]), m["coverage_gaps"])
        self.assertEqual([d for d in m["data_stores"] if d["kind"] == "table"], [], "ni una tabla de sistema se presenta como negocio")


class RehydrateConRespaldoSqlTest(unittest.TestCase):
    """Lo que rehydrate saca del respaldo lo dicta el perfil (`database.dump.format`)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_el_esquema_de_sistema_es_blocked_no_se_restaura(self):
        dump = self.root / "sistema.dump"
        dump.write_text(MYSQL_SYSTEM, encoding="utf-8")
        facts = read_dump_facts(dump, {"dump": {"format": "sql_text"}})
        self.assertTrue(facts.system_only)
        self.assertEqual(facts.dbname, "mysql")

    def test_hechos_del_respaldo(self):
        dump = self.root / "app.sql"
        dump.write_text(MYSQL_APP, encoding="utf-8")
        facts = read_dump_facts(dump, {"dump": {"format": "sql_text"}})
        self.assertEqual((facts.dbname, facts.server_version, facts.tables, facts.owners), ("almacen_prod", "5.7.36", 3, ["app"]))

    def test_formato_equivocado_es_blocked(self):
        dump = self.root / "app.dump"
        dump.write_bytes(b"PGDMP\x00" * 8)
        with self.assertRaisesRegex(Blocked, "script SQL"):
            read_dump_facts(dump, {"dump": {"format": "sql_text"}})

    def test_el_cliente_de_la_sonda_lo_arma_el_perfil(self):
        probe = {"client": ["mysql", "-uroot", "-N", "-e", "{sql}"], "env": {"MYSQL_PWD": "{db_password}"}}
        argv, env = db_client_command(probe, {"db_user": "u", "db_name": "app", "db_password": "p$w"}, "select 1")
        self.assertEqual(argv, ["mysql", "-uroot", "-N", "-e", "select 1"])
        self.assertEqual(env, ["MYSQL_PWD=p$w"])


if __name__ == "__main__":
    unittest.main()
