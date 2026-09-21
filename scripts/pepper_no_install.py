"""Backend de build de PEPPER: se niega a construir.

PEPPER se usa desde el clon (`python3 -m pepper …`): el núcleo lee `schemas/`,
`profiles/`, `examples/` y `.claude/skills/` de la raíz del repositorio. Un wheel
solo llevaría `pepper/` y dejaría un ejecutable que no encuentra sus perfiles ni el
fixture de la demo, pero se instalaría sin quejarse (revisión 2026-09-21). Este
backend hace que `pip install .`, `pip wheel .` y `python -m build` fallen con el
porqué, en vez de producir un artefacto a medias.
"""

MESSAGE = (
    "PEPPER no se instala: se usa desde el clon con `python3 -m pepper …` "
    "(lee schemas/, profiles/, examples/ y .claude/skills/ de la raíz del repositorio). "
    "Dependencias: `pip install -r requirements-dev.txt`. Ver docs/documentacion/DECISIONES.md (D28)."
)


class NotInstallable(RuntimeError):
    pass


def _refuse(*_args, **_kwargs):
    raise NotInstallable(MESSAGE)


build_wheel = _refuse
build_sdist = _refuse
build_editable = _refuse
prepare_metadata_for_build_wheel = _refuse
prepare_metadata_for_build_editable = _refuse


def get_requires_for_build_wheel(config_settings=None):
    return []


def get_requires_for_build_sdist(config_settings=None):
    return []


def get_requires_for_build_editable(config_settings=None):
    return []
