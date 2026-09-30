"""Un desplegable que es una CARPETA (PHP, Node con fuente, un dist estático) entra al plan como
datos del perfil: `rehydrate.artifact_kind = directory` (auditoría 2026-09-29, bloque 6).

Se prueba con el perfil `php-apache-mysql` y sus fixtures sintéticos: la carpeta se encuentra bajo
legacy/, el servidor sale de NOTAS.md con la versión COMPLETA (php:7.4-apache), el datasource se lee
del .env con el lector genérico, y al renderizar la carpeta viaja como UN archivo (`legacy.tar`) junto
al compose —nunca como directorio del host— y el compose resultante pasa la verificación estática de
aislamiento con las mismas reglas que los perfiles de la JVM.
"""

import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pepper.isolate import check_static  # noqa: E402
from pepper.profiles import load_profile  # noqa: E402
from pepper.rehydrate import Blocked, find_all_inputs, make_plan, pack_directory, render  # noqa: E402

PROFILE = load_profile("php-apache-mysql")
FIXTURE_SOURCE = ROOT / "profiles" / "php-apache-mysql" / "fixtures" / "source"
SYNTHESIZE = ROOT / "profiles" / "php-apache-mysql" / "fixtures" / "synthesize.py"

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


def _legacy(root: Path, notes: str = "# Notas\n\nProducción corre PHP 7.4 sobre Apache 2.4.\n") -> Path:
    legacy = root / "legacy"
    shutil.copytree(FIXTURE_SOURCE, legacy / "ventanilla", symlinks=True)
    subprocess.run([sys.executable, str(SYNTHESIZE), str(legacy / "respaldo.sql")], check=True, capture_output=True)
    if notes is not None:
        (legacy / "NOTAS.md").write_text(notes, encoding="utf-8")
    return legacy


class CarpetaComoDesplegableTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_la_carpeta_bajo_legacy_es_el_desplegable_y_los_archivos_sueltos_no(self):
        legacy = _legacy(self.root)
        (legacy / "algo.war").write_bytes(b"PK\x05\x06" + b"\x00" * 18)   # un zip suelto no cuenta para este perfil
        (legacy / ".oculta").mkdir()
        artifacts, dumps = find_all_inputs(legacy, dump_suffixes=(".sql",), artifact_kind="directory")
        self.assertEqual([a.name for a in artifacts], ["ventanilla"])
        self.assertEqual([d.name for d in dumps], ["respaldo.sql"])

    def test_sin_carpeta_es_blocked_diciendo_que_espera_una_carpeta(self):
        legacy = self.root / "legacy"
        legacy.mkdir()
        (legacy / "respaldo.sql").write_text("-- MySQL dump\n", encoding="utf-8")
        with self.assertRaises(Blocked) as caught:
            find_all_inputs(legacy, dump_suffixes=(".sql",), artifact_kind="directory")
        self.assertIn("CARPETA", str(caught.exception))
        self.assertIn("artifact_kind = directory", str(caught.exception))

    def test_un_kind_desconocido_es_blocked(self):
        legacy = _legacy(self.root)
        with self.assertRaises(Blocked):
            find_all_inputs(legacy, artifact_kind="tarball")

    def test_el_plan_sale_del_env_y_de_notas(self):
        legacy = _legacy(self.root)
        plan = make_plan(legacy, PROFILE, host_port=18077)
        self.assertTrue(plan.artifact.is_dir())
        self.assertEqual(plan.server, "php")
        self.assertEqual(plan.server_image, "php:7.4-apache", "la versión completa de NOTAS.md manda, no solo la mayor")
        self.assertEqual((plan.db_engine, plan.db_name, plan.db_user, plan.db_port), ("mysql", "tramites", "app_tramites", 3306))
        self.assertTrue(plan.shared_namespace, "DB_HOST=127.0.0.1: el app comparte la pila de red de la base")
        self.assertTrue(plan.db_image.startswith("mysql:5"), plan.db_image)
        self.assertEqual(plan.db_version, "5.7.44")
        self.assertIn("smtp.ejemplo.gob", plan.external_hosts)
        self.assertIn("api.pagos.ejemplo.com", plan.external_hosts)
        # APP_URL (el nombre público del sistema) también va al stub: si el app se llama a sí mismo por
        # ese nombre, el stub lo registra en vez de que la petición se pierda; el núcleo no sabe cuál es "yo".
        self.assertIn("ventanilla.ejemplo.gob", plan.external_hosts)
        self.assertIn("587", plan.stub_ports.split(","), "MAIL_PORT del .env abre el puerto en el stub")
        self.assertEqual(plan.stack_name.split("-")[0], "ventanilla")

    def test_notas_con_otra_version_da_otra_imagen(self):
        legacy = _legacy(self.root, notes="PHP 8.1 en producción\n")
        plan = make_plan(legacy, PROFILE, host_port=18077)
        self.assertEqual(plan.server_image, "php:8.1-apache")

    def test_sin_version_de_php_en_notas_es_blocked(self):
        legacy = _legacy(self.root, notes="# Notas\n\nApache 2.4 y MySQL 5.7.\n")
        with self.assertRaises(Blocked) as caught:
            make_plan(legacy, PROFILE, host_port=18077)
        self.assertIn("versión de php", str(caught.exception).lower())

    def test_render_empaca_la_carpeta_como_un_archivo_y_el_compose_pasa_el_aislamiento(self):
        legacy = _legacy(self.root)
        plan = make_plan(legacy, PROFILE, host_port=18077)
        out = self.root / "pepper-out" / "rehydrate"
        written = render(plan, PROFILE, out)
        tar_path = out / "legacy.tar"
        self.assertIn(tar_path, written)
        self.assertEqual(oct(tar_path.stat().st_mode & 0o777), "0o600", "el tar lleva el .env con credenciales")
        with tarfile.open(tar_path) as tar:
            names = set(tar.getnames())
        self.assertIn(".env", names)
        self.assertIn("public/index.php", names)
        self.assertIn("routes/web.php", names)
        compose_text = (out / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("./legacy.tar:/legacy.tar:ro", compose_text, "la carpeta viaja como UN archivo :ro, no como directorio del host")
        self.assertNotIn(str(legacy / "ventanilla"), compose_text,
                         "la carpeta del fuente no entra al compose: el contenedor no ve un directorio del host")
        self.assertIn("respaldo.sql:/dump/backup.sql:ro", compose_text, "el respaldo sí es un archivo concreto :ro")
        self.assertIn("php:7.4-apache", compose_text)
        self.assertIn("smtp.ejemplo.gob", compose_text)
        self.assertTrue(any("legacy.tar" in n for n in plan.notes), plan.notes)
        restore_text = (out / "restore.sh").read_text(encoding="utf-8")
        self.assertIn("DB_USER", restore_text)
        if yaml is None:
            self.skipTest("pyyaml no instalado")
        # lo que `docker compose config` sustituye antes de que isolate mire el compose resuelto
        resuelto = (compose_text.replace("${DB_PASSWORD}", "clave").replace("${PEPPER_PLATFORM}", "linux/amd64")
                    .replace("$$", "$"))
        compose = yaml.safe_load(resuelto)
        report = check_static(compose, plan.external_hosts, "ingress", resolved=True, compose_dir=out)
        self.assertEqual(report.verdict, "VERIFIED", [f.check for f in report.errors + report.unknowns])

    def test_pack_directory_no_sigue_enlaces_ni_lleva_git(self):
        source = self.root / "fuente"
        (source / ".git").mkdir(parents=True)
        (source / ".git" / "config").write_text("[core]\n", encoding="utf-8")
        (source / "index.php").write_text("<?php echo 1;", encoding="utf-8")
        fuera = self.root / "fuera.txt"
        fuera.write_text("no debe viajar", encoding="utf-8")
        os.symlink(fuera, source / "enlace.txt")
        tar_path = pack_directory(source, self.root / "legacy.tar")
        with tarfile.open(tar_path) as tar:
            members = {m.name: m for m in tar.getmembers()}
        self.assertIn("index.php", members)
        self.assertNotIn(".git/config", members)
        self.assertTrue(members["enlace.txt"].issym(), "un enlace viaja como enlace, no como el archivo al que apunta")


if __name__ == "__main__":
    unittest.main()
