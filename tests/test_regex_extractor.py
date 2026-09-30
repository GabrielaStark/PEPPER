"""`regex_extractor`: una familia con fuente en texto (PHP, Django, Rails) entra al mapa con DATOS.

Hermético: un directorio sintético con `routes/web.php` (Laravel), `urls.py` (Django) y
`config/routes.rb` (Rails), y tres perfiles ficticios que solo traen `extractors.json`.
Ninguna línea del núcleo sabe de ninguno de los tres frameworks (auditoría 2026-09-29).
"""

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pepper.inspect.systemmap import build_map, render_map  # noqa: E402
from pepper.validate import validate_instance  # noqa: E402

FUENTE = {
    "routes/web.php": (
        "<?php\n"
        "Route::get('/citas', [CitaController::class, 'index']);\n"
        "Route::post('/citas', [CitaController::class, 'store']);\n"
        "Route::get('/citas', [CitaController::class, 'index']);   // repetida a propósito\n"
        "Route::GET('/pacientes/{id}', [PacienteController::class, 'show']);\n"
        "Route::view('/ayuda', 'ayuda');\n"
    ),
    "routes/api.php": "<?php\nRoute::post('/api/turnos', [TurnoApi::class, 'crear']);\n",
    "app/Console/Kernel.php": (
        "<?php\nclass Kernel {\n  protected function schedule(Schedule $schedule) {\n"
        "    $schedule->command('citas:recordar')->dailyAt('08:00');\n"
        "    $schedule->command('turnos:cerrar')->hourly();\n  }\n}\n"
    ),
    ".env": "APP_NAME=Citas\nMAIL_HOST=smtp.correo.example\nMAIL_PORT=587\nDB_PASSWORD=secreta\n",
    "urls.py": (
        "from django.urls import path\nfrom . import views\n\n"
        "urlpatterns = [\n"
        "    path('citas/', views.listar, name='citas'),\n"
        "    path('citas/<int:id>/', views.detalle),\n"
        "    path('', views.inicio),\n"
        "]\n"
    ),
    "config/routes.rb": (
        "Rails.application.routes.draw do\n"
        "  get 'citas', to: 'citas#index'\n"
        "  post 'citas', to: 'citas#create'\n"
        "  resources :pacientes\n"
        "  delete 'citas/:id', to: 'citas#destroy'\n"
        "end\n"
    ),
    "vendor/lib/routes/web.php": "<?php\nRoute::get('/de-una-libreria', fn () => 1);\n",
}

# Tres perfiles ficticios: solo extractors.json, ni una línea de Python.
PERFIL_LARAVEL = {"extractors": [
    {"mechanism": "regex_extractor", "surface": "entrypoints",
     "member_patterns": [r"^routes/.*\.php$"], "exclude_patterns": [r"^vendor/"],
     "pattern": r"Route::(?P<method>get|post|put|patch|delete)\('(?P<path>[^']+)'(?:,\s*\[(?P<handler>[^\]]+)\])?",
     "flags": "i", "transforms": {"method": "upper", "handler": "strip"}},
    {"mechanism": "regex_extractor", "surface": "entrypoints",
     "member_patterns": [r"^routes/api\.php$"],
     "pattern": r"Route::(?P<method>get|post)\('(?P<path>/api/[^']+)'",
     "flags": "i", "defaults": {"kind": "rest_endpoint"}, "transforms": {"method": ["upper"]}},
    {"mechanism": "regex_extractor", "surface": "jobs",
     "member_patterns": [r"Console/Kernel\.php$"],
     "pattern": r"->command\('(?P<name>[^']+)'\)->(?P<schedule>\w+\([^)]*\))"},
    {"mechanism": "regex_extractor", "surface": "external_dependencies",
     "member_patterns": [r"^\.env$"], "pattern": r"^MAIL_HOST=(?P<host>\S+)$", "flags": "m",
     "defaults": {"kind": "smtp"}},
]}
PERFIL_DJANGO = {"extractors": [
    {"mechanism": "regex_extractor", "surface": "entrypoints",
     "member_patterns": [r"urls\.py$"],
     "pattern": r"path\('(?P<path>[^']*)',\s*(?P<handler>[\w.]+)",
     "transforms": {"path": "leading_slash"}},
]}
PERFIL_RAILS = {"extractors": [
    {"mechanism": "regex_extractor", "surface": "entrypoints",
     "member_patterns": [r"^config/routes\.rb$"],
     "pattern": r"^\s*(?P<method>get|post|put|patch|delete)\s+'(?P<path>[^']+)'(?:,\s*to:\s*'(?P<handler>[^']+)')?",
     "flags": "m", "transforms": {"method": "upper", "path": "leading_slash"}},
]}


def _perfil(root, nombre, extractores):
    carpeta = root / "profiles" / nombre
    carpeta.mkdir(parents=True)
    (carpeta / "extractors.json").write_text(json.dumps(extractores, ensure_ascii=False, indent=2), encoding="utf-8")
    return json.loads((carpeta / "extractors.json").read_text(encoding="utf-8"))["extractors"]


class RegexExtractorTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.fuente = self.root / "app-fuente"
        for name, body in FUENTE.items():
            path = self.fuente / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        self.laravel = _perfil(self.root, "php-laravel-mysql", PERFIL_LARAVEL)
        self.django = _perfil(self.root, "python-django-postgres", PERFIL_DJANGO)
        self.rails = _perfil(self.root, "ruby-rails-postgres", PERFIL_RAILS)

    def tearDown(self):
        self._tmp.cleanup()

    def _rutas(self, mapa):
        return {(e.get("method", ""), e["path"]): e for e in mapa["entrypoints"]}

    def test_laravel_rutas_jobs_y_externos_desde_texto(self):
        mapa = build_map(self.fuente, self.laravel, "php-laravel-mysql", tools={})
        rutas = self._rutas(mapa)
        self.assertIn(("GET", "/citas"), rutas)
        self.assertIn(("POST", "/citas"), rutas)
        self.assertIn(("GET", "/pacientes/{id}"), rutas, "flags=i: Route::GET también cuenta")
        self.assertEqual(rutas[("GET", "/citas")]["handler"], "CitaController::class, 'index'")
        self.assertEqual(rutas[("GET", "/citas")]["kind"], "http_route")
        self.assertEqual(rutas[("GET", "/citas")]["evidence"], "routes/web.php:2", "evidencia = miembro:línea")
        self.assertEqual(sum(1 for m, p in rutas if p == "/citas" and m == "GET"), 1, "la ruta repetida se dice una vez")
        self.assertNotIn(("", "/ayuda"), rutas, "Route::view no casa el patrón: no se inventa")
        self.assertNotIn(("GET", "/de-una-libreria"), rutas, "exclude_patterns saca vendor/")
        self.assertEqual(rutas[("POST", "/api/turnos")]["kind"], "rest_endpoint", "defaults del perfil")
        jobs = {j["name"]: j for j in mapa["jobs"]}
        self.assertEqual(jobs["citas:recordar"]["schedule"], "dailyAt('08:00')")
        self.assertEqual(jobs["turnos:cerrar"]["evidence"], "app/Console/Kernel.php:5")
        [smtp] = mapa["external_dependencies"]
        self.assertEqual((smtp["name"], smtp["target"], smtp["kind"]), ("smtp.correo.example", "smtp.correo.example", "smtp"))
        self.assertNotIn("secreta", json.dumps(mapa), "el .env se lee para el host, no para la clave")
        # entrypoints, jobs y external quedan cubiertos; el resto son huecos declarados, no ceros
        huecos = " ".join(mapa["coverage_gaps"])
        for cubierta in ("entrypoints:", "jobs:", "external_dependencies:"):
            self.assertNotIn(cubierta, huecos)
        for hueco in ("screens", "classes", "data_stores", "catalogs"):
            self.assertIn(hueco, huecos)
        try:
            errores = validate_instance(mapa, "system-map")
        except ImportError:
            self.skipTest("jsonschema no instalado")
        self.assertEqual(errores, [], errores)

    def test_django_y_rails_con_el_mismo_mecanismo(self):
        django = self._rutas(build_map(self.fuente, self.django, "python-django-postgres", tools={}))
        self.assertEqual(set(django), {("", "/citas/"), ("", "/citas/<int:id>/"), ("", "/")})
        self.assertEqual(django[("", "/citas/")]["handler"], "views.listar")
        self.assertEqual(django[("", "/")]["evidence"], "urls.py:7")
        rails = self._rutas(build_map(self.fuente, self.rails, "ruby-rails-postgres", tools={}))
        self.assertEqual(set(rails), {("GET", "/citas"), ("POST", "/citas"), ("DELETE", "/citas/:id")})
        self.assertEqual(rails[("POST", "/citas")]["handler"], "citas#create")
        self.assertFalse(any("pacientes" in p for _, p in rails), "`resources :pacientes` no casa: no se expande por adivinanza")

    def test_tambien_sobre_un_zip(self):
        zipped = self.root / "app.zip"
        with zipfile.ZipFile(zipped, "w") as z:
            for name, body in FUENTE.items():
                z.writestr(name, body)
        rutas = self._rutas(build_map(zipped, self.rails, "ruby-rails-postgres", tools={}))
        self.assertIn(("GET", "/citas"), rutas)
        self.assertEqual(rutas[("GET", "/citas")]["evidence"], "config/routes.rb:2")

    def test_el_render_legible_los_muestra(self):
        mapa = build_map(self.fuente, self.laravel, "php-laravel-mysql", tools={})
        superficie = render_map(mapa)["surface.md"]
        self.assertIn("| POST | `/citas` |", superficie)
        self.assertIn("| citas:recordar | `dailyAt('08:00')` |", superficie)
        self.assertIn("smtp.correo.example", superficie)

    def test_un_patron_invalido_es_hueco_declarado_no_traceback(self):
        roto = [{"mechanism": "regex_extractor", "surface": "entrypoints", "member_patterns": [r"\.php$"],
                 "pattern": r"Route::(?P<method>get|post\('(?P<path>[^']+)'"}]   # paréntesis sin cerrar
        mapa = build_map(self.fuente, roto, "p", tools={})
        self.assertFalse(mapa["complete"])
        [hueco] = [g for g in mapa["coverage_gaps"] if g.startswith("regex_extractor(entrypoints)")]
        self.assertIn("patrón inválido", hueco)
        self.assertEqual(mapa["entrypoints"], [])

    def test_lo_que_el_contrato_no_admite_se_declara(self):
        casos = {
            "surface": ({"surface": "pantallas", "member_patterns": [r"\.php$"], "pattern": r"(?P<path>x)"}, "surface"),
            "sin grupos": ({"surface": "jobs", "member_patterns": [r"\.php$"], "pattern": r"command\('x'\)"}, "grupo nombrado"),
            "grupo ajeno": ({"surface": "jobs", "member_patterns": [r"\.php$"], "pattern": r"(?P<name>x)(?P<cron>y)"}, "cron"),
            "transform": ({"surface": "jobs", "member_patterns": [r"\.php$"], "pattern": r"(?P<name>x)",
                           "transforms": {"name": "capitalizar"}}, "transform desconocida"),
            "flag": ({"surface": "jobs", "member_patterns": [r"\.php$"], "pattern": r"(?P<name>x)", "flags": "z"}, "flag desconocida"),
            "sin miembros": ({"surface": "jobs", "member_patterns": [r"\.cobol$"], "pattern": r"(?P<name>x)"}, "ningún miembro"),
            "sin member_patterns": ({"surface": "jobs", "pattern": r"(?P<name>x)"}, "member_patterns"),
            "default fuera de campo": ({"surface": "entrypoints", "member_patterns": [r"\.php$"], "pattern": r"(?P<path>x)",
                                        "defaults": {"verbo": "GET"}}, "verbo"),
        }
        for nombre, (spec, esperado) in casos.items():
            mapa = build_map(self.fuente, [dict(spec, mechanism="regex_extractor")], "p", tools={})
            huecos = [g for g in mapa["coverage_gaps"] if g.startswith("regex_extractor")]
            self.assertEqual(len(huecos), 1, (nombre, mapa["coverage_gaps"]))
            self.assertIn(esperado, huecos[0], nombre)
            self.assertFalse(any("el extractor del perfil falló" in g for g in mapa["coverage_gaps"]), nombre)

    def test_un_valor_fuera_del_vocabulario_no_entra_y_se_dice(self):
        spec = [{"mechanism": "regex_extractor", "surface": "external_dependencies", "member_patterns": [r"^\.env$"],
                 "pattern": r"^MAIL_HOST=(?P<host>\S+)$", "flags": "m", "defaults": {"kind": "correo"}}]
        mapa = build_map(self.fuente, spec, "p", tools={})
        self.assertEqual(mapa["external_dependencies"], [])
        [hueco] = [g for g in mapa["coverage_gaps"] if g.startswith("regex_extractor(external_dependencies)")]
        self.assertIn("vocabulario", hueco)
        self.assertIn(".env:2", hueco)

    def test_sin_matches_es_nota_no_hueco(self):
        spec = [{"mechanism": "regex_extractor", "surface": "jobs", "member_patterns": [r"urls\.py$"],
                 "pattern": r"cron\('(?P<name>[^']+)'\)"}]
        mapa = build_map(self.fuente, spec, "p", tools={})
        self.assertFalse(any(g.startswith("regex_extractor") for g in mapa["coverage_gaps"]))
        self.assertTrue(any("no casó en ninguno" in n for n in mapa["notes"]), mapa["notes"])


if __name__ == "__main__":
    unittest.main()
