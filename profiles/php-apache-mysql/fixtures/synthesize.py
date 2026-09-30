#!/usr/bin/env python3
"""Respaldo sintético del perfil php-apache-mysql, con la forma de un `mysqldump` 5.7 de una base Laravel.

No describe ningún sistema real: existe para que la suite (`tests/test_perfiles.py`) pruebe el lector
`sql_dump` y `rehydrate` (cabecera con `Server version`, tablas con conteo, catálogo, vista, rutina,
trigger, DEFINER y esquema calificado) sin un legacy. Se genera al vuelo: no se versiona el .sql.

    python3 profiles/php-apache-mysql/fixtures/synthesize.py <directorio o destino.sql>
"""
from __future__ import annotations

import sys
from pathlib import Path

DB = "tramites_prod"          # el nombre de ORIGEN: distinto de DB_DATABASE del .env a propósito (restore lo reescribe)

ESTADOS = [(1, "Recibido"), (2, "Turnado"), (3, "En revisión"), (4, "Resuelto"), (5, "Vencido")]
DEPENDENCIAS = [(1, "Dirección de Padrón"), (2, "Tesorería"), (3, "Obras Públicas")]


def tramites(n: int = 350):   # más que catalog_max_rows (300): una tabla de negocio no se vuelca como catálogo
    for i in range(1, n + 1):
        yield (i, f"TR-{i:04d}", f"Solicitante {i}", 1 + (i % 5), 1 + (i % 3), f"2026-0{1 + (i % 9)}-{1 + (i % 27):02d} 10:00:00")


def dump() -> str:
    out = []
    w = out.append
    w("-- MySQL dump 10.13  Distrib 5.7.44, for Linux (x86_64)")
    w("--")
    w(f"-- Host: localhost    Database: {DB}")
    w("-- ------------------------------------------------------")
    w("-- Server version\t5.7.44")
    w("")
    w("/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;")
    w(f"CREATE DATABASE /*!32312 IF NOT EXISTS*/ `{DB}` /*!40100 DEFAULT CHARACTER SET utf8mb4 */;")
    w(f"USE `{DB}`;")
    w("DROP TABLE IF EXISTS `estados`;")
    w("CREATE TABLE `estados` (\n  `id` int(10) unsigned NOT NULL AUTO_INCREMENT,\n  `nombre` varchar(60) NOT NULL,\n  PRIMARY KEY (`id`)\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;")
    w("INSERT INTO `estados` VALUES " + ",".join(f"({i},'{n}')" for i, n in ESTADOS) + ";")
    w("DROP TABLE IF EXISTS `dependencias`;")
    w("CREATE TABLE `dependencias` (\n  `id` int(10) unsigned NOT NULL AUTO_INCREMENT,\n  `nombre` varchar(120) NOT NULL,\n  PRIMARY KEY (`id`)\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;")
    w("INSERT INTO `dependencias` VALUES " + ",".join(f"({i},'{n}')" for i, n in DEPENDENCIAS) + ";")
    w("DROP TABLE IF EXISTS `users`;")
    w("CREATE TABLE `users` (\n  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,\n  `name` varchar(255) NOT NULL,\n  `email` varchar(255) NOT NULL,\n"
      "  `password` varchar(255) NOT NULL,\n  `rol` enum('operador','supervisor','admin') NOT NULL DEFAULT 'operador',\n  `dependencia_id` int(10) unsigned DEFAULT NULL,\n"
      "  `remember_token` varchar(100) DEFAULT NULL,\n  `created_at` timestamp NULL DEFAULT NULL,\n  `updated_at` timestamp NULL DEFAULT NULL,\n  PRIMARY KEY (`id`),\n  UNIQUE KEY `users_email_unique` (`email`)\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;")
    w("INSERT INTO `users` VALUES (1,'Operador Uno','operador@ejemplo.gob','$2y$10$fixturefixturefixturefixturefixturefixturefixturefix','operador',1,NULL,'2026-01-05 09:00:00',NULL),"
      "(2,'Supervisora Dos','supervisora@ejemplo.gob','$2y$10$fixturefixturefixturefixturefixturefixturefixturefix','supervisor',1,NULL,'2026-01-05 09:00:00',NULL),"
      "(3,'Admin Tres','admin@ejemplo.gob','$2y$10$fixturefixturefixturefixturefixturefixturefixturefix','admin',NULL,NULL,'2026-01-05 09:00:00',NULL);")
    w("DROP TABLE IF EXISTS `tramites`;")
    w("CREATE TABLE `tramites` (\n  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,\n  `folio` varchar(20) NOT NULL,\n  `solicitante` varchar(200) NOT NULL,\n"
      "  `estado_id` int(10) unsigned NOT NULL,\n  `dependencia_id` int(10) unsigned NOT NULL,\n  `created_at` timestamp NULL DEFAULT NULL,\n  PRIMARY KEY (`id`),\n"
      "  UNIQUE KEY `tramites_folio_unique` (`folio`),\n  KEY `tramites_estado_id_foreign` (`estado_id`),\n"
      "  CONSTRAINT `tramites_estado_id_foreign` FOREIGN KEY (`estado_id`) REFERENCES `estados` (`id`),\n"
      "  CONSTRAINT `tramites_dependencia_id_foreign` FOREIGN KEY (`dependencia_id`) REFERENCES `dependencias` (`id`)\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;")
    w("INSERT INTO `tramites` VALUES " + ",".join(f"({i},'{f}','{s}',{e},{d},'{c}')" for i, f, s, e, d, c in tramites()) + ";")
    w("DROP TABLE IF EXISTS `pagos`;")
    w("CREATE TABLE `pagos` (\n  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,\n  `tramite_id` bigint(20) unsigned NOT NULL,\n  `monto` decimal(10,2) NOT NULL,\n"
      "  `referencia_externa` varchar(64) DEFAULT NULL,\n  `pagado_at` timestamp NULL DEFAULT NULL,\n  PRIMARY KEY (`id`)\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;")
    w("INSERT INTO `pagos` VALUES (1,1,350.00,'PG-0001','2026-02-01 12:00:00'),(2,2,350.00,'PG-0002','2026-02-02 12:00:00'),(3,4,120.50,NULL,NULL);")
    w("DROP TABLE IF EXISTS `migrations`;")
    w("CREATE TABLE `migrations` (\n  `id` int(10) unsigned NOT NULL AUTO_INCREMENT,\n  `migration` varchar(255) NOT NULL,\n  `batch` int(11) NOT NULL,\n  PRIMARY KEY (`id`)\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;")
    w("INSERT INTO `migrations` VALUES (1,'2019_12_14_000001_create_users_table',1),(2,'2020_03_01_000000_create_tramites_table',1);")
    # vista con el esquema de origen calificado (lo emite mysqldump; restore.sh lo reescribe)
    w("DROP TABLE IF EXISTS `v_tramites_pendientes`;")
    w(f"/*!50001 CREATE ALGORITHM=UNDEFINED */\n/*!50013 DEFINER=`app_tramites`@`localhost` SQL SECURITY DEFINER */\n"
      f"/*!50001 VIEW `v_tramites_pendientes` AS select `t`.`folio` AS `folio`,`e`.`nombre` AS `estado` from (`{DB}`.`tramites` `t` join `{DB}`.`estados` `e` on((`e`.`id` = `t`.`estado_id`))) where (`t`.`estado_id` in (1,2,3)) */;")
    w("DELIMITER ;;")
    w("/*!50003 CREATE*/ /*!50017 DEFINER=`app_tramites`@`localhost`*/ /*!50003 TRIGGER `tramites_folio_mayusculas` BEFORE INSERT ON `tramites` FOR EACH ROW SET NEW.folio = UPPER(NEW.folio) */;;")
    w("DELIMITER ;")
    w("DELIMITER ;;")
    w("CREATE DEFINER=`app_tramites`@`localhost` PROCEDURE `sp_vencer_tramites`(IN dias INT)\nBEGIN\n  UPDATE tramites SET estado_id = 5 WHERE estado_id IN (1,2) AND created_at < NOW() - INTERVAL dias DAY;\nEND ;;")
    w("DELIMITER ;")
    w("-- Dump completed on 2026-09-29 23:59:59")
    return "\n".join(out) + "\n"


def main(argv):
    if len(argv) != 2:
        raise SystemExit(__doc__)
    destino = Path(argv[1])
    if destino.suffix != ".sql":   # la suite pasa un directorio; una persona puede pasar el archivo
        destino.mkdir(parents=True, exist_ok=True)
        destino = destino / "respaldo.sql"
    destino.write_text(dump(), encoding="utf-8")
    print(f"respaldo sintético escrito en {destino}")


if __name__ == "__main__":
    main(sys.argv)
