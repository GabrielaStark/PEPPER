"""Para las pruebas: armar un paquete remoto como lo haría una persona que autoriza la propuesta (D24)."""

from pathlib import Path

from pepper.boundary import BoundaryError, authorize
from pepper.package import assemble


def assemble_authorized(correlated, out, legacy=None, by="prueba", **kwargs):
    """assemble; si la frontera pide autorización, la da (a nombre de `by`) y repite."""
    authorization = Path(out).parent / f"{Path(out).name}.data-boundary.json"
    try:
        return assemble(correlated, out, legacy, authorization=authorization, **kwargs)
    except BoundaryError as error:
        authorize(error.proposal_path, by, authorization)
        return assemble(correlated, out, legacy, authorization=authorization, **kwargs)
