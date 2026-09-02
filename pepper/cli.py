"""Línea de comandos: `pepper correlate | package | export | detect | validate | isolate | demo`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

from pepper import REPO_ROOT, __version__


def _invocation() -> str:
    """Cómo se invocó pepper, para que los "siguientes pasos" impresos funcionen tal cual."""
    return "pepper" if Path(sys.argv[0]).name == "pepper" else "python3 -m pepper"


def _cmd_correlate(args: argparse.Namespace) -> int:
    from pepper.correlate import run

    summary = run(args.evidence, args.out, args.profile, args.tolerance_ms)
    print(f"correlate · {summary['session_id']} · perfil: {summary['profile'] or 'ninguno'}")
    print(
        f"  {summary['raw_lines']} líneas crudas → {summary['parsed']} eventos parseados "
        f"({summary['unparsed']} sin parsear) → {summary['kept']} conservados, {summary['dropped']} descartados"
    )
    print(f"  {summary['traces']} peticiones correlacionadas · {summary['unassigned']} eventos sin asignar")
    print(f"  salida: {args.out}")
    return 0


def _cmd_package(args: argparse.Namespace) -> int:
    from pepper.package import assemble

    summary = assemble(args.correlated, args.out, args.legacy)
    print(f"package · {summary['session_id']} · {summary['files']} archivos")
    print(f"  evidencia: {summary['events']} eventos, {summary['traces']} peticiones")
    print(f"  legacy: {', '.join(summary['legacy']) if summary['legacy'] else 'sin artefactos (usa --legacy)'}")
    print(f"  paquete: {args.out}")
    print()
    print("Siguiente paso — Discover, con el agente que prefieras:")
    print(f"  cd {args.out} && claude    # o codex")
    return 0


def _print_report(report, label: str) -> None:
    for warning in report.warnings:
        print(f"  aviso: {warning}")
    if report.errors:
        print(f"export · RECHAZADO · {len(report.errors)} error(es); {label}")
        for error in report.errors:
            print(f"  ✗ {error}")


def _cmd_export(args: argparse.Namespace) -> int:
    from pepper.export import check, publish

    if args.check:
        report = check(args.package)
        _print_report(report, "corrige y vuelve a comprobar")
        if report.errors:
            return 1
        stats = report.stats
        print(
            f"export · válido · {stats.get('steps', 0)} pasos, {stats.get('candidate_rules', 0)} reglas candidatas, "
            f"{stats.get('contradictions', 0)} contradicciones, {stats.get('unknowns', 0)} desconocidos, "
            f"{stats.get('evidence', 0)} evidencias (sin publicar)"
        )
        return 0

    if args.out is None:
        print("pepper export: indica --out <dir> (o usa --check para solo validar)", file=sys.stderr)
        return 2
    report = publish(args.package, args.out)
    _print_report(report, "no se publicó nada")
    if report.errors:
        print(f"  detalle: {args.package / 'output' / 'validation.md'}")
        return 1
    stats = report.stats
    print(
        f"export · publicado · {stats.get('steps', 0)} pasos, {stats.get('candidate_rules', 0)} reglas candidatas, "
        f"{stats.get('contradictions', 0)} contradicciones, {stats.get('unknowns', 0)} desconocidos, "
        f"{stats.get('evidence', 0)} evidencias"
    )
    print(f"  salida: {args.out}")
    return 0


def _cmd_detect(args: argparse.Namespace) -> int:
    from pepper.detect import detect

    results = detect(args.artifacts)
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0
    applicable = [r for r in results if r["applicable"]]
    print(f"detect · {args.artifacts} · {len(results)} perfil(es) evaluados")
    for result in results:
        mark = "✓" if result["applicable"] else "·"
        print(f"  {mark} {result['profile_id']} ({result['status']}) — puntaje {result['score']:g} / mínimo {result['min_score']:g}")
        for match in result["matches"]:
            print(f"      + {match['type']} {match['pattern']!r} → {match['hit']}  (+{match['weight']:g})")
    print()
    if not applicable:
        print("Ningún perfil aplica: escalón 2 (observar con colectores genéricos) o 3 (inspección + borrador de perfil).")
    else:
        best = applicable[0]
        if best["status"] == "validated":
            print(f"Perfil aplicable y validado: {best['profile_id']} → escalón 1.")
        else:
            print(f"Perfil aplicable pero en borrador: {best['profile_id']} → úsalo solo con supervisión humana.")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    from pepper.validate import validate_file

    failed = 0
    for path in args.files:
        try:
            errors = validate_file(path, args.schema)
        except ImportError:
            print("pepper validate: falta jsonschema (pip install jsonschema)", file=sys.stderr)
            return 2
        if errors:
            failed += 1
            print(f"✗ {path}")
            for error in errors:
                print(f"    {error}")
        else:
            print(f"✓ {path}")
    return 1 if failed else 0


def _cmd_isolate(args: argparse.Namespace) -> int:
    from pepper.isolate import check_live, check_static, render, resolve_compose

    hosts = [h.strip() for h in (args.hosts or "").split(",") if h.strip()]
    try:
        compose = resolve_compose(args.compose)
    except (RuntimeError, ValueError) as error:
        print(f"pepper isolate: {error}", file=sys.stderr)
        return 2
    report = check_static(compose, hosts, args.ingress)
    title = f"Aislamiento — {args.compose}"
    if args.live:
        live = check_live(args.compose, hosts, args.ingress)
        report.findings.extend(live.findings)
        title += " (compose + contenedores)"

    for finding in report.errors:
        print(f"  ✗ {finding.check}" + (f"\n      {finding.detail}" if finding.detail else ""))
    for finding in report.warnings:
        print(f"  ! {finding.check}" + (f"\n      {finding.detail}" if finding.detail else ""))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(render(report, title) + "\n", encoding="utf-8")

    checked = len([f for f in report.findings if f.level == "ok"])
    if report.isolated:
        print(f"isolate · AISLADO · {checked} comprobaciones en verde, {len(report.warnings)} aviso(s)")
        print("  ningún contenedor del legacy puede alcanzar nada fuera de su red")
    else:
        print(f"isolate · NO AISLADO · {len(report.errors)} fuga(s)")
        print("  el entorno puede alcanzar producción: no lo levantes ni observes hasta corregir")
    if args.out:
        print(f"  reporte: {args.out}")
    return 0 if report.isolated else 1


def _cmd_proxy(args: argparse.Namespace) -> int:
    from pepper import proxy

    return proxy.run(args)


def _cmd_demo(args: argparse.Namespace) -> int:
    from pepper.correlate import run
    from pepper.package import assemble

    fixture = REPO_ROOT / "examples" / "legacy-demo"
    correlated = args.out / "correlated"
    package = args.out / "package"

    summary = run(fixture / "raw-evidence", correlated)
    print(
        f"correlate · {summary['raw_lines']} líneas crudas → {summary['kept']} eventos relevantes "
        f"en {summary['traces']} peticiones ({summary['dropped']} descartadas como ruido)"
    )
    assemble(correlated, package, fixture / "artifacts")
    print(f"package   · {package}")
    print()
    print("Ahora corre tu agente dentro del paquete:")
    print(f"  cd {package} && claude    # o codex")
    print()
    print("Y cuando termine, valida y publica su resultado:")
    print(f"  {_invocation()} export {package} --out {args.out / 'export'}")
    print()
    print(f"Clave de respuestas: {fixture / 'expected' / 'notes.md'}")
    return 0


COMMANDS: Dict[str, Callable[[argparse.Namespace], int]] = {
    "correlate": _cmd_correlate,
    "package": _cmd_package,
    "export": _cmd_export,
    "detect": _cmd_detect,
    "validate": _cmd_validate,
    "isolate": _cmd_isolate,
    "proxy": _cmd_proxy,
    "demo": _cmd_demo,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pepper",
        description="PEPPER — descubrimiento dinámico de sistemas legacy.",
    )
    parser.add_argument("--version", action="version", version=f"pepper {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="comando")
    commands.required = True

    correlate = commands.add_parser("correlate", help="normaliza, reduce y correlaciona evidencia cruda")
    correlate.add_argument("evidence", type=Path, help="directorio con session.json y los archivos de sus colectores")
    correlate.add_argument("--out", type=Path, required=True, help="directorio de salida")
    correlate.add_argument("--profile", help="id o ruta del perfil (por defecto, environment.profile_id de session.json)")
    correlate.add_argument("--tolerance-ms", type=int, default=500, help="holgura al asignar eventos a una petición por ventana temporal (default 500)")

    package = commands.add_parser("package", help="arma el paquete controlado para el agente")
    package.add_argument("correlated", type=Path, help="salida de `pepper correlate`")
    package.add_argument("--legacy", type=Path, help="directorio con los artefactos del legacy (source/, configuration/, docs/, ...)")
    package.add_argument("--out", type=Path, required=True, help="directorio del paquete (debe no existir o estar vacío)")

    export = commands.add_parser("export", help="valida la salida del agente y la publica")
    export.add_argument("package", type=Path, help="paquete controlado con output/runtime-discovery.json")
    export.add_argument("--out", type=Path, help="directorio de publicación")
    export.add_argument("--check", action="store_true", help="solo validar (deja output/validation.md), sin publicar")

    detect = commands.add_parser("detect", help="evalúa qué perfil aplica a un directorio de artefactos")
    detect.add_argument("artifacts", type=Path, help="directorio con los artefactos del legacy")
    detect.add_argument("--json", action="store_true", help="salida en JSON")

    validate = commands.add_parser("validate", help="valida archivos contra los contratos de schemas/")
    validate.add_argument("files", type=Path, nargs="+", help="profile.json, parsers/*.json, session.json, environment.json, flow.json, events.jsonl, runtime-discovery.json")
    validate.add_argument("--schema", choices=("event", "environment", "flow", "parser", "profile", "runtime-discovery", "session"), help="fuerza el schema (si el nombre del archivo no lo delata)")

    isolate = commands.add_parser("isolate", help="verifica que un entorno rehidratado no pueda alcanzar nada externo")
    isolate.add_argument("compose", type=Path, help="docker-compose.yml del entorno rehidratado")
    isolate.add_argument("--hosts", help="hosts externos que el artefacto invoca, separados por coma: cada uno debe resolver al stub")
    isolate.add_argument("--live", action="store_true", help="además, verifica los contenedores en ejecución (según Docker, no según el archivo)")
    isolate.add_argument("--ingress", default="ingress", help="servicio que publica el puerto al host: el único con salida permitida (default: ingress)")
    isolate.add_argument("--out", type=Path, help="escribe el reporte legible en este archivo")

    proxy_cmd = commands.add_parser("proxy", help="proxy HTTP del ingress: inyecta correlation_id y emite http.jsonl")
    from pepper.proxy import _host_port
    proxy_cmd.add_argument("--listen", type=_host_port, default=("0.0.0.0", 8080), help="host:puerto donde escuchar (default 0.0.0.0:8080)")
    proxy_cmd.add_argument("--upstream", type=_host_port, required=True, help="host:puerto del app rehidratado")
    proxy_cmd.add_argument("--out", default=None, help="además de stdout, escribir el http.jsonl a este archivo")
    proxy_cmd.add_argument("--timeout", type=float, default=120.0, help="segundos de espera por respuesta del app (default 120)")

    demo = commands.add_parser("demo", help="correlate + package sobre examples/legacy-demo")
    demo.add_argument("--out", type=Path, default=Path("pepper-out/legacy-demo"), help="directorio de trabajo (default pepper-out/legacy-demo)")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except (FileNotFoundError, ValueError, FileExistsError) as error:
        print(f"pepper {args.command}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
