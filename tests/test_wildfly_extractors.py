"""El perfil `java-wildfly-postgres` tiene extractors.json y `pepper map` con él termina honesto, no en exit 2.

Auditoría 2026-09-29: `detect` elegía este perfil para examples/legacy-demo/artifacts y
`pepper map` respondía exit 2 por falta de extractors.json. Los extractores son heredados
del perfil JSF sin corrida real; lo que no pueden leer lo declaran.
"""

import json
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pepper.inspect.systemmap import build_map  # noqa: E402
from pepper.profiles import load_profile  # noqa: E402
from pepper.validate import validate_instance  # noqa: E402

DEMO = ROOT / "examples" / "legacy-demo" / "artifacts"
PROFILE = load_profile("java-wildfly-postgres")
EXTRACTORS = json.loads((PROFILE.dir / "extractors.json").read_text(encoding="utf-8"))["extractors"]

# javap de mentira: una clase JAX-RS, sin ninguna anotación de Spring
FAKE_JAVAP = r'''#!/usr/bin/env python3
import sys
args = sys.argv[1:]
for fqn in args[args.index("-classpath") + 2:]:
    print(f"public class {fqn} {{")
    print("  public javax.ws.rs.core.Response register(gob.demo.ApplicationRequest);")
    print("        javax.ws.rs.POST()")
    print("        javax.ws.rs.Path(")
    print('          value="/applications"')
    print("}")
'''


class PerfilWildflyTest(unittest.TestCase):
    def test_sobre_el_demo_el_mapa_sale_incompleto_y_dice_por_que(self):
        # con un javap cualquiera: lo que se prueba es que un directorio de fuente no es el WAR que el perfil espera
        mapa = build_map(DEMO, EXTRACTORS, PROFILE.id, tools={"javap": sys.executable})
        self.assertFalse(mapa["complete"])
        huecos = " ".join(mapa["coverage_gaps"])
        self.assertIn("jvm_route_annotations: el artefacto no es un archivo zip/WAR", huecos)
        self.assertIn("jvm_class_inventory: el artefacto no es un archivo zip/WAR", huecos)
        self.assertIn("pg_dump_custom: no se encontró el respaldo", huecos)
        for cubierta in ("entrypoints:", "screens:", "classes:", "external_dependencies:", "data_stores:"):
            self.assertNotIn(cubierta, huecos, "toda superficie tiene un mecanismo que la cubre")
        notas = " ".join(mapa["notes"])
        # config_hosts lee `clave=valor`: el properties del demo y el standalone.xml dan sus hosts
        self.assertIn("notificaciones.smtp.host: mail.dependencia.gob.mx", notas)
        self.assertIn("datasource.url: jdbc:postgresql://srv-bd-01:5432/solicitudes", notas)
        self.assertNotIn("******", notas, "la contraseña del standalone.xml no viaja")
        try:
            errores = validate_instance(mapa, "system-map")
        except ImportError:
            self.skipTest("jsonschema no instalado")
        self.assertEqual(errores, [], errores)

    def test_pepper_map_con_el_perfil_que_detect_elige_ya_no_termina_en_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = subprocess.run([sys.executable, "-m", "pepper", "map", str(DEMO), "--profile", "java-wildfly-postgres",
                                  "--out", str(Path(tmp) / "system-map.json")], capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("mapa INCOMPLETO", out.stdout)
        self.assertNotIn("no declara extractors.json", out.stderr)

    def test_un_war_jax_rs_deja_la_nota_de_que_no_se_reconocio_ninguna_anotacion(self):
        with tempfile.TemporaryDirectory() as tmp:
            war = Path(tmp) / "solicitudes.war"
            with zipfile.ZipFile(war, "w") as z:
                z.writestr("WEB-INF/classes/gob/demo/rest/ApplicationsResource.class", b"\xca\xfe\xba\xbe")
                z.writestr("WEB-INF/jboss-web.xml", "<jboss-web/>")
            javap = Path(tmp) / "javap"
            javap.write_text(FAKE_JAVAP, encoding="utf-8")
            javap.chmod(javap.stat().st_mode | stat.S_IEXEC)
            mapa = build_map(war, [e for e in EXTRACTORS if e["mechanism"] == "jvm_route_annotations"], PROFILE.id,
                             tools={"javap": str(javap)})
        self.assertEqual(mapa["entrypoints"], [], "JAX-RS no se reconoce: no se inventa la ruta")
        self.assertTrue(any("ninguna trae una anotación que este lector reconozca" in n for n in mapa["notes"]), mapa["notes"])


if __name__ == "__main__":
    unittest.main()
