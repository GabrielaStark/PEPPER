#!/usr/bin/env python3
"""Genera el respaldo sintético del fixture: un `pg_dump -Fc` chico con el esquema del legacy-demo.

Un respaldo custom de PostgreSQL es binario y no se versiona; se fabrica al correr la prueba
con el mismo escritor que usa tests/test_systemmap.py. Uso:

    python3 profiles/<id>/fixtures/synthesize.py <directorio-de-salida>   → <directorio>/respaldo.dump

Las tablas son las de examples/legacy-demo/artifacts/database/01-schema.sql (citizen,
application, application_history); las filas son ficticias.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tests.test_systemmap import write_custom_dump  # noqa: E402

TABLES = {
    "citizen": ("CREATE TABLE public.citizen (\n    id bigint NOT NULL,\n    nombre character varying(200) NOT NULL,\n    curp character varying(18),\n"
                "    status character varying(20) NOT NULL,\n    nationality character varying(2) NOT NULL,\n    fecha_alta timestamp without time zone NOT NULL\n);",
                [[1001, "Persona Ficticia Uno", None, "ACTIVE", "MX", "2019-03-12 10:00:00"],
                 [1003, "Persona Ficticia Tres", None, "SUSPENDED", "MX", "2019-03-12 10:00:00"]]),
    "application": ("CREATE TABLE public.application (\n    id bigint NOT NULL,\n    folio character varying(20) NOT NULL,\n    citizen_id bigint NOT NULL,\n"
                    "    tipo_tramite character varying(50) NOT NULL,\n    estado character varying(30) NOT NULL,\n    fecha_registro timestamp without time zone NOT NULL\n);",
                    [[87, "SOL-2026-000042", 1001, "LICENCIA_FUNCIONAMIENTO", "REGISTERED", "2026-08-25 13:21:01"]]),
    "application_history": ("CREATE TABLE public.application_history (\n    id bigint NOT NULL,\n    application_id bigint NOT NULL,\n"
                            "    estado character varying(30) NOT NULL,\n    fecha timestamp without time zone NOT NULL\n);",
                            [[1, 87, "REGISTERED", "2026-08-25 13:21:02"]]),
}


def main(out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "respaldo.dump"
    write_custom_dump(target, TABLES)
    return target


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("uso: synthesize.py <directorio-de-salida>")
    print(main(Path(sys.argv[1])))
