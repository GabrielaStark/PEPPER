#!/usr/bin/env python3
"""Genera el respaldo sintético del fixture: un `pg_dump -Fc` chico, escrito en Python puro.

Un respaldo custom de PostgreSQL es binario y no se versiona; se fabrica al correr la prueba
con el mismo escritor que usa tests/test_systemmap.py (cabecera, TOC y bloques zlib). Uso:

    python3 profiles/<id>/fixtures/synthesize.py <directorio-de-salida>   → <directorio>/respaldo.dump

Las tablas tienen la forma del legacy que este perfil describió (catálogos ct*, una tabla de
personas que se cuenta pero no se vuelca, una tabla grande con columna de estado); ninguna fila
es de una persona real.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tests.test_systemmap import write_custom_dump  # noqa: E402

TABLES = {
    "ctroles": ("CREATE TABLE public.ctroles (\n    llrol bigint NOT NULL,\n    boactivo boolean,\n    dsrol character varying(50)\n);",
                [[1, "t", "ADMIN"], [2, "t", "RECEPCION"], [3, "f", "TITULAR"]]),
    "ctestatus": ("CREATE TABLE public.ctestatus (\n    llestatus bigint NOT NULL,\n    dsestatus character varying(30)\n);",
                  [[1, "AGENDADA"], [2, "ATENDIDA"], [3, "CANCELADA"]]),
    "trabajador": ("CREATE TABLE public.trabajador (\n    lltrabajador bigint,\n    dsnombre character varying,\n    dscurp character varying(18)\n);",
                   [[1, "Persona Ficticia", "XXXX000000XXXXXX00"]]),
    "cita": ("CREATE TABLE public.cita (\n    llcita bigint NOT NULL,\n    dsestatus character varying(20),\n    fcalta date,\n    CONSTRAINT pk_cita PRIMARY KEY (llcita)\n);",
             [[i, "AGENDADA" if i % 5 else "CANCELADA", f"202{4 + (i % 2)}-01-0{1 + (i % 9)}"] for i in range(1, 41)]),
}
FUNCTIONS = [("fn_estatus()", "CREATE FUNCTION public.fn_estatus() RETURNS trigger\n    LANGUAGE plpgsql\n    AS $$BEGIN IF NEW.dsestatus IS NULL THEN NEW.dsestatus := 'AGENDADA'; END IF; RETURN NEW; END;$$;")]
TRIGGERS = [("cita trg_estatus", "CREATE TRIGGER trg_estatus BEFORE INSERT ON public.cita FOR EACH ROW EXECUTE PROCEDURE public.fn_estatus();")]
VIEWS = [("vw_citas_pendientes", "CREATE VIEW public.vw_citas_pendientes AS SELECT llcita FROM public.cita WHERE dsestatus = 'AGENDADA';")]


def main(out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "respaldo.dump"
    write_custom_dump(target, TABLES, FUNCTIONS, TRIGGERS, VIEWS)
    return target


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("uso: synthesize.py <directorio-de-salida>")
    print(main(Path(sys.argv[1])))
