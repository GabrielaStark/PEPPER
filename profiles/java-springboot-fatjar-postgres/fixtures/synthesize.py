#!/usr/bin/env python3
"""Genera el respaldo sintético del fixture: un `pg_dump -Fc` chico, escrito en Python puro.

Un respaldo custom de PostgreSQL es binario y no se versiona; se fabrica al correr la prueba
con el mismo escritor que usa tests/test_systemmap.py. Uso:

    python3 profiles/<id>/fixtures/synthesize.py <directorio-de-salida>   → <directorio>/respaldo.dump

Las tablas tienen la forma de un sistema de trámites en varios servicios: catálogos ct_*,
una tabla de usuarios que se cuenta pero no se vuelca, una tabla grande con estatus.
Ninguna fila es de una persona real.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tests.test_systemmap import write_custom_dump  # noqa: E402

TABLES = {
    "ct_estatus": ("CREATE TABLE public.ct_estatus (\n    id bigint NOT NULL,\n    clave character varying(20),\n    descripcion character varying(80)\n);",
                   [[1, "REGISTRADA", "Solicitud registrada"], [2, "EN_REVISION", "En revisión"], [3, "RESUELTA", "Resuelta"]]),
    "ct_tipo_tramite": ("CREATE TABLE public.ct_tipo_tramite (\n    id bigint NOT NULL,\n    clave character varying(30),\n    admite_prorroga boolean\n);",
                        [[1, "LICENCIA", "t"], [2, "PERMISO", "f"]]),
    "usuario": ("CREATE TABLE public.usuario (\n    id bigint,\n    nombre character varying,\n    correo character varying\n);",
                [[1, "Persona Ficticia", "ficticia@correo.example"]]),
    "solicitud": ("CREATE TABLE public.solicitud (\n    id bigint NOT NULL,\n    folio character varying(20),\n    estatus character varying(20),\n    fecha_registro date\n);",
                  [[i, f"TR-2026-{i:06d}", "REGISTRADA" if i % 4 else "RESUELTA", f"2026-0{1 + (i % 9)}-15"] for i in range(1, 31)]),
}
FUNCTIONS = [("fn_folio()", "CREATE FUNCTION public.fn_folio() RETURNS trigger\n    LANGUAGE plpgsql\n    AS $$BEGIN IF NEW.folio IS NULL THEN NEW.folio := 'TR-' || nextval('folio_seq'); END IF; RETURN NEW; END;$$;")]
TRIGGERS = [("solicitud trg_folio", "CREATE TRIGGER trg_folio BEFORE INSERT ON public.solicitud FOR EACH ROW EXECUTE PROCEDURE public.fn_folio();")]


def main(out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "respaldo.dump"
    write_custom_dump(target, TABLES, FUNCTIONS, TRIGGERS)
    return target


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("uso: synthesize.py <directorio-de-salida>")
    print(main(Path(sys.argv[1])))
