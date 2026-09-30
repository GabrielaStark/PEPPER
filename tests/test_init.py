"""`pepper init`: un workspace aparte del clon, y el núcleo corriendo desde él.

Hermético: cada prueba crea su workspace en un directorio temporal a partir de ESTA instalación
(el clon donde corre la suite). Lo que corre por subprocess lo hace con `cwd` = workspace, como
lo haría una persona: `python3 -m pepper …` resuelve el paquete por el enlace `pepper/`."""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pepper  # noqa: E402
from pepper.init import GITIGNORE, InitError, create_workspace  # noqa: E402
from pepper.workspace import HOME_FILE, find_root, is_tool_path, read_home, tool_paths, version_line  # noqa: E402

TOOL_COPIES = (".claude/commands/pepper.md", ".claude/commands/pepper-observe.md", ".claude/agents/descubridor-funcional.md",
               ".claude/skills/evidencia-runtime/SKILL.md", ".claude/settings.json", "scripts/guardia_datos.py",
               "CLAUDE.md", "AGENTS.md", "templates/NOTAS-LEGACY.md")


def _run(ws, *args):
    """`python3 -m pepper …` desde el workspace, sin PYTHONPATH: el paquete lo encuentra por el enlace."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    return subprocess.run([sys.executable, "-m", "pepper", *args], cwd=str(ws), env=env,
                          capture_output=True, text=True, timeout=300)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.ws = self.tmp / "mi-legacy"

    def tearDown(self):
        self._tmp.cleanup()


class LayoutTest(Base):
    def test_el_workspace_tiene_el_layout_completo(self):
        create_workspace(self.ws)
        link = self.ws / "pepper"
        self.assertTrue(link.is_symlink(), "pepper/ es un enlace, no una copia del núcleo")
        self.assertEqual(link.resolve(), (ROOT / "pepper").resolve())
        self.assertTrue((link / "cli.py").is_file(), "el enlace lleva al paquete")
        for rel in TOOL_COPIES:
            copied = self.ws / rel
            self.assertTrue(copied.is_file(), f"falta la copia {rel}")
            self.assertFalse(copied.is_symlink(), f"{rel} es copia, no enlace: la persona y Claude Code lo leen de la raíz")
            self.assertEqual(copied.read_bytes(), (ROOT / rel).read_bytes(), f"{rel} difiere de la instalación")
        self.assertFalse((self.ws / ".claude" / "worktrees").exists(), "de .claude/ solo viajan commands, agents, skills y settings.json")
        for rel in ("examples", "tests"):
            self.assertFalse((self.ws / rel).exists(), f"{rel} es de la instalación: no se copia al workspace")
        for rel in ("schemas", "profiles", "docs/documentacion"):
            self.assertTrue((self.ws / rel).is_symlink(), f"{rel} es un enlace a la instalación")
            self.assertEqual((self.ws / rel).resolve(), (ROOT / rel).resolve())
        self.assertTrue((self.ws / "docs" / "documentacion" / "PRINCIPIOS.md").is_file(), "los comandos citan PRINCIPIOS.md desde el workspace")
        self.assertEqual((self.ws / "legacy" / "NOTAS.md").read_text(encoding="utf-8"),
                         (ROOT / "templates" / "NOTAS-LEGACY.md").read_text(encoding="utf-8"))
        self.assertTrue((self.ws / "docs" / "pepper" / ".gitkeep").is_file())
        for rel in ("pepper-out", "evidence"):
            self.assertTrue((self.ws / rel).is_dir() and not any((self.ws / rel).iterdir()), f"{rel}/ vacío")
        self.assertFalse((self.ws / ".git").exists(), "init no crea un repositorio: no hay a dónde subir nada")
        home = read_home(self.ws)
        self.assertEqual(home["home"], str(ROOT))
        self.assertEqual(home["version"], pepper.__version__)
        self.assertRegex(home.get("commit", "0" * 40), r"^[0-9a-f]{40}$")

    def test_el_gitignore_es_del_workspace(self):
        create_workspace(self.ws)
        text = (self.ws / ".gitignore").read_text(encoding="utf-8")
        self.assertEqual(text, GITIGNORE)
        rules = {line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")}
        for rule in ("/legacy/", "/evidence/", "/pepper-out/", "/pepper", f"/{HOME_FILE}", "/docs/pepper/explore.json", "__pycache__/"):
            self.assertIn(rule, rules)
        self.assertNotIn("/docs/pepper/", rules, "docs/pepper/ es el producto: SÍ se versiona")
        self.assertIn("docs/pepper/ es el PRODUCTO", text)
        self.assertNotIn("/legacies/", rules, "no es el .gitignore de la herramienta")

    def test_un_directorio_con_contenido_falla_con_mensaje(self):
        self.ws.mkdir()
        (self.ws / "algo.txt").write_text("x", encoding="utf-8")
        with self.assertRaisesRegex(InitError, "ya tiene contenido.*--force"):
            create_workspace(self.ws)
        self.assertEqual(sorted(p.name for p in self.ws.iterdir()), ["algo.txt"], "no dejó nada a medias")
        from pepper.cli import main
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = main(["init", str(self.ws)])
        self.assertEqual(code, 2)
        self.assertIn("pepper init:", stderr.getvalue())
        self.assertIn("ya tiene contenido", stderr.getvalue())

    def test_dentro_del_clon_se_niega_sin_crear_nada(self):
        target = ROOT / "pepper-out" / "workspace-de-prueba-init"
        with self.assertRaisesRegex(InitError, "dentro del clon"):
            create_workspace(target)
        self.assertFalse(target.exists())

    def test_windows_no_esta_soportado(self):
        with mock.patch.object(os, "name", "nt"):
            with self.assertRaisesRegex(InitError, "Windows no está soportado"):
                create_workspace(self.ws)
        self.assertFalse(self.ws.exists())

    def test_force_recopia_la_herramienta_y_no_toca_el_trabajo(self):
        create_workspace(self.ws)
        notes = self.ws / "legacy" / "NOTAS.md"
        notes.write_text("# Mi sistema\nproducción es WildFly 21\n", encoding="utf-8")
        (self.ws / "legacy" / "sistema.war").write_bytes(b"PK\x05\x06" + b"\x00" * 18)
        (self.ws / "docs" / "pepper" / "system-map.json").write_text("{}\n", encoding="utf-8")
        (self.ws / "pepper-out" / "explore.json").write_text("{}\n", encoding="utf-8")
        (self.ws / "evidence" / "explore-001").mkdir()
        (self.ws / ".gitignore").write_text(GITIGNORE + "/mio/\n", encoding="utf-8")
        # la copia de la herramienta se desactualiza o se rompe
        (self.ws / ".claude" / "commands" / "pepper.md").write_text("viejo\n", encoding="utf-8")
        (self.ws / ".claude" / "commands" / "sobrante.md").write_text("x\n", encoding="utf-8")
        import shutil
        shutil.rmtree(self.ws / ".claude" / "agents")
        (self.ws / "CLAUDE.md").unlink()
        (self.ws / HOME_FILE).write_text("home: /otra/parte\nversion: 0.0.1\n", encoding="utf-8")

        with self.assertRaisesRegex(InitError, "ya tiene contenido"):
            create_workspace(self.ws)
        create_workspace(self.ws, force=True)

        for rel in TOOL_COPIES:
            self.assertEqual((self.ws / rel).read_bytes(), (ROOT / rel).read_bytes(), f"{rel} no se recopió")
        self.assertFalse((self.ws / ".claude" / "commands" / "sobrante.md").exists(), "lo que ya no está en la instalación se va")
        self.assertEqual(read_home(self.ws)["home"], str(ROOT))
        self.assertEqual(notes.read_text(encoding="utf-8"), "# Mi sistema\nproducción es WildFly 21\n", "NOTAS.md editado no se toca")
        self.assertTrue((self.ws / "legacy" / "sistema.war").is_file())
        self.assertTrue((self.ws / "docs" / "pepper" / "system-map.json").is_file())
        self.assertTrue((self.ws / "pepper-out" / "explore.json").is_file())
        self.assertTrue((self.ws / "evidence" / "explore-001").is_dir())
        self.assertEqual((self.ws / ".gitignore").read_text(encoding="utf-8"), GITIGNORE + "/mio/\n", "un .gitignore editado no se pisa")

    def test_version_avisa_si_la_copia_es_de_otra_version(self):
        create_workspace(self.ws)
        self.assertNotIn("--force", version_line(self.ws))
        (self.ws / HOME_FILE).write_text(f"home: {ROOT}\nversion: 0.0.9\n", encoding="utf-8")
        line = version_line(self.ws)
        self.assertIn(f"pepper {pepper.__version__}", line)
        self.assertIn("0.0.9", line)
        self.assertIn("init . --force", line)


class DesdeElWorkspaceTest(Base):
    """El núcleo corre con cwd = workspace y la herramienta en otro lado."""

    def setUp(self):
        super().setUp()
        create_workspace(self.ws)

    def test_version_y_repo_root_resuelven_a_la_instalacion(self):
        result = _run(self.ws, "--version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"pepper {pepper.__version__}", result.stdout)
        self.assertIn(str(ROOT), result.stdout, "en un workspace, --version dice cuál es la instalación")
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        probe = subprocess.run([sys.executable, "-c", "import pepper, pepper.cli; print(pepper.__file__); print(pepper.REPO_ROOT); "
                                "print(pepper.PROFILES_DIR); print(pepper.SKILLS_DIR)"],
                               cwd=str(self.ws), env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(probe.returncode, 0, probe.stderr)
        file_, repo_root, profiles_dir, skills_dir = probe.stdout.splitlines()
        self.assertTrue(file_.startswith(str(self.ws)), f"el paquete se importó por el enlace del workspace: {file_}")
        self.assertEqual(repo_root, str(ROOT), "resolve() sigue el enlace: REPO_ROOT es la instalación")
        self.assertEqual(profiles_dir, str(ROOT / "profiles"))
        self.assertEqual(skills_dir, str(ROOT / ".claude" / "skills"))

    def test_detect_evalua_los_perfiles_de_la_instalacion(self):
        result = _run(self.ws, "detect", "legacy/")
        self.assertEqual(result.returncode, 0, result.stderr)
        perfiles = len(list((ROOT / "profiles").glob("*/profile.json")))
        self.assertIn(f"{perfiles} perfil(es) evaluados", result.stdout)
        self.assertIn("Vi 1 archivo(s)", result.stdout, "solo NOTAS.md: el workspace recién creado no trae artefactos")

    def test_el_guardia_copiado_bloquea_desde_el_workspace(self):
        """El hook que Claude Code invoca desde la raíz del workspace es la copia; `pepper.sensitive` lo
        importa por el enlace. Un Read del respaldo bajo legacy/ se bloquea igual que en el clon."""
        import json

        (self.ws / "legacy" / "respaldo.sql").write_text("INSERT INTO persona VALUES ('x');\n", encoding="utf-8")
        hook = self.ws / "scripts" / "guardia_datos.py"
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PEPPER_GUARDIA_DEV")}
        env["CLAUDE_PROJECT_DIR"] = str(self.ws)

        def run_hook(tool, payload):
            event = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": payload, "cwd": str(self.ws)}
            return subprocess.run([sys.executable, str(hook)], input=json.dumps(event), cwd=str(self.ws), env=env,
                                  capture_output=True, text=True, timeout=60)

        blocked = run_hook("Read", {"file_path": str(self.ws / "legacy" / "respaldo.sql")})
        self.assertEqual(blocked.returncode, 2, blocked.stderr)
        self.assertIn("guardia de datos", blocked.stderr)
        allowed = run_hook("Read", {"file_path": str(self.ws / "legacy" / "NOTAS.md")})
        self.assertEqual(allowed.returncode, 0, "NOTAS.md es texto limpio: el escáner de la instalación lo juzga por el enlace\n" + allowed.stderr)
        allowed = run_hook("Bash", {"command": "python3 -m pepper detect legacy/"})
        self.assertEqual(allowed.returncode, 0, allowed.stderr)

    def test_demo_corre_desde_el_workspace_y_deja_su_salida_ahi(self):
        result = _run(self.ws, "demo")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        out = self.ws / "pepper-out" / "legacy-demo"
        self.assertTrue((out / "package" / "evidence" / "events.jsonl").is_file(), "el paquete queda en el workspace")
        self.assertTrue((out / "correlated").is_dir())
        self.assertIn(str(ROOT / "examples" / "legacy-demo" / "expected" / "notes.md"), result.stdout,
                      "el fixture es de la instalación")
        self.assertNotIn(str(self.ws / "pepper"), result.stdout)


class LaHerramientaCopiadaNoEsElLegacyTest(Base):
    """`detect .` y `package --legacy .` en un workspace excluyen lo que init copió o enlazó."""

    def setUp(self):
        super().setUp()
        create_workspace(self.ws)

    def test_tool_paths_cubre_todo_lo_que_init_deja(self):
        tool = tool_paths(self.ws)
        self.assertTrue(tool, "el marcador .claude/commands/pepper.md está en el workspace")
        for rel in (*TOOL_COPIES, HOME_FILE, "pepper", "pepper/cli.py", "pepper/inspect/systemmap.py", "scripts", "templates",
                    "docs/pepper/system-map.json", "pepper-out/x", "evidence/x"):
            self.assertTrue(is_tool_path(self.ws / rel, tool), f"{rel} es herramienta o salida de PEPPER, no legacy")
        for rel in ("legacy/sistema.war", "legacy/NOTAS.md", "src/main/java/App.java", "pom.xml"):
            self.assertFalse(is_tool_path(self.ws / rel, tool), f"{rel} sí es del legacy")

    def test_detect_no_ve_la_herramienta_copiada(self):
        import zipfile

        from pepper.detect import _walk, detect, inventory

        # un WAR de verdad en legacy/ y, en la copia de la herramienta, texto que parece señal
        with zipfile.ZipFile(self.ws / "legacy" / "app.war", "w") as war:
            war.writestr("WEB-INF/jboss-web.xml", "<jboss-web/>")
        vistos = {p.relative_to(self.ws).as_posix() for p in _walk(self.ws)}
        archivos = {p.relative_to(self.ws).as_posix() for p in _walk(self.ws) if p.is_file()}
        self.assertEqual(archivos, {".gitignore", "legacy/NOTAS.md", "legacy/app.war"}, vistos)
        # `docs/` a secas no es herramienta (encima del repo del legacy trae SU documentación); docs/pepper sí
        self.assertLessEqual(vistos - archivos, {"legacy", "docs"}, vistos)
        self.assertEqual(inventory(self.ws)["files"], 3)
        for result in detect(self.ws):
            for match in result["matches"]:
                self.assertTrue(match["hit"].startswith("legacy/"), f"{result['profile_id']} pegó en la herramienta: {match}")

    def test_package_ignora_la_herramienta_al_copiar_el_legacy(self):
        from pepper.package import _legacy_ignore

        ignore = _legacy_ignore(self.ws)
        names = sorted(p.name for p in self.ws.iterdir()) + ["src", "pom.xml"]
        ignored = set(ignore(str(self.ws), names))
        for name in (".claude", ".pepper-home", ".gitignore", "pepper", "scripts", "templates", "CLAUDE.md", "AGENTS.md",
                     "legacy", "pepper-out", "evidence"):
            self.assertIn(name, ignored, f"{name} viajaría en el paquete como si fuera del legacy")
        self.assertNotIn("src", ignored)
        self.assertNotIn("pom.xml", ignored)
        # docs/ del legacy viaja; docs/pepper (el producto de PEPPER) no
        self.assertNotIn("docs", ignored)
        self.assertEqual(set(ignore(str(self.ws / "docs"), ["pepper", "manual-de-usuario.md"])), {"pepper"})


class RutasDelComposeTest(Base):
    """Con el workspace aparte, legacy/ ya no está bajo REPO_ROOT: las rutas de los volúmenes tienen
    que seguir siendo aceptables para `pepper isolate`."""

    def test_relativas_al_compose_dentro_del_workspace_y_absolutas_fuera(self):
        from pepper.rehydrate import _relative_to

        create_workspace(self.ws)
        self.assertEqual(find_root(self.ws / "pepper-out" / "rehydrate"), self.ws.resolve())
        war = self.ws / "legacy" / "app.war"
        self.assertEqual(_relative_to(war, self.ws / "pepper-out" / "rehydrate"), "../../legacy/app.war")
        self.assertEqual(_relative_to(war, self.ws), "./legacy/app.war", "sin `./` Compose lo leería como volumen nombrado")
        # dos árboles sin marcador: no hay workspace común, la ruta va absoluta
        otro = self.tmp / "suelto"
        (otro / "legacy").mkdir(parents=True)
        self.assertIsNone(find_root(otro))
        self.assertEqual(_relative_to(otro / "legacy" / "app.war", otro / "out"), str((otro / "legacy" / "app.war").resolve()))
        # un legacy fuera del workspace del compose: absoluta también
        self.assertEqual(_relative_to(otro / "legacy" / "app.war", self.ws / "pepper-out" / "rehydrate"),
                         str((otro / "legacy" / "app.war").resolve()))

    def test_rehydrate_planea_en_un_workspace_fuera_del_clon_y_el_compose_pasa_isolate(self):
        from pepper.rehydrate import make_plan, render
        from tests.test_rehydrate import PROFILE, _make_war
        from tests.test_systemmap import TABLES, write_custom_dump

        create_workspace(self.ws)
        legacy = self.ws / "legacy"
        _make_war(legacy / "nominas-2.3.war")
        write_custom_dump(legacy / "respaldo.dump", TABLES)
        with (legacy / "NOTAS.md").open("a", encoding="utf-8") as notes:   # la persona escribe su línea en la plantilla
            notes.write("\nproducción: aplicaciones es un wildfly 21\n")
        plan = make_plan(legacy, PROFILE)
        self.assertEqual(plan.server, "wildfly")
        out = self.ws / "pepper-out" / "rehydrate"
        render(plan, PROFILE, out)
        compose_text = (out / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("- ../../legacy/nominas-2.3.war:/opt/jboss/wildfly/standalone/deployments/nominas-2.3.war:ro", compose_text)
        self.assertIn("- ../../legacy/respaldo.dump:/dump/backup.dump:ro", compose_text)
        self.assertNotIn(str(ROOT), compose_text, "nada del compose apunta a la instalación")
        self.assertEqual((out / "proxy" / "proxy.py").read_bytes(), (ROOT / "pepper" / "proxy.py").read_bytes())
        try:
            from pepper.isolate import check_static, resolve_compose
            compose, resolved = resolve_compose(out / "docker-compose.yml")
        except RuntimeError:
            self.skipTest("sin docker compose ni pyyaml para resolver el compose")
        report = check_static(compose, plan.external_hosts, "ingress", resolved=resolved, compose_dir=out)
        self.assertNotEqual(report.verdict, "FAILED", [f.check for f in report.errors])
        montajes = [f.check for f in report.errors + report.unknowns if "monta" in f.check]
        self.assertEqual(montajes, [], "los volúmenes al artefacto y al respaldo son archivos concretos: se aceptan")


if __name__ == "__main__":
    unittest.main()
