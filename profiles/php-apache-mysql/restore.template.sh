#!/bin/sh
# Plantilla de restauración — perfil php-apache-mysql (BORRADOR).
# Rehydrate sustituye {{...}} y la deja en pepper-out/rehydrate/restore.sh.
#
# Restaura un respaldo SQL (mysqldump / mariadb-dump) DENTRO de la base que el .env del legacy espera
# ($DB_NAME), sin importar cómo se llamaba en origen, y crea al usuario que el .env declara ($DB_USER)
# con la clave del .env (es la misma que la de root del contenedor: DB_PASSWORD). Reglas, las mismas
# que enseñó el perfil groovy-grails1-tomcat-mysql en su corrida real (2026-09-22):
#   - Si el respaldo es el esquema de sistema `mysql` (general_log, user, db, plugin…), NO se
#     restaura: reescribiría usuarios, contraseñas, grants y plugins del contenedor. Código 3.
#   - La primera línea de mariadb-dump >= 10.11 ("/*M!999999\- enable the sandbox mode */")
#     rompe al cliente mysql 5.7: se quita.
#   - CREATE DATABASE / USE del respaldo se quitan: todo entra a $DB_NAME.
#   - DEFINER=`usuario`@`host` de vistas, triggers y rutinas se reemplaza por CURRENT_USER.
#   - Las vistas vienen con el esquema de origen calificado (`origen`.`tabla`): se reescribe a $DB_NAME.
set -u
echo "== esperando a MySQL en $DB_HOST =="
until mysqladmin ping -h "$DB_HOST" -uroot --silent 2>/dev/null; do sleep 2; done

if grep -qE '^CREATE TABLE (IF NOT EXISTS )?`(general_log|columns_priv|proxies_priv|innodb_index_stats)`' /dump/backup.sql \
   && grep -qE '^CREATE TABLE `user`' /dump/backup.sql; then
  echo "PEPPER_RESTORE status=3 errors=0"
  echo "== el respaldo es el esquema de sistema 'mysql' (cuentas y privilegios), no la base de la aplicación: NO se restaura =="
  exit 3
fi

SOURCE_DB=$(head -20 /dump/backup.sql | sed -n 's/^-- Host: .*Database: *\([A-Za-z0-9_$-]*\).*$/\1/p' | head -1)
if [ -n "$SOURCE_DB" ] && [ "$SOURCE_DB" != "$DB_NAME" ]; then
  echo "== las referencias calificadas a \`$SOURCE_DB\`.\`tabla\` (vistas) se reescriben a \`$DB_NAME\` =="
  QUALIFIED_SED="s/\`$SOURCE_DB\`\./\`$DB_NAME\`./g"
else
  QUALIFIED_SED=""
fi
echo "== restaurando dentro de $DB_NAME =="
sed -e '1{/^\/\*M!999999/d;}' \
    -e '/^CREATE DATABASE /d' -e '/^USE `/d' \
    -e 's/DEFINER=`[^`]*`@`[^`]*`/DEFINER=CURRENT_USER/g' \
    -e "$QUALIFIED_SED" \
    /dump/backup.sql > /tmp/backup.filtered.sql
{ mysql -h "$DB_HOST" -uroot --default-character-set=utf8mb4 "$DB_NAME" < /tmp/backup.filtered.sql 2>&1; echo $? > /tmp/restore.status; } | tee /tmp/restore.log | head -40
status=$(cat /tmp/restore.status)
errors=$(grep -c "^ERROR" /tmp/restore.log || true)
if [ "$status" != "0" ]; then
  echo "PEPPER_RESTORE status=$status errors=$errors"
  echo "== mysql falló (código $status): la base queda a medias, no se marca como restaurada =="
  exit "$status"
fi

# El usuario con el que entra el app (DB_USERNAME del .env), con la clave del .env. Si el legacy
# entraba como root, ya existe. El plugin de autenticación es el default del motor: si producción
# corría MySQL 8 con mysql_native_password para un PHP viejo, el app lo dirá al arrancar
# (SQLSTATE[HY000] [2054]) y rehydrate lo reporta como FAILED en vez de adivinarlo aquí.
if [ "$DB_USER" != "root" ]; then
  echo "== creando al usuario $DB_USER del legacy con acceso a $DB_NAME =="
  mysql -h "$DB_HOST" -uroot -e "CREATE USER IF NOT EXISTS '$DB_USER'@'%' IDENTIFIED BY '$MYSQL_PWD'; GRANT ALL PRIVILEGES ON \`$DB_NAME\`.* TO '$DB_USER'@'%'; FLUSH PRIVILEGES;"
fi

echo "== resultado =="
mysql -h "$DB_HOST" -uroot -N -e "select 'tablas', count(*) from information_schema.tables where table_schema='$DB_NAME' and table_type='BASE TABLE' union all select 'vistas', count(*) from information_schema.views where table_schema='$DB_NAME' union all select 'rutinas', count(*) from information_schema.routines where routine_schema='$DB_NAME' union all select 'triggers', count(*) from information_schema.triggers where trigger_schema='$DB_NAME';"
# Marca fuera de la base de la aplicación (MySQL no tiene comentario de base): amarra el volumen a ESTE respaldo.
mysql -h "$DB_HOST" -uroot -e "create database if not exists pepper_meta; create table if not exists pepper_meta.restored (dump_sha varchar(64) primary key, restored_at timestamp default current_timestamp); insert ignore into pepper_meta.restored (dump_sha) values ('{{dump_sha}}');"
echo "PEPPER_RESTORE status=$status errors=$errors"
