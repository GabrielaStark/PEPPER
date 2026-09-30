"""Registro de lectores de `pepper map`: aquí SÍ se escribe Python por tecnología; un perfil parametriza estos lectores.

El núcleo no conoce SISTEMAS; conoce FORMATOS: bytecode de la JVM vía `javap`, Groovy
compilado, YAML de Spring, `pg_dump -Fc`, SQL en texto, plantillas y configuración como
texto. Cada formato tiene aquí UN lector con nombre (su "mecanismo"), y `extractors.json`
del perfil dice cuáles corren y con qué patrones. Un formato nuevo es un lector nuevo en
este paquete más su entrada en esta tabla y en `schemas/extractors.schema.json`; un
sistema nuevo del mismo formato es solo un perfil.

Hasta la auditoría 2026-09-29 este registro vivía repartido en tres tablas hermanas de
`systemmap.py` (`_MECHANISMS`, `_MECHANISM_SURFACES`, `DUMP_MECHANISMS`) más una función
`_groovy()` cableada a mano: agregar un mecanismo exigía tocar las tres y era fácil dejar
una desalineada (un mecanismo sin superficie declarada no contaba para la cobertura). Ahora
una sola fila lo dice todo: cómo corre, qué superficies del mapa alimenta, si necesita el
respaldo y si necesita `javap`.

Los lectores de archivo (groovy.py, jvm.py, pgdump.py, sqldump.py, y los extractores de
systemmap.py) se quedan donde están; esto es solo el despacho. Un mecanismo que no esté
en la tabla sigue siendo un hueco declarado en el mapa, no un traceback.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

# Las superficies del mapa que un extractor puede alimentar. Lo que ningún mecanismo del
# perfil cubre no puede salir como "cero": sale como hueco declarado (D23).
SURFACES: Tuple[str, ...] = ("entrypoints", "jobs", "external_dependencies", "data_stores",
                             "catalogs", "classes", "screens")

# Firma común: (artefacto, spec del extractor, reporte del mapa, contexto {dump, tools}).
Reader = Callable[[Path, Dict[str, Any], Any, Dict[str, Any]], None]


@dataclass(frozen=True)
class Mechanism:
    """Un lector registrado: cómo corre y qué le hace falta."""

    name: str
    run: Reader
    surfaces: Tuple[str, ...]        # superficies fijas que alimenta
    needs_dump: bool = False         # lee el respaldo (--dump), no el artefacto
    needs_javap: bool = False        # lee bytecode de la JVM con `javap`
    surface_from_spec: Optional[str] = None   # la superficie la declara el perfil en esta clave del spec
    description: str = ""

    def covers(self, spec: Dict[str, Any]) -> Tuple[str, ...]:
        """Las superficies que ESTE extractor (con su spec) alimenta."""
        if self.surface_from_spec:
            declared = spec.get(self.surface_from_spec)
            return (declared,) if declared in SURFACES else ()
        return self.surfaces


# --- adaptadores: los lectores viven en sus módulos; aquí solo se les da la firma común ---
# Importan tarde para no formar un ciclo (systemmap importa este registro al cargar).

def _archive_url_scan(artifact: Path, spec: Dict[str, Any], report: Any, ctx: Dict[str, Any]) -> None:
    from pepper.inspect import systemmap
    systemmap._extract_archive_urls(artifact, spec, report)


def _config_hosts(artifact: Path, spec: Dict[str, Any], report: Any, ctx: Dict[str, Any]) -> None:
    from pepper.inspect import systemmap
    systemmap._extract_config_hosts(artifact, spec, report)


def _pg_dump_custom(artifact: Path, spec: Dict[str, Any], report: Any, ctx: Dict[str, Any]) -> None:
    from pepper.inspect import systemmap
    systemmap._extract_pg_dump(spec, report, ctx.get("dump"))


def _sql_dump(artifact: Path, spec: Dict[str, Any], report: Any, ctx: Dict[str, Any]) -> None:
    from pepper.inspect import systemmap
    systemmap._extract_sql_dump(spec, report, ctx.get("dump"))


def _jvm_route_annotations(artifact: Path, spec: Dict[str, Any], report: Any, ctx: Dict[str, Any]) -> None:
    from pepper.inspect import systemmap
    systemmap._extract_jvm_routes(artifact, spec, report, ctx.get("tools") or {})


def _jvm_class_inventory(artifact: Path, spec: Dict[str, Any], report: Any, ctx: Dict[str, Any]) -> None:
    from pepper.inspect import systemmap
    systemmap._extract_jvm_classes(artifact, spec, report, ctx.get("tools") or {})


def _view_templates(artifact: Path, spec: Dict[str, Any], report: Any, ctx: Dict[str, Any]) -> None:
    from pepper.inspect import systemmap
    systemmap._extract_views(artifact, spec, report)


def _groovy_config_values(artifact: Path, spec: Dict[str, Any], report: Any, ctx: Dict[str, Any]) -> None:
    from pepper.inspect import groovy
    groovy.extract_config_values(artifact, spec, report, ctx.get("tools") or {})


def _groovy_controller_actions(artifact: Path, spec: Dict[str, Any], report: Any, ctx: Dict[str, Any]) -> None:
    from pepper.inspect import groovy
    groovy.extract_controller_actions(artifact, spec, report, ctx.get("tools") or {})


def _groovy_url_mappings(artifact: Path, spec: Dict[str, Any], report: Any, ctx: Dict[str, Any]) -> None:
    from pepper.inspect import groovy
    groovy.extract_url_mappings(artifact, spec, report, ctx.get("tools") or {})


def _table(*mechanisms: Mechanism) -> Dict[str, Mechanism]:
    return {m.name: m for m in mechanisms}


MECHANISMS: Dict[str, Mechanism] = _table(
    Mechanism("archive_url_scan", _archive_url_scan, ("external_dependencies",),
              description="URLs externas incrustadas en cualquier miembro de texto del artefacto, agrupadas por host"),
    Mechanism("config_hosts", _config_hosts, ("external_dependencies",),
              description="hosts/urls en archivos de configuración `clave: valor`"),
    Mechanism("pg_dump_custom", _pg_dump_custom, ("data_stores", "catalogs"), needs_dump=True,
              description="respaldo custom de pg_dump (-Fc) leído en Python puro"),
    Mechanism("sql_dump", _sql_dump, ("data_stores", "catalogs"), needs_dump=True,
              description="respaldo SQL en texto (mysqldump, mariadb-dump, pg_dump plano)"),
    Mechanism("jvm_route_annotations", _jvm_route_annotations, ("entrypoints", "jobs"), needs_javap=True,
              description="rutas @*Mapping y jobs @Scheduled de Spring vía `javap -v`"),
    Mechanism("jvm_class_inventory", _jvm_class_inventory, ("classes",), needs_javap=True,
              description="por clase: métodos públicos, constantes y cadenas vía `javap -c -constants`"),
    Mechanism("view_templates", _view_templates, ("screens",),
              description="pantallas leídas con las regex del perfil sobre plantillas de texto"),
    Mechanism("groovy_config_values", _groovy_config_values, ("jobs",), needs_javap=True,
              description="Config/DataSource de Grails reconstruidos del bytecode: jobs y notas"),
    Mechanism("groovy_controller_actions", _groovy_controller_actions, ("entrypoints",), needs_javap=True,
              description="acciones de controladores Grails → rutas por convención"),
    Mechanism("groovy_url_mappings", _groovy_url_mappings, ("entrypoints",), needs_javap=True,
              description="UrlMappings de Grails → rutas declaradas"),
)

# Los que leen el respaldo, no el artefacto: en un sistema de varias piezas corren UNA vez,
# con la pieza que habla con la base. En las demás no son un hueco: no les toca.
DUMP_MECHANISMS: Tuple[str, ...] = tuple(name for name, m in MECHANISMS.items() if m.needs_dump)
