"""`pepper init <dir>`: un workspace aparte del clon de la herramienta.

Antes el flujo era `git clone PEPPER mi-legacy && rm -rf .git`: herramienta y trabajo en la misma
carpeta. Eso impedía actualizar la herramienta, versionarla y devolver un perfil como contribución
(auditoría 2026-09-29). Ahora el clon se queda como *instalación* y cada legacy vive en su
*workspace*:

    <dir>/
      pepper -> <instalación>/pepper     enlace: `python3 -m pepper …` corre desde aquí tal cual
      docs/documentacion, profiles, schemas -> <instalación>/…   enlaces: los comandos citan PRINCIPIOS.md
                                         y REFERENCIA.md, un borrador de perfil se escribe en profiles/
                                         DE LA INSTALACIÓN (es la contribución), y los contratos se leen
      .pepper-home                       ruta, versión y commit de la instalación
      .claude/                           copia: commands, agents, skills, settings.json
      scripts/guardia_datos.py           copia: el hook que Claude Code invoca desde la raíz
      CLAUDE.md, AGENTS.md, templates/   copias
      legacy/NOTAS.md                    aquí van el desplegable y el respaldo
      docs/pepper/                       el PRODUCTO (mapa, entorno, funcional.md); se versiona
      pepper-out/, evidence/             datos ajenos; nunca se versionan
      .gitignore                         el del workspace

Copias y no enlaces para lo que Claude Code carga de la raíz (los comandos, los agentes, los
hooks) y para lo que una persona debe poder leer sin seguir un enlace. El paquete sí va enlazado:
así `REPO_ROOT`, `PROFILES_DIR`, `SCHEMAS_DIR` y `SKILLS_DIR` (derivados de `__file__` resuelto)
apuntan a la instalación y una actualización del clon llega al workspace sin recopiar nada.

No se crea un repositorio git ni un remoto: el workspace no tiene a dónde subir nada por accidente.
Si la persona quiere versionar el producto, hace `git init` ella.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import List

from pepper import REPO_ROOT, __version__
from pepper.workspace import HOME_FILE, MARKER, installation_commit

# Lo que se copia de la instalación, tal cual, a la raíz del workspace.
CLAUDE_ITEMS = ("commands", "agents", "skills", "settings.json")
COPIED = ("CLAUDE.md", "AGENTS.md", "templates", "scripts/guardia_datos.py")
# Lo que se ENLAZA a la instalación: la documentación que los comandos citan, los perfiles (un
# borrador redactado en el workspace cae en el clon, listo para contribuirse) y los contratos.
LINKED = ("docs/documentacion", "profiles", "schemas")
# Lo que es trabajo: `--force` no lo toca.
WORK_DIRS = ("legacy", "docs/pepper", "pepper-out", "evidence")

GITIGNORE = """\
# .gitignore del workspace de PEPPER (lo escribió `pepper init`).
#
# docs/pepper/ es el PRODUCTO — el mapa, el entorno y funcional.md describen el sistema — y SÍ se
# versiona: es lo que este workspace existe para producir. Todo lo demás, no.

# Datos ajenos: el desplegable, el respaldo y la evidencia cruda. Nunca.
/legacy/
/evidence/

# Salidas intermedias: compose con credenciales, paquetes, autorización de datos y su llave.
/pepper-out/

# La herramienta: el enlace al paquete de la instalación, su marca y las copias que Claude Code
# carga de la raíz. Se recrean con `python3 -m pepper init . --force`; no son parte del producto.
/pepper
/.pepper-home
/.claude/
/scripts/guardia_datos.py
/CLAUDE.md
/AGENTS.md
/templates/
/docs/documentacion
/profiles
/schemas

# Configuración del explorador: claves de usuario reales y contraseña de prueba. Vive en
# pepper-out/, nunca en docs/pepper/.
/docs/pepper/explore.json

__pycache__/
*.pyc
.DS_Store
"""


class InitError(Exception):
    """El workspace no se puede crear; el mensaje dice por qué."""


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _check_installation(home: Path) -> None:
    missing = [rel for rel in (str(MARKER), "pepper/__init__.py", *COPIED, *LINKED) if not (home / rel).exists()]
    missing += [f".claude/{item}" for item in CLAUDE_ITEMS if not (home / ".claude" / item).exists()]
    if missing:
        raise InitError(f"la instalación en {home} no trae {', '.join(missing)}: ¿es un clon completo de PEPPER?")


def _copy(source: Path, target: Path) -> None:
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"))
    else:
        shutil.copy2(source, target)


def _link(root: Path, home: Path, rel: str, expect_file: str) -> Path:
    """Un enlace simbólico `root/rel` → `home/rel`; `expect_file` tiene que existir detrás."""
    link = root / rel
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink():
        link.unlink()
    elif link.exists():
        raise InitError(f"{link} existe y no es un enlace: un workspace no lleva una copia de la herramienta (bórralo y repite)")
    try:
        link.symlink_to(home / rel, target_is_directory=True)
    except OSError as error:
        raise InitError(f"no pude crear el enlace {link} → {home / rel} ({error}); "
                        "sin enlace simbólico no hay workspace aparte")
    if not (link / expect_file).is_file():
        link.unlink()
        raise InitError(f"el enlace {link} no lleva a la instalación de PEPPER ({home / rel})")
    return link


def _link_package(root: Path, home: Path) -> Path:
    return _link(root, home, "pepper", "__init__.py")


def _write_home(root: Path, home: Path) -> None:
    lines = ["# Instalación de PEPPER que usa este workspace. Lo escribió `pepper init`; no se edita a mano.",
             f"home: {home}", f"version: {__version__}"]
    commit = installation_commit(home)
    if commit:
        lines.append(f"commit: {commit}")
    (root / HOME_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")


def create_workspace(directory: Path, force: bool = False, home: Path = REPO_ROOT) -> List[str]:
    """Crea (o con `force`, actualiza) el workspace en `directory`. Devuelve las líneas del informe."""
    if os.name == "nt":
        raise InitError("Windows no está soportado: el workspace necesita un enlace simbólico al paquete de PEPPER "
                        "(y el núcleo se ha probado solo en macOS y Linux)")
    home = home.resolve()
    _check_installation(home)
    root = directory.resolve()
    if root == home or _is_under(root, home):
        raise InitError(f"{root} está dentro del clon de la herramienta ({home}): el workspace va aparte, "
                        "en otra carpeta (si no, el legacy y el producto quedarían en el repositorio de PEPPER)")
    if root.exists() and not root.is_dir():
        raise InitError(f"{root} existe y no es un directorio")
    root.mkdir(parents=True, exist_ok=True)
    existing = sorted(p.name for p in root.iterdir())
    if existing and not force:
        raise InitError(f"{root} ya tiene contenido ({', '.join(existing[:6])}{'…' if len(existing) > 6 else ''}). "
                        "Usa un directorio vacío; o, si es un workspace de PEPPER y quieres actualizar la herramienta "
                        "copiada, repite con --force (no toca legacy/, docs/pepper/, pepper-out/ ni evidence/)")

    report: List[str] = []
    # Primero el enlace: si el sistema de archivos no lo permite, no se deja nada a medias.
    link = _link_package(root, home)
    report.append(f"  pepper → {os.readlink(link)}  (enlace al núcleo de la instalación)")
    _write_home(root, home)
    report.append(f"  {HOME_FILE}  (instalación, versión y commit)")
    for rel, marker_file in zip(LINKED, ("PRINCIPIOS.md", "README.md", "profile.schema.json")):
        _link(root, home, rel, marker_file)
    report.append(f"  {', '.join(LINKED)} → instalación  (enlaces: documentación, perfiles —un borrador nuevo cae en el clon— y contratos)")

    for item in CLAUDE_ITEMS:
        _copy(home / ".claude" / item, root / ".claude" / item)
    report.append("  .claude/  (commands, agents, skills, settings.json: copia)")
    for rel in COPIED:
        _copy(home / rel, root / rel)
    report.append(f"  {', '.join(COPIED)}  (copias)")

    for rel in WORK_DIRS:
        (root / rel).mkdir(parents=True, exist_ok=True)
    keep = root / "docs" / "pepper" / ".gitkeep"
    if not keep.exists():
        keep.write_text("", encoding="utf-8")
    notes = root / "legacy" / "NOTAS.md"
    if notes.exists():
        report.append("  legacy/NOTAS.md  (ya existía: no se toca)")
    else:
        shutil.copy2(home / "templates" / "NOTAS-LEGACY.md", notes)
        report.append("  legacy/NOTAS.md  (plantilla: escribe ahí lo que sabes del sistema)")
    report.append("  docs/pepper/ (el producto), pepper-out/ y evidence/ (datos ajenos)")

    gitignore = root / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(GITIGNORE, encoding="utf-8")
        report.append("  .gitignore  (del workspace: ignora legacy/, evidence/, pepper-out/ y la herramienta; docs/pepper/ se versiona)")
    elif gitignore.read_text(encoding="utf-8", errors="replace") != GITIGNORE:
        report.append("  .gitignore  (ya existía y es distinto: no se toca)")
    return report
