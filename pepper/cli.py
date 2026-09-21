"""Línea de comandos: `pepper detect | map | validate | isolate | proxy | collect | correlate | package | export | demo`."""

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

    summary = assemble(
        args.correlated,
        args.out,
        args.legacy,
        data_mode=args.data_mode,
        allow_sensitive=args.allow_sensitive,
        acknowledge_unscanned=args.acknowledge_unscanned,
        manifest_out=args.manifest_out,
        system_map=args.map,
        previous=args.previous,
    )
    print(f"package · {summary['session_id']} · {summary['files']} archivos")
    print(f"  evidencia: {summary['events']} eventos, {summary['traces']} peticiones")
    print(f"  legacy: {', '.join(summary['legacy']) if summary['legacy'] else 'sin artefactos (usa --legacy)'}")
    print(f"  mapa: {summary['map'] or 'sin mapa (usa --map docs/pepper/system-map.json): el agente solo verá la ejecución'}")
    print(f"  discovery anterior: {summary['previous'] or 'ninguno (primer documento del sistema)'}")
    print(f"  datos: modo {summary['data_mode']} · {summary['sensitive_findings']} hallazgo(s) sensible(s) · "
          f"{summary['unscanned_files']} archivo(s) no inspeccionado(s)")
    print(f"  paquete: {args.out}")
    print(f"  manifest externo: {summary['external_manifest']} (no lo metas al paquete)")
    if summary.get("redacted_notes"):
        print(f"  ⚠ redacté credenciales en: {', '.join(summary['redacted_notes'])} (estaban en claro; el original en legacy/ no se tocó)")
    print()
    print("Siguiente paso — Discover, con el agente que prefieras:")
    if summary["data_mode"] == "remote":
        print(f"  cd {args.out} && claude    # o codex")
    else:
        print(f"  paquete LOCAL: usa únicamente un agente/modelo que no envíe contenido fuera de la máquina")
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
        report = check(args.package, args.manifest)
        _print_report(report, "corrige y vuelve a comprobar")
        if report.errors:
            return 1
        print(f"export · válido · {_stats_line(report.stats)} (sin publicar)")
        return 0

    if args.out is None:
        print("pepper export: indica --out <dir> (o usa --check para solo validar)", file=sys.stderr)
        return 2
    report = publish(args.package, args.out, args.manifest, system_doc_dir=args.system_doc)
    _print_report(report, "no se publicó nada")
    if report.errors:
        print(f"  detalle: {args.package / 'output' / 'validation.md'}")
        return 1
    print(f"export · publicado · {_stats_line(report.stats)}")
    print(f"  salida: {args.out}")
    if args.system_doc:
        print(f"  documento del sistema: {args.system_doc / 'funcional.md'} (y funcional.json)")
    return 0


def _stats_line(stats: Dict[str, int]) -> str:
    return (f"{stats.get('actors', 0)} actores, {stats.get('journeys', 0)} recorridos, {stats.get('rules', 0)} reglas, "
            f"{stats.get('states', 0)} ciclos de estado, {stats.get('automation', 0)} automáticos, "
            f"{stats.get('integrations', 0)} integraciones, {stats.get('unknowns', 0)} desconocidos, "
            f"{stats.get('sources', 0)} fuentes")


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
        for missing in result.get("missing_required") or []:
            print(f"      ✗ falta lo que define al stack: {missing} — el perfil NO aplica")
        for match in result["matches"]:
            print(f"      + {match['type']} {match['pattern']!r} → {match['hit']}  (+{match['weight']:g})")
    print()
    if not applicable:
        from pepper.detect import inventory

        seen = inventory(args.artifacts)
        if seen["files"] == 0:
            print(f"No hay artefactos en {args.artifacts}: nada que detectar. Copia ahí lo que tengas del legacy (WAR/JAR, dist, respaldo, configuración) y repite.")
        else:
            dominant = ", ".join(f"{ext} ×{n}" for ext, n in seen["dominant"])
            print(f"Vi {seen['files']} archivo(s); predominan: {dominant}.")
            print("Ningún perfil cubre este stack: escalón 2 (observar con colectores genéricos) o 3 (inspección + borrador de perfil).")
    else:
        best = applicable[0]
        if best["status"] == "validated":
            print(f"Perfil aplicable y validado: {best['profile_id']} → escalón 1.")
        else:
            print(f"Perfil aplicable, en borrador: {best['profile_id']} → corre igual; environment.json y funcional.md lo declaran como borrador.")
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
        compose, resolved = resolve_compose(args.compose)
    except (RuntimeError, ValueError) as error:
        print(f"pepper isolate: {error}", file=sys.stderr)
        return 2
    report = check_static(compose, hosts, args.ingress, resolved=resolved,
                          compose_dir=args.compose.resolve().parent)
    title = f"Aislamiento — {args.compose}"
    if args.live:
        live = check_live(args.compose, hosts, args.ingress)
        report.findings.extend(live.findings)
        title += " (compose + contenedores)"

    for finding in report.errors:
        print(f"  ✗ {finding.check}" + (f"\n      {finding.detail}" if finding.detail else ""))
    for finding in report.unknowns:
        print(f"  ? {finding.check}" + (f"\n      {finding.detail}" if finding.detail else ""))
    for finding in report.warnings:
        print(f"  ! {finding.check}" + (f"\n      {finding.detail}" if finding.detail else ""))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(render(report, title) + "\n", encoding="utf-8")

    checked = len([f for f in report.findings if f.level == "ok"])
    if report.verdict == "VERIFIED":
        print(f"isolate · AISLADO (verificado) · {checked} comprobaciones en verde, {len(report.warnings)} aviso(s)")
        print("  ningún contenedor del legacy puede alcanzar nada fuera de su red")
    elif report.verdict == "FAILED":
        print(f"isolate · NO AISLADO · {len(report.errors)} fuga(s)")
        print("  el entorno puede alcanzar producción: no lo levantes ni observes hasta corregir")
    else:
        print(f"isolate · NO VERIFICADO · {len(report.unknowns)} comprobación(es) pendiente(s)")
        print("  lo no verificado bloquea igual que una fuga (fail-closed): resuélvelo y repite")
    if args.out:
        print(f"  reporte: {args.out}")
    return {"VERIFIED": 0, "FAILED": 1, "UNKNOWN": 2}[report.verdict]


def _cmd_proxy(args: argparse.Namespace) -> int:
    from pepper import proxy

    return proxy.run(args)


def _cmd_collect(args: argparse.Namespace) -> int:
    from datetime import datetime

    from pepper.isolate import resolve_compose
    from pepper.observe import collect

    def _moment(raw: str, label: str) -> datetime:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError(f"--{label} sin zona horaria ({raw!r}); usa ISO con offset, p. ej. 2026-09-02T10:00:00-06:00")
        return parsed

    try:
        compose, _ = resolve_compose(args.compose)
        summary = collect(compose, args.session_id, _moment(args.start, "start"), _moment(args.end, "end"),
                          args.out, margin_s=args.margin, ingress=args.ingress)
    except (RuntimeError, ValueError, FileExistsError) as error:
        print(f"pepper collect: {error}", file=sys.stderr)
        return 2
    captured = [item for item in summary if "file" in item]
    for item in summary:
        if "warning" in item:
            print(f"  ! {item['warning']}")
    for item in captured:
        print(f"  ✓ {item['service']:<12} → {item['file']} ({item['lines']} líneas)")
    for item in summary:
        if "skipped" in item:
            print(f"  – {item['service']:<12} {item['skipped']}")
    print(f"collect · {len(captured)} archivo(s) en {args.out / args.session_id}")
    print("  faltan: session.json y las fuentes del perfil (archivos dentro de contenedores) — los declara el observador")
    return 0 if captured else 1


def _cmd_rehydrate(args: argparse.Namespace) -> int:
    from pepper.profiles import load_profile
    from pepper.rehydrate import Blocked, bring_up, make_plan, render, write_environment

    profile = load_profile(args.profile)
    notes = args.notes or (args.legacy / "NOTAS.md")
    try:
        plan = make_plan(args.legacy, profile, host_port=args.port, notes_path=notes)
        written = render(plan, profile, args.out)
    except Blocked as error:
        print(f"rehydrate · BLOCKED · {error}")
        args.docs.mkdir(parents=True, exist_ok=True)
        (args.docs / "environment.json").write_text(json.dumps({
            "schema_version": "0.1.0", "status": "BLOCKED", "profile_id": profile.id, "support_tier": 1,
            "components": [], "validations": [], "missing_evidence": [{"missing": str(error)}], "notes": ""},
            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 1
    print(f"rehydrate · plan · {plan.artifact.name} + {plan.dump.name} · perfil de configuración '{plan.spring_profile}'")
    print(f"  base: {plan.db_engine} {plan.db_version} ({plan.db_image}) en {plan.db_ip}:{plan.db_port}/{plan.db_name} (usuario {plan.db_user}); restaura con {plan.db_tool_image}")
    print(f"  app: {plan.server} → {plan.server_image} en {plan.app_ip}; ingress en http://127.0.0.1:{plan.host_port}")
    print(f"  externos al stub ({plan.stub_ip}): {', '.join(plan.external_hosts) or 'ninguno'}; puertos {plan.stub_ports}")
    if plan.external_by_ip:
        print(f"  por IP directa (sin registro): {', '.join(plan.external_by_ip)}")
    for d in plan.deviations:
        print(f"  ! {d}")
    print(f"  escrito: {', '.join(str(w.relative_to(args.out)) for w in written)} en {args.out}")
    if not args.up:
        print(f"  siguiente: {_invocation()} rehydrate {args.legacy} --profile {profile.id} --up   (o revisa el compose primero)")
        return 0
    try:
        status, validations, missing = bring_up(plan, profile, args.out, wait_s=args.wait)
    except KeyboardInterrupt:
        status, validations, missing = "FAILED", [{"check": "levantar", "result": "fail", "detail": "interrumpido por el humano"}], []
    except Exception as error:  # noqa: BLE001 — un traceback aquí dejaba environment.json viejo con READY
        status, validations, missing = "FAILED", [{"check": "levantar", "result": "fail", "detail": f"{type(error).__name__}: {str(error)[:300]}"}], []
    env_path, val_path = write_environment(plan, profile, status, validations, missing, args.docs, args.out)
    for v in validations:
        print(f"  {'✓' if v['result'] == 'pass' else '✗'} {v['check']}: {v.get('detail', '')[:120]}")
    print(f"rehydrate · {status} · {env_path} · {val_path}")
    return 0 if status in ("READY", "PARTIAL") else 1


def _cmd_explore(args: argparse.Namespace) -> int:
    import json as _json
    import re as _re
    import time as _time
    from datetime import datetime

    from pepper.explore import Explorer, operator_note, write_session
    from pepper.isolate import check_live, check_static, resolve_compose
    from pepper.observe import collect
    from pepper.profiles import load_profile

    from pepper.explore import CredentialsError, config_problems, outcome

    if not _re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", args.session) or ".." in args.session:
        print(f"pepper explore: --session {args.session!r} no es un identificador válido (letras, dígitos, _ . -)", file=sys.stderr)
        return 2
    config = _json.loads(args.config.read_text(encoding="utf-8"))
    profile = load_profile(args.profile) if args.profile else None
    probe = ((profile.data.get("rehydrate") or {}).get("database") or {}).get("probe") or {} if profile else {}
    if probe.get("client") and (config.get("credentials") or {}).get("sql"):
        # el cliente de la base desechable es del perfil, no del núcleo (psql, mysql…)
        creds = config.setdefault("credentials", {})
        creds.setdefault("client", probe["client"])
        creds.setdefault("client_env", probe.get("env") or {})
        creds.setdefault("quiet_prefix", probe.get("quiet_prefix", ""))
        env_file = args.compose.resolve().parent / ".env"
        if env_file.is_file() and "db_password" not in creds:
            m = _re.search(r'^DB_PASSWORD="(.*)"$', env_file.read_text(encoding="utf-8"), _re.M)
            if m:
                creds["db_password"] = m.group(1).replace("$$", "$").replace('\\"', '"').replace("\\\\", "\\")
    problems = config_problems(config)
    if problems:
        print(f"pepper explore: {args.config} incompleto — " + "; ".join(problems), file=sys.stderr)
        return 2
    system_map = _json.loads(args.map.read_text(encoding="utf-8")) if args.map else {"entrypoints": []}
    if not args.plan and not any(e.get("method", "GET") in ("GET", "") for e in system_map.get("entrypoints", [])):
        print("pepper explore: el mapa no trae rutas GET que recorrer; pasa --map docs/pepper/system-map.json", file=sys.stderr)
        return 2
    out_dir = args.out / args.session
    if out_dir.exists() and any(out_dir.iterdir()):
        print(f"pepper explore: evidence/{args.session} ya existe; usa otro --session", file=sys.stderr)
        return 2

    # Fail-closed: el explorador solo corre sobre un entorno AISLADO verificado en vivo.
    hosts = [h.strip() for h in (args.hosts or "").split(",") if h.strip()]
    try:
        compose, resolved = resolve_compose(args.compose)
    except (RuntimeError, ValueError) as error:
        print(f"pepper explore: {error}", file=sys.stderr)
        return 2
    report = check_static(compose, hosts, args.ingress, resolved=resolved, compose_dir=args.compose.resolve().parent)
    report.findings.extend(check_live(args.compose, hosts, args.ingress).findings)
    if report.verdict != "VERIFIED":
        print(f"pepper explore: el entorno no está AISLADO (verificado): {report.verdict}. No se explora.", file=sys.stderr)
        for finding in report.errors + report.unknowns:
            print(f"  ✗ {finding.check}", file=sys.stderr)
        return 1
    print(f"explore · aislamiento verificado en vivo ({len([f for f in report.findings if f.level == 'ok'])} comprobaciones)")

    plan = _json.loads(args.plan.read_text(encoding="utf-8")) if args.plan else None
    kind = f"plan ({args.plan.name})" if plan else "recorrido automático por rol y pantalla"
    actions: List[Dict] = []
    summary: Dict = {}
    started = datetime.now().astimezone()
    try:
        with Explorer(config, system_map, out_dir, headless=not args.headed) as explorer:
            # Las credenciales se fijan ANTES de abrir la ventana: el UPDATE con la clave de
            # prueba no debe caer dentro de lo que se captura.
            explorer.grant_credentials(args.compose)
            started = datetime.now().astimezone()
            if args.budget:
                explorer.deadline = _time.time() + args.budget
            try:
                if plan:
                    summary = explorer.run_plan(plan, docker_compose=args.compose)
                else:
                    summary = explorer.walk(docker_compose=args.compose, submit=not args.no_submit)
            finally:
                actions = [a.record() for a in explorer.actions]
    except CredentialsError as error:
        # Sin credenciales no se exploró nada: se dice y se para. Seguir a Correlate con
        # una sesión vacía escondería el fallo detrás de un "Siguiente".
        print(f"pepper explore: {error}", file=sys.stderr)
        if "laywright" in str(error):
            print("  instala el navegador: pip install playwright && python3 -m playwright install chromium", file=sys.stderr)
        else:
            print(f"  lo registrado quedó en {out_dir}/explore.jsonl; corrige {args.config} (credentials.setup_sql / sql) y repite", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        summary = dict(summary or {}, interrupted="interrumpido por el humano")
    except Exception as error:  # noqa: BLE001 — sin esto un TimeoutError del navegador dejaba la sesión sin session.json
        text = str(error)
        if "Executable doesn't exist" in text or "playwright install" in text:
            print("pepper explore: falta el navegador de Playwright: python3 -m playwright install chromium", file=sys.stderr)
            return 1
        summary = dict(summary or {}, error=f"{type(error).__name__}: {text[:200]}")
    _time.sleep(args.settle)  # que el sistema termine lo que la última acción disparó
    ended = datetime.now().astimezone()

    print(f"explore · {len(actions)} acciones · {summary}")
    # Captura: lo que el ingress y los contenedores vieron en la ventana. `docker logs`
    # solo devuelve lo ya emitido: se espera el margen antes de pedirlo.
    _time.sleep(args.margin)
    captured = collect(compose, args.session, started, ended, args.out, margin_s=args.margin, ingress=args.ingress)
    collectors = [{"source": "explorer", "kind": "generic", "file": "explore.jsonl",
                   "note": "explore.jsonl del explorador de PEPPER: una línea por acción del navegador automático (rol, ruta, botón, resultado, mensajes, captura)."}]
    profile = load_profile(args.profile) if args.profile else None
    for item in captured:
        if "file" not in item:
            if "warning" in item:
                print(f"  ! {item['warning']}")
            continue
        rel = item["file"].split("/", 1)[1] if "/" in item["file"] else item["file"]
        print(f"  ✓ {item['service']:<10} → {rel} ({item['lines']} líneas)")
        if rel == "http.jsonl":
            collectors.append({"source": "http-proxy", "kind": "generic", "file": rel,
                               "note": "stdout del ingress (pepper/proxy.py): una línea JSON por petición y por respuesta; origen del correlation_id; incluye direction=blocked del navegador."})
            continue
        source = None
        for collector in (profile.data.get("collectors", []) if profile else []):
            if rel.split("/")[-1] in (collector.get("location") or "") or collector.get("source") == item["service"]:
                source = collector["source"]
                break
        if source:
            collectors.append({"source": source, "kind": "profile", "file": rel,
                               "note": f"docker logs --timestamps del servicio {item['service']} (prefijo RFC3339 UTC de Docker)."})
        else:
            print(f"  – {rel}: sin parser en el perfil; se conserva pero no se correlaciona")
    note = operator_note(actions, summary, kind)
    write_session(out_dir, args.session, args.flow_name or kind, started, ended,
                  profile.id if profile else None, note, collectors)
    print(f"  session.json: {out_dir / 'session.json'}")
    print(f"  nota: {note[:300]}")
    print(f"  capturas: {out_dir / 'screens'}")
    # Un exit 0 con cero trabajo escondía el fallo detrás de un "Siguiente" (auditoría 2026-09-11).
    code, why = outcome(summary, actions, [c["file"] for c in collectors])
    if code:
        print(f"pepper explore: {why}", file=sys.stderr)
        print(f"  la sesión quedó escrita en {out_dir} para que se vea qué pasó; usa otro --session al repetir", file=sys.stderr)
        return code
    print()
    print(f"Siguiente: {_invocation()} correlate {out_dir} --out pepper-out/{args.session}/correlated")
    return 0


def _cmd_map(args: argparse.Namespace) -> int:
    import json as _json

    from pepper.inspect import build_map, coverage, render_map
    from pepper.profiles import load_profile

    extractors: List[Dict] = []
    profile_id = None
    if args.profile:
        profile = load_profile(args.profile)
        profile_id = profile.id
        spec_path = profile.dir / "extractors.json"
        if not spec_path.is_file():
            print(f"pepper map: el perfil {profile.id} no declara extractors.json", file=sys.stderr)
            return 2
        extractors = _json.loads(spec_path.read_text(encoding="utf-8")).get("extractors", [])
    else:
        print("pepper map: sin --profile no hay extractores; el mapa saldría vacío", file=sys.stderr)
        return 2

    try:
        system_map = build_map(args.artifact, extractors, profile_id, dump=args.dump)
    except FileNotFoundError as error:
        print(f"pepper map: {error}", file=sys.stderr)
        return 2

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(_json.dumps(system_map, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    map_dir = args.out.parent / "map"
    map_dir.mkdir(exist_ok=True)
    for name, text in render_map(system_map).items():
        (map_dir / name).write_text(text, encoding="utf-8")

    ep = system_map["entrypoints"]
    routes = [e for e in ep if e["kind"] in ("http_route", "rest_endpoint")]
    tables = [d for d in system_map["data_stores"] if d["kind"] == "table"]
    rules_in_db = [d for d in system_map["data_stores"] if d["kind"] in ("trigger", "function")]
    print(f"map · {args.artifact.name} · perfil {profile_id}")
    print(f"  entradas HTTP: {len(routes)} ({sum(1 for e in ep if e['kind']=='rest_endpoint')} REST) · "
          f"jobs: {len(system_map['jobs'])} · hosts externos: {len(system_map['external_dependencies'])}")
    print(f"  pantallas: {len(system_map['screens'])} · clases propias: {len(system_map['classes'])} · "
          f"etiquetas: {system_map.get('labels', 0)}")
    print(f"  tablas: {len(tables)} · catálogos completos: {len(system_map['catalogs'])} · "
          f"distribuciones: {len(system_map['distributions'])} · triggers+funciones: {len(rules_in_db)}")
    if system_map["complete"]:
        print("  mapa COMPLETO")
    else:
        print(f"  mapa INCOMPLETO ({len(system_map['coverage_gaps'])} hueco(s) declarado(s) — lo que no se pudo enumerar, no un cero):")
        for gap in system_map["coverage_gaps"]:
            print(f"    ? {gap}")

    if args.evidence:
        observed = []
        http = args.evidence / "http.jsonl"
        if http.is_file():
            for line in http.read_text(encoding="utf-8").splitlines():
                try:
                    observed.append(_json.loads(line).get("path", ""))
                except ValueError:
                    pass
        cov = coverage(system_map, observed, evidence_dir=args.evidence)
        jobs_txt = (f"{cov['jobs_observed']}/{cov['jobs_total']} jobs" if cov["jobs_measurable"]
                    else f"jobs: {cov['jobs_total']} (sin firma declarada: confírmalos a mano)")
        print(f"  cobertura: {cov['routes_observed']}/{cov['routes_total']} rutas · "
              f"{cov['dependencies_observed']}/{cov['dependencies_total']} dependencias confirmadas · {jobs_txt}")
        if cov["dependencies_confirmed"]:
            print(f"    dependencias vistas en ejecución: {', '.join(cov['dependencies_confirmed'])}")
        if cov["not_observed"]:
            print(f"    sin observar aún: {len(cov['not_observed'])} rutas (ver {args.out})")
    print(f"  salida: {args.out} + {map_dir}/ (surface, db, catalogs, screens, code)")
    return 0


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
    # El fixture trae credenciales de juguete a propósito; la excepción es explícita y
    # queda en el manifest, igual que con un legacy real (la bandera synthetic no exime).
    package_summary = assemble(correlated, package, fixture / "artifacts", allow_sensitive=True)
    print(f"package   · {package}")
    print("            (fixture sintético con credenciales de juguete: --allow-sensitive implícito, registrado en el manifest)")
    print()
    print("Ahora corre tu agente dentro del paquete:")
    print(f"  cd {package} && claude    # o codex")
    print()
    print("Y cuando termine, valida y publica su resultado:")
    print(f"  {_invocation()} export {package} --manifest {package_summary['external_manifest']} "
          f"--out {args.out / 'export'} --system-doc {args.out / 'docs'}")
    print()
    print(f"Clave de respuestas: {fixture / 'expected' / 'notes.md'}")
    return 0


COMMANDS: Dict[str, Callable[[argparse.Namespace], int]] = {
    "correlate": _cmd_correlate,
    "package": _cmd_package,
    "export": _cmd_export,
    "detect": _cmd_detect,
    "map": _cmd_map,
    "rehydrate": _cmd_rehydrate,
    "explore": _cmd_explore,
    "validate": _cmd_validate,
    "isolate": _cmd_isolate,
    "proxy": _cmd_proxy,
    "collect": _cmd_collect,
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
    package.add_argument("--data-mode", choices=("remote", "local"), default="remote",
                         help="frontera autorizada: remote bloquea datos sensibles/no inspeccionados; local prohíbe agentes remotos")
    package.add_argument("--allow-sensitive", action="store_true",
                         help="autoriza explícitamente ubicaciones sensibles detectadas en modo remote")
    package.add_argument("--acknowledge-unscanned", action="store_true",
                         help="autoriza explícitamente binarios/archivos grandes no inspeccionables en modo remote")
    package.add_argument("--manifest-out", type=Path,
                         help="manifest externo; default <paquete>.evidence-manifest.json, siempre fuera del paquete")
    package.add_argument("--map", type=Path,
                         help="system-map.json de `pepper map` (se copia con su carpeta map/ legible); sin él el agente solo ve la ejecución")
    package.add_argument("--previous", type=Path,
                         help="funcional.json publicado por un discovery anterior: el nuevo lo extiende en vez de empezar de cero")

    export = commands.add_parser("export", help="valida la salida del agente y la publica")
    export.add_argument("package", type=Path, help="paquete controlado con output/funcional.json y funcional.md")
    export.add_argument("--out", type=Path, help="directorio de publicación de esta sesión")
    export.add_argument("--system-doc", type=Path,
                        help="directorio del documento del SISTEMA (p. ej. docs/pepper): ahí queda funcional.md/json acumulado")
    export.add_argument("--check", action="store_true", help="solo validar (deja output/validation.md), sin publicar")
    export.add_argument("--manifest", type=Path, required=True,
                        help="evidence-manifest.json conservado FUERA del paquete; obligatorio como raíz de confianza")

    detect = commands.add_parser("detect", help="evalúa qué perfil aplica a un directorio de artefactos")
    detect.add_argument("artifacts", type=Path, help="directorio con los artefactos del legacy")
    detect.add_argument("--json", action="store_true", help="salida en JSON")

    map_cmd = commands.add_parser("map", help="saca todo lo que el sistema ES: rutas, jobs, pantallas, clases, tablas, catálogos, triggers")
    map_cmd.add_argument("artifact", type=Path, help="el artefacto desplegable (WAR/JAR/zip o directorio)")
    map_cmd.add_argument("--profile", required=True, help="perfil cuyos extractores aplicar (declara cómo minar este stack)")
    map_cmd.add_argument("--dump", type=Path, help="respaldo de la base (para inventariar datos y servidores foráneos)")
    map_cmd.add_argument("--evidence", type=Path, help="directorio de evidencia (evidence/<sid>) para medir cobertura observada")
    map_cmd.add_argument("--out", type=Path, default=Path("docs/pepper/system-map.json"), help="dónde escribir el mapa (default docs/pepper/system-map.json); la carpeta legible map/ queda al lado")

    rehydrate = commands.add_parser("rehydrate", help="del artefacto y el respaldo a un entorno aislado corriendo: compose desde el perfil, restauración, arranque, verificación")
    rehydrate.add_argument("legacy", type=Path, help="directorio con el desplegable, el respaldo y NOTAS.md")
    rehydrate.add_argument("--profile", required=True, help="perfil con la receta (compose_template, restore_template, server_images)")
    rehydrate.add_argument("--out", type=Path, default=Path("pepper-out/rehydrate"), help="dónde escribir compose, restore.sh, proxy, stub y .env")
    rehydrate.add_argument("--docs", type=Path, default=Path("docs/pepper"), help="dónde escribir environment.json y validation.md")
    rehydrate.add_argument("--notes", type=Path, help="NOTAS.md (default legacy/NOTAS.md)")
    rehydrate.add_argument("--port", type=int, default=18080, help="puerto en loopback donde el ingress publica el app")
    rehydrate.add_argument("--up", action="store_true", help="además de planear: levantar, restaurar, esperar, verificar y validar")
    rehydrate.add_argument("--wait", type=int, default=300, help="segundos máximos de espera al arranque del app")

    explore = commands.add_parser("explore", help="recorre el sistema solo: entra con cada rol, abre cada pantalla, provoca rechazos, llena y guarda; o ejecuta un plan del agente")
    explore.add_argument("compose", type=Path, help="docker-compose.yml del entorno rehidratado (se verifica el aislamiento en vivo antes)")
    explore.add_argument("--config", type=Path, required=True, help="explore.json: cómo entrar, roles y credenciales de la base desechable, pistas de llenado")
    explore.add_argument("--map", type=Path, help="system-map.json: de ahí salen las rutas a recorrer")
    explore.add_argument("--session", required=True, help="id de la sesión de evidencia (p. ej. explore-001)")
    explore.add_argument("--flow-name", help="nombre del flujo (default: el modo)")
    explore.add_argument("--plan", type=Path, help="plan.json escrito por el agente: pasos encadenados en vez del recorrido automático")
    explore.add_argument("--profile", help="perfil cuyos colectores declaran los parsers de los logs capturados")
    explore.add_argument("--hosts", help="hosts externos del artefacto (para isolate), separados por coma")
    explore.add_argument("--ingress", default="ingress")
    explore.add_argument("--out", type=Path, default=Path("evidence"), help="raíz de la evidencia (default evidence/)")
    explore.add_argument("--budget", type=int, default=0, help="segundos máximos de recorrido; al agotarse cierra limpio y escribe session.json")
    explore.add_argument("--no-submit", action="store_true", help="solo abrir y fotografiar pantallas; no apretar botones")
    explore.add_argument("--headed", action="store_true", help="navegador visible (para depurar)")
    explore.add_argument("--settle", type=int, default=12, help="segundos de espera al final antes de cerrar la ventana (default 12)")
    explore.add_argument("--margin", type=int, default=30, help="margen de captura a cada lado (default 30)")

    validate = commands.add_parser("validate", help="valida archivos contra los contratos de schemas/")
    validate.add_argument("files", type=Path, nargs="+", help="profile.json, parsers/*.json, session.json, environment.json, flow.json, events.jsonl, system-map.json, funcional.json")
    validate.add_argument("--schema", choices=("event", "environment", "flow", "functional-discovery", "parser", "profile", "session", "system-map"), help="fuerza el schema (si el nombre del archivo no lo delata)")

    isolate = commands.add_parser("isolate", help="verifica que un entorno rehidratado no pueda alcanzar nada externo")
    isolate.add_argument("compose", type=Path, help="docker-compose.yml del entorno rehidratado")
    isolate.add_argument("--hosts", help="hosts externos que el artefacto invoca, separados por coma: cada uno debe resolver al stub")
    isolate.add_argument("--live", action="store_true", help="además, verifica los contenedores en ejecución (según Docker, no según el archivo)")
    isolate.add_argument("--ingress", default="ingress",
                         help="servicio proxy que publica el puerto al host sin egress (default: ingress)")
    isolate.add_argument("--out", type=Path, help="escribe el reporte legible en este archivo")

    proxy_cmd = commands.add_parser("proxy", help="proxy HTTP del ingress: inyecta correlation_id y emite http.jsonl")
    from pepper.proxy import _host_port
    proxy_cmd.add_argument("--listen", type=_host_port, default=("0.0.0.0", 8080), help="host:puerto donde escuchar (default 0.0.0.0:8080)")
    proxy_cmd.add_argument("--upstream", type=_host_port, required=True, help="host:puerto del app rehidratado")
    proxy_cmd.add_argument("--out", default=None, help="además de stdout, escribir el http.jsonl a este archivo")
    proxy_cmd.add_argument("--timeout", type=float, default=120.0, help="segundos de espera por respuesta del app (default 120)")

    collect_cmd = commands.add_parser("collect", help="copia la ventana observada desde los contenedores a evidence/<session_id>/")
    collect_cmd.add_argument("compose", type=Path, help="docker-compose.yml del entorno rehidratado")
    collect_cmd.add_argument("session_id", help="sesión de observación (p. ej. flow-001)")
    collect_cmd.add_argument("--start", required=True, help="inicio de la ventana, ISO con zona (2026-09-02T10:00:00-06:00)")
    collect_cmd.add_argument("--end", required=True, help="fin de la ventana, ISO con zona")
    collect_cmd.add_argument("--margin", type=int, default=30, help="segundos de margen a cada lado (default 30)")
    collect_cmd.add_argument("--out", type=Path, default=Path("evidence"), help="raíz de la evidencia (default evidence/)")
    collect_cmd.add_argument("--ingress", default="ingress", help="servicio cuyo stdout es el http.jsonl del proxy (default: ingress)")

    demo = commands.add_parser("demo", help="correlate + package sobre examples/legacy-demo")
    demo.add_argument("--out", type=Path, default=Path("pepper-out/legacy-demo"), help="directorio de trabajo (default pepper-out/legacy-demo)")

    return parser


_TOOL_REMOTE_HINTS = ("GabrielaStark/PEPPER", "GabrielaStark/PEPPER.git")


def tool_remote_warning(root: Path) -> Optional[str]:
    """El clon de la herramienta NO es el repositorio del legacy.

    Si alguien trabaja dentro del clon con el remoto original conectado, un `git push`
    distraído publicaría el sistema de otra persona en un repo público. `.gitignore` ya
    impide agregar `legacy/`, `evidence/`, `pepper-out/`, `docs/pepper/` y `docs/analysis/`;
    esto avisa de la única vía que queda (un `git add -f`) y de cómo cerrarla. Solo habla
    cuando hay de verdad un legacy en la carpeta: durante el desarrollo de la herramienta
    no estorba.
    """
    config = root / ".git" / "config"
    if not config.is_file():
        return None
    try:
        text = config.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if not any(hint in text for hint in _TOOL_REMOTE_HINTS):
        return None
    legacy = root / "legacy"
    try:
        if not (legacy.is_dir() and any(legacy.iterdir())):
            return None
    except OSError:
        return None
    return ("⚠ Este clon sigue apuntando al repositorio de la herramienta y ya tiene un legacy dentro.\n"
            "  Nada de este sistema debe llegar ahí. Desconéctalo antes de seguir:\n"
            "      git remote remove origin        # o, si no necesitas git aquí: rm -rf .git\n"
            "  (`.gitignore` ya bloquea legacy/, evidence/, pepper-out/, docs/pepper/ y docs/analysis/.)")


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    warning = tool_remote_warning(Path.cwd())
    if warning:
        print(warning, file=sys.stderr)
    try:
        return COMMANDS[args.command](args)
    except (FileNotFoundError, ValueError, FileExistsError) as error:
        if isinstance(error, FileNotFoundError) and getattr(error, "filename", None) in ("docker", "javap", "pg_restore"):
            print(f"pepper {args.command}: no encuentro `{error.filename}` en el PATH. "
                  + ("Instala Docker Desktop y ábrelo; sin Docker no hay aislamiento, levantar ni explorar." if error.filename == "docker"
                     else f"Instálalo o pon su ruta en el PATH."), file=sys.stderr)
            return 2
        print(f"pepper {args.command}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
