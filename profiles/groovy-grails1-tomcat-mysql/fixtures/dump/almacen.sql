-- MySQL dump 10.13  Distrib 5.7.36, for Linux (x86_64)
--
-- Host: 10.100.0.2    Database: almacen_prod
-- ------------------------------------------------------
-- Server version	5.7.36

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET NAMES utf8 */;

--
-- Respaldo SINTÉTICO para el fixture del perfil (2026-09-30): tres tablas con la forma de un
-- mysqldump real, sin datos de ninguna persona. `user` existe para comprobar que una tabla de
-- cuentas se cuenta pero no se vuelca como catálogo.
--

DROP TABLE IF EXISTS `estatus`;
CREATE TABLE `estatus` (
  `id` int(11) NOT NULL,
  `nombre` varchar(50) DEFAULT NULL,
  `activo` bit(1) DEFAULT b'1',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;
INSERT INTO `estatus` VALUES (1,'PENDIENTE',b'1'),(2,'SURTIDO',b'1'),(3,'CANCELADO',b'0');

DROP TABLE IF EXISTS `movimiento`;
CREATE TABLE `movimiento` (
  `id` char(38) NOT NULL,
  `version` bigint(20) NOT NULL,
  `status` varchar(255) DEFAULT NULL,
  `date_created` datetime DEFAULT NULL,
  `quantity` int(11) DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;
INSERT INTO `movimiento` VALUES ('ff80',0,'PENDIENTE','2024-01-05 10:00:00',10),('ff81',1,'SURTIDO','2025-03-01 09:00:00',4),('ff82',0,'PENDIENTE','2025-06-01 09:00:00',1);

DROP TABLE IF EXISTS `user`;
CREATE TABLE `user` (
  `id` char(38) NOT NULL,
  `username` varchar(255) NOT NULL,
  `password` varchar(255) DEFAULT NULL,
  `active` bit(1) DEFAULT b'1',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;
INSERT INTO `user` VALUES ('ff01','admin','c2ludGV0aWNv',b'1'),('ff02','manager','c2ludGV0aWNv',b'1');

/*!50001 CREATE ALGORITHM=UNDEFINED */
/*!50013 DEFINER=`almacen`@`%` SQL SECURITY DEFINER */
/*!50001 VIEW `v_pendientes` AS select `m`.`id` AS `id` from `movimiento` `m` where (`m`.`status` = 'PENDIENTE') */;

DELIMITER ;;
/*!50003 CREATE*/ /*!50017 DEFINER=`almacen`@`%`*/ /*!50003 TRIGGER `trg_movimiento_status` BEFORE INSERT ON `movimiento` FOR EACH ROW BEGIN
  IF NEW.status IS NULL THEN SET NEW.status = 'PENDIENTE'; END IF;
END */;;
DELIMITER ;
