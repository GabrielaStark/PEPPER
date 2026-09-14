#!/bin/sh
# Plantilla de restauración — perfil java-springboot-jsf-postgres.
# Rehydrate sustituye {{...}} y la deja en pepper-out/rehydrate/restore.sh.
#
# Restaura el respaldo DENTRO de la base que el artefacto espera (el nombre del
# respaldo no importa), sin dueños ni privilegios (el app entra con el usuario
# del datasource), y re-apunta al stub todo servidor foráneo (dblink /
# postgres_fdw): un respaldo puede traer credenciales de producción en sus
# USER MAPPING, y la máquina del ingeniero puede tener VPN — sin este paso, una
# vista con dblink alcanza producción con una sola consulta (pasó de verdad).
set -u
echo "== esperando a PostgreSQL en $PGHOST =="
until pg_isready -q; do sleep 2; done
echo "== roles que el respaldo referencia (sin login; el app usa el del datasource) =="
{{create_roles}}
echo "== restaurando (sin dueños ni privilegios) =="
# El estado de pg_restore se conserva aparte: en un pipeline `sh` devuelve el de `head`, y un
# restore a medias pasaba como éxito (auditoría 2026-09-11).
{ pg_restore --no-owner --no-privileges --jobs=2 -d "$PGDATABASE" /dump/backup.dump 2>&1; echo $? > /tmp/restore.status; } | tee /tmp/restore.log | grep -Ei "error|warning" | sort | uniq -c | sort -rn | head -40
status=$(cat /tmp/restore.status)
errors=$(grep -c "pg_restore: error" /tmp/restore.log || true)
if [ "$status" != "0" ] && [ "$status" != "1" ]; then
  echo "PEPPER_RESTORE status=$status errors=$errors"
  echo "== pg_restore falló (código $status): la base queda a medias, no se marca como restaurada =="
  exit "$status"
fi
echo "== servidores foráneos (dblink/postgres_fdw) → stub: el entorno nunca contacta producción =="
psql -d "$PGDATABASE" -Atc "select srvname from pg_foreign_server" | while read srv; do
  [ -n "$srv" ] && psql -d "$PGDATABASE" -c "ALTER SERVER \"$srv\" OPTIONS (SET host '{{stub_ip}}');" >/dev/null && echo "  $srv → {{stub_ip}}"
done
echo "== resultado =="
psql -d "$PGDATABASE" -Atc "select 'tablas', count(*) from pg_tables where schemaname not in ('pg_catalog','information_schema') union all select 'vistas', count(*) from pg_views where schemaname not in ('pg_catalog','information_schema') union all select 'extensiones', count(*) from pg_extension;"
# La marca amarra el volumen a ESTE respaldo: otro respaldo u otro legacy con el mismo prefijo no lo reutiliza en silencio.
psql -d "$PGDATABASE" -c "COMMENT ON DATABASE \"$PGDATABASE\" IS 'pepper:restored:{{dump_sha}}';" >/dev/null
echo "PEPPER_RESTORE status=$status errors=$errors"
