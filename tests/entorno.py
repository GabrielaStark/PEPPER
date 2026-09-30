"""Lo que el entorno de pruebas puede o no puede hacer, dicho una vez.

Una revisión externa (2026-09-30) corrió la suite en una máquina sin permiso para abrir sockets
y cuatro pruebas dieron ERROR en vez de omitirse: la prueba del stub, la del proxy y una del
aislamiento abren un socket en loopback (o uno Unix) para hablar de verdad, y sin permiso reventaban
en `setUp`. Un entorno que no deja abrir sockets no es una falla del código: fuera de CI esas
pruebas se saltan diciendo por qué; en CI (`PEPPER_CI=1`) nada se salta y el error es real.
"""

from __future__ import annotations

import os
import socket
import tempfile
import unittest


def en_ci() -> bool:
    return bool(os.environ.get("PEPPER_CI"))


def _puede(family: int, address) -> bool:
    try:
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            probe.bind(address)
            probe.listen(1)
        return True
    except OSError:
        return False


def exige_sockets_loopback() -> None:
    """Salta la prueba si aquí no se puede abrir un socket TCP en 127.0.0.1 (en CI no salta)."""
    if _puede(socket.AF_INET, ("127.0.0.1", 0)):
        return
    if en_ci():
        raise AssertionError("en CI la prueba es obligatoria: no se pudo abrir un socket en 127.0.0.1")
    raise unittest.SkipTest("este entorno no permite abrir sockets en 127.0.0.1 (no es una falla del código)")


def exige_sockets_unix() -> None:
    """Salta la prueba si aquí no se puede crear un socket Unix (en CI no salta)."""
    if not hasattr(socket, "AF_UNIX"):
        raise unittest.SkipTest("sin sockets Unix en esta plataforma")
    with tempfile.TemporaryDirectory() as tmp:
        if _puede(socket.AF_UNIX, os.path.join(tmp, "s.sock")):
            return
    if en_ci():
        raise AssertionError("en CI la prueba es obligatoria: no se pudo crear un socket Unix")
    raise unittest.SkipTest("este entorno no permite crear sockets Unix (no es una falla del código)")
