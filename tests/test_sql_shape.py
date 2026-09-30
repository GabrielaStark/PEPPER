"""`sql_shape` con los identificadores de cada dialecto (auditoría 2026-09-29).

Hoy `INSERT INTO \\`user\\`` devolvía tabla None, `FROM ONLY citas` devolvía `ONLY` y
`EXEC dbo.sp_x` no decía qué procedimiento. Cada caso queda fijado aquí.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pepper.correlate.sql import normalize_identifier, sql_shape  # noqa: E402


class SqlShapeTest(unittest.TestCase):
    def test_backticks_de_mysql(self):
        self.assertEqual(sql_shape("INSERT INTO `user` (`id`, `name`) VALUES (1, 'x')")[:2], ("INSERT", "user"))
        self.assertEqual(sql_shape("insert into `user`(`id`) values (1)")[:2], ("INSERT", "user"))
        self.assertEqual(sql_shape("UPDATE `app`.`order` SET status='CERRADO' WHERE id=3")[:2], ("UPDATE", "app.order"))
        self.assertEqual(sql_shape("DELETE FROM `order_item` WHERE id = 9")[:2], ("DELETE", "order_item"))
        self.assertEqual(sql_shape("SELECT * FROM `citas` c WHERE c.id = 1")[:2], ("SELECT", "citas"))
        self.assertEqual(sql_shape("INSERT IGNORE INTO `log` VALUES (1)")[:2], ("INSERT", "log"))

    def test_comillas_dobles_calificadas_por_esquema(self):
        self.assertEqual(sql_shape('UPDATE "app"."order" SET x = 1')[:2], ("UPDATE", "app.order"))
        self.assertEqual(sql_shape('INSERT INTO "public"."cita" (a) VALUES (1)')[:2], ("INSERT", "public.cita"))
        self.assertEqual(sql_shape('SELECT id FROM "Citas" WHERE id=$1')[:2], ("SELECT", "Citas"))
        self.assertEqual(sql_shape("select c.* from archivo.expediente c")[:2], ("SELECT", "archivo.expediente"))
        self.assertEqual(sql_shape('select 1 from "app" . "order"')[:2], ("SELECT", "app.order"), "espacios alrededor del punto")

    def test_corchetes_de_sql_server(self):
        self.assertEqual(sql_shape("INSERT INTO [dbo].[Users] ([Name]) VALUES ('x')")[:2], ("INSERT", "dbo.Users"))
        self.assertEqual(sql_shape("UPDATE [Users] SET [Name] = 'y' WHERE [Id] = 1")[:2], ("UPDATE", "Users"))
        self.assertEqual(sql_shape("SELECT TOP 10 * FROM [dbo].[Orders] WHERE [Status] = 'OPEN'")[:2], ("SELECT", "dbo.Orders"))
        self.assertEqual(sql_shape("DELETE FROM [dbo].[Sessions] WHERE Expired = 1")[:2], ("DELETE", "dbo.Sessions"))

    def test_only_de_postgresql_no_es_una_tabla(self):
        self.assertEqual(sql_shape("SELECT * FROM ONLY citas WHERE id = 1")[:2], ("SELECT", "citas"))
        self.assertEqual(sql_shape("UPDATE ONLY public.cita SET estado = 'X'")[:2], ("UPDATE", "public.cita"))
        self.assertEqual(sql_shape("DELETE FROM ONLY cita WHERE id = 1")[:2], ("DELETE", "cita"))

    def test_procedimientos(self):
        self.assertEqual(sql_shape("EXEC dbo.sp_x @a = 1")[:2], ("PROCEDURE", "dbo.sp_x"))
        self.assertEqual(sql_shape("EXECUTE [dbo].[sp_cerrar] 1")[:2], ("PROCEDURE", "dbo.sp_cerrar"))
        self.assertEqual(sql_shape("CALL cerrar(3)")[:2], ("PROCEDURE", "cerrar"))
        self.assertEqual(sql_shape("call `almacen`.`cerrar`(3)")[:2], ("PROCEDURE", "almacen.cerrar"))

    def test_lo_de_siempre_sigue_igual(self):
        operation, table, extra = sql_shape("INSERT INTO application (folio) VALUES ($1)")
        self.assertEqual((operation, table, extra), ("INSERT", "application", {}))
        self.assertEqual(sql_shape("SELECT nextval('folio_seq')"), ("SELECT", None, {"sequence": "folio_seq"}))
        self.assertEqual(sql_shape("BEGIN")[:2], ("TRANSACTION", None))
        self.assertEqual(sql_shape("COMMIT;")[:2], ("TRANSACTION", None))
        self.assertEqual(sql_shape("SET NAMES utf8")[:2], ("OTHER", None))
        self.assertEqual(sql_shape("WITH x AS (SELECT 1) SELECT * FROM x")[:2], ("SELECT", "x"))
        self.assertEqual(sql_shape("MERGE INTO cuentas USING nuevas ON (1=1)")[:2], ("UPDATE", "cuentas"))
        self.assertEqual(sql_shape("CREATE TABLE t (id int)")[:2], ("DDL", None))
        self.assertEqual(sql_shape("")[:2], ("OTHER", None))

    def test_normalize_identifier(self):
        self.assertEqual(normalize_identifier('"app"."order"'), "app.order")
        self.assertEqual(normalize_identifier("[dbo].[Users]"), "dbo.Users")
        self.assertEqual(normalize_identifier("`user`"), "user")
        self.assertEqual(normalize_identifier("public.cita"), "public.cita")


if __name__ == "__main__":
    unittest.main()
