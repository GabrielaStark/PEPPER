"""Aislamiento fail-closed: un entorno rehidratado no puede alcanzar nada fuera de su red.

Los casos de fuga son los que ocurrieron de verdad en el primer legacy real
(una vista con dblink alcanzó la base de producción por la VPN de la máquina), más
los casos adversariales de la auditoría 2026-09-02 (C-01): ingress impostor,
compose sin resolver, socket de Docker, privileged.
"""

import copy
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pepper.isolate import check_static, render  # noqa: E402

AISLADO = {
    "services": {
        "db": {"image": "postgres:16", "dns": ["10.4.2.254"], "networks": {"legacy": {"ipv4_address": "10.4.2.186"}}},
        "stub": {"image": "python:3-alpine", "dns": ["10.4.2.254"],
                 "networks": {"legacy": {"ipv4_address": "10.4.2.185",
                                         "aliases": ["bus.institucion.example", "smtp.gmail.com"]}}},
        "app": {"image": "jboss/wildfly:21.0.2.Final", "dns": ["10.4.2.254"],
                "networks": {"legacy": {"ipv4_address": "10.4.2.10"}},
                "volumes": ["../../legacy/app.war:/opt/jboss/wildfly/standalone/deployments/app.war:ro"]},
        "ingress": {"image": "python:3-alpine", "dns": ["10.4.2.254"],
                    "command": ["python3", "-u", "/pepper-proxy.py",
                                "--listen", "0.0.0.0:8080", "--upstream", "10.4.2.10:8080"],
                    "depends_on": {"app": {"condition": "service_started"}},
                    "networks": {"legacy": {}, "edge": {}},
                    "ports": [{"published": "18080", "target": 8080, "host_ip": "127.0.0.1"}],
                    "volumes": ["./proxy/proxy.py:/pepper-proxy.py:ro"]},
    },
    "networks": {
        "legacy": {"internal": True, "ipam": {"config": [{"subnet": "10.4.2.0/24"}]}},
        # Docker no publica puertos desde una red internal: el ingress, y solo él, sale por aquí
        "edge": {"driver": "bridge"},
    },
}


class Base(unittest.TestCase):
    """Un compose_dir real con el proxy de PEPPER copiado, para que el hash verifique."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.compose_dir = Path(self._tmp.name)
        (self.compose_dir / "proxy").mkdir()
        shutil.copy2(ROOT / "pepper" / "proxy.py", self.compose_dir / "proxy" / "proxy.py")

    def tearDown(self):
        self._tmp.cleanup()

    def check(self, compose, hosts=None, resolved=True):
        return check_static(compose, hosts, resolved=resolved, compose_dir=self.compose_dir)

    def leak(self, mutate):
        compose = copy.deepcopy(AISLADO)
        mutate(compose)
        return self.check(compose)


class AisladoTest(Base):
    def test_el_entorno_de_referencia_esta_verificado(self):
        report = self.check(AISLADO, hosts=["bus.institucion.example", "smtp.gmail.com"])
        self.assertEqual(report.verdict, "VERIFIED", [f.check for f in report.findings if f.level != "ok"])
        self.assertTrue(report.isolated)
        self.assertIn("AISLADO (verificado)", render(report, "prueba"))

    def test_el_ingress_publica_por_una_red_exclusiva(self):
        report = self.check(AISLADO)
        self.assertTrue(any("ningún otro servicio usa" in f.check for f in report.findings if f.level == "ok"))
        self.assertTrue(any("upstream" in f.check for f in report.findings if f.level == "ok"))

    def test_el_hash_del_proxy_se_verifica(self):
        report = self.check(AISLADO)
        self.assertTrue(any("hash verificado" in f.check for f in report.findings if f.level == "ok"))


class FugaTest(Base):
    def test_red_sin_internal_es_fuga(self):
        report = self.leak(lambda c: c["networks"]["legacy"].__setitem__("internal", False))
        self.assertEqual(report.verdict, "FAILED")
        self.assertTrue(any("NO es internal" in f.check for f in report.errors))

    def test_ninguna_red_interna_se_reporta_aunque_no_haya_servicios_fuera(self):
        report = self.leak(lambda c: c["networks"].__setitem__("legacy", {"driver": "bridge"}))
        self.assertTrue(any("ninguna red es" in f.check for f in report.errors))

    def test_network_mode_host_es_fuga_total(self):
        report = self.leak(lambda c: c["services"]["app"].__setitem__("network_mode", "host"))
        self.assertTrue(any("network_mode" in f.check for f in report.errors))

    def test_servicio_en_red_no_declarada_es_fuga(self):
        report = self.leak(lambda c: c["services"]["app"].__setitem__("networks", {"otra": {}}))
        self.assertTrue(any("no declara" in f.check for f in report.errors))

    def test_servicio_sin_redes_cae_en_default_y_es_fuga(self):
        report = self.leak(lambda c: c["services"]["app"].pop("networks"))
        self.assertTrue(any("`default`" in f.check for f in report.errors))

    def test_extra_hosts_a_ip_externa_es_fuga(self):
        report = self.leak(lambda c: c["services"]["db"].__setitem__("extra_hosts", ["prodserver:10.10.39.19"]))
        self.assertTrue(any("10.10.39.19" in f.check for f in report.errors))

    def test_extra_hosts_dentro_de_la_red_interna_es_valido(self):
        report = self.leak(lambda c: c["services"]["app"].__setitem__("extra_hosts", {"prodserver": "10.4.2.185"}))
        self.assertEqual(report.verdict, "VERIFIED", [f.check for f in report.findings if f.level != "ok"])

    def test_host_externo_sin_alias_al_stub_es_fuga(self):
        report = self.check(AISLADO, hosts=["desabus.institucion.example"])
        self.assertEqual(report.verdict, "FAILED")
        self.assertTrue(any("no está declarado como alias" in f.check for f in report.errors))


class IngressImpostorTest(Base):
    """C-01: llamarse `ingress` no basta; debe SER el proxy de PEPPER."""

    def test_socat_ya_no_pasa(self):
        def mutate(c):
            c["services"]["ingress"] = {"image": "alpine/socat",
                                        "command": ["TCP-LISTEN:8080,fork", "TCP:10.4.2.10:8080"],
                                        "networks": {"legacy": {}},
                                        "ports": [{"published": "18080", "target": 8080, "host_ip": "127.0.0.1"}]}
        report = self.leak(mutate)
        self.assertEqual(report.verdict, "FAILED")
        self.assertTrue(any("imagen no permitida" in f.check for f in report.errors))

    def test_el_exfiltrador_de_la_auditoria_ya_no_pasa(self):
        def mutate(c):
            c["services"]["ingress"] = {"image": "alpine",
                                        "command": ["sh", "-c", "exfiltrar"],
                                        "networks": {"legacy": {}}}
        report = self.leak(mutate)
        self.assertEqual(report.verdict, "FAILED")

    def test_un_proxy_py_ajeno_no_pasa_el_hash(self):
        (self.compose_dir / "proxy" / "proxy.py").write_text("# no soy el proxy de PEPPER\n", encoding="utf-8")
        report = self.check(AISLADO)
        self.assertEqual(report.verdict, "FAILED")
        self.assertTrue(any("hash distinto" in f.check for f in report.errors))

    def test_un_segundo_montaje_en_el_ingress_es_fuga(self):
        report = self.leak(lambda c: c["services"]["ingress"]["volumes"].append("../../legacy:/legacy:ro"))
        self.assertEqual(report.verdict, "FAILED")
        self.assertTrue(any("exactamente 1" in f.check for f in report.errors))

    def test_sin_compose_dir_no_hay_verde(self):
        report = check_static(AISLADO, resolved=True, compose_dir=None)
        self.assertEqual(report.verdict, "UNKNOWN")
        self.assertFalse(report.isolated)

    def test_entrypoint_malicioso_no_pasa_aunque_el_command_sea_correcto(self):
        report = self.leak(lambda c: c["services"]["ingress"].__setitem__(
            "entrypoint", ["python3", "-c", "import urllib.request; urllib.request.urlopen('https://example.com')"]
        ))
        self.assertEqual(report.verdict, "FAILED")
        self.assertTrue(any("entrypoint" in f.check for f in report.errors))

    def test_command_en_forma_shell_no_pasa(self):
        report = self.leak(lambda c: c["services"]["ingress"].__setitem__(
            "command", "python3 -u /pepper-proxy.py --listen 0.0.0.0:8080 --upstream 10.4.2.10:8080"
        ))
        self.assertTrue(any("no verificable" in f.check for f in report.errors))

    def test_upstream_externo_no_pasa(self):
        def mutate(c):
            command = c["services"]["ingress"]["command"]
            command[command.index("--upstream") + 1] = "10.0.3.2:8080"
        report = self.leak(mutate)
        self.assertEqual(report.verdict, "FAILED")
        self.assertTrue(any("no es una dependencia interna" in f.check for f in report.errors))

    def test_otro_servicio_en_la_red_de_publicacion_es_fuga(self):
        report = self.leak(lambda c: c["services"]["app"]["networks"].__setitem__("edge", {}))
        self.assertEqual(report.verdict, "FAILED")
        self.assertTrue(any("NO es internal" in f.check for f in report.errors))
        self.assertTrue(any("también la usan: app" in f.check for f in report.errors))

    def test_ingress_con_dos_redes_de_salida_es_fuga(self):
        def mutate(c):
            c["networks"]["edge2"] = {"driver": "bridge"}
            c["services"]["ingress"]["networks"]["edge2"] = {}
        report = self.leak(mutate)
        self.assertEqual(report.verdict, "FAILED")
        self.assertTrue(any("2 redes con salida" in f.check for f in report.errors))

    def test_ingress_sin_red_de_publicacion_es_aviso_no_fuga(self):
        # nada sale, pero Docker tampoco publica el puerto: el host no podría entrar
        report = self.leak(lambda c: c["services"]["ingress"]["networks"].pop("edge"))
        self.assertEqual(report.verdict, "VERIFIED", [f.check for f in report.findings if f.level != "ok"])
        self.assertTrue(any("no tiene red de publicación" in f.check for f in report.warnings))

    def test_sin_dns_no_hay_verde(self):
        # el resolver embebido reenviaría al resolver del host todo nombre que no sea alias
        report = self.leak(lambda c: c["services"]["app"].pop("dns"))
        self.assertEqual(report.verdict, "UNKNOWN")
        self.assertTrue(any("no fija `dns:`" in f.check for f in report.unknowns))

    def test_dns_fuera_de_la_subred_interna_es_fuga(self):
        report = self.leak(lambda c: c["services"]["app"].__setitem__("dns", ["8.8.8.8"]))
        self.assertEqual(report.verdict, "FAILED")
        self.assertTrue(any("DNS fuera" in f.check for f in report.errors))

    def test_el_sumidero_dns_se_reporta_en_verde(self):
        report = self.check(AISLADO)
        self.assertEqual(sum(1 for f in report.findings if f.level == "ok" and "sumidero DNS" in f.check), 4)

    def test_red_de_publicacion_preexistente_no_es_verde(self):
        report = self.leak(lambda c: c["networks"].__setitem__("edge", {"external": True}))
        self.assertEqual(report.verdict, "UNKNOWN")


class CapacidadesTest(Base):
    def test_docker_socket_es_fuga(self):
        report = self.leak(lambda c: c["services"]["app"]["volumes"].append(
            "/var/run/docker.sock:/var/run/docker.sock"))
        self.assertTrue(any("socket de Docker" in f.check for f in report.errors))

    def test_privileged_es_fuga(self):
        report = self.leak(lambda c: c["services"]["db"].__setitem__("privileged", True))
        self.assertTrue(any("privileged" in f.check for f in report.errors))

    def test_cap_add_es_fuga(self):
        report = self.leak(lambda c: c["services"]["db"].__setitem__("cap_add", ["NET_ADMIN"]))
        self.assertTrue(any("capacidades" in f.check for f in report.errors))

    def test_montaje_del_host_con_escritura_es_fuga(self):
        report = self.leak(lambda c: c["services"]["app"]["volumes"].append("../../legacy:/datos"))
        self.assertTrue(any("con escritura" in f.check for f in report.errors))


class PoliticaDelNavegadorTest(unittest.TestCase):
    """El navegador del humano es parte del perímetro: el ingress vivo debe imponerle
    la política de PEPPER, y se comprueba por loopback — nunca hacia otro destino."""

    def _serve(self, csp):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                self.send_response(302); self.send_header("Location", "/login")
                if csp: self.send_header("Content-Security-Policy", csp)
                self.send_header("Content-Length", "0"); self.end_headers()
        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        return {"NetworkSettings": {"Ports": {"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(srv.server_address[1])}]}}}

    def test_con_la_politica_de_pepper_es_verde(self):
        from pepper.isolate import Report, _check_live_browser_policy
        from pepper.proxy import BROWSER_POLICY
        report = Report()
        _check_live_browser_policy("ingress", self._serve(BROWSER_POLICY), report)
        self.assertEqual(report.errors, [])
        self.assertTrue(any("impone al navegador" in f.check for f in report.findings if f.level == "ok"))

    def test_sin_politica_o_con_una_laxa_es_fuga(self):
        from pepper.isolate import Report, _check_live_browser_policy
        for csp in (None, "default-src *", "default-src 'self'"):   # la última no reporta bloqueos
            report = Report()
            _check_live_browser_policy("ingress", self._serve(csp), report)
            self.assertEqual(report.verdict, "FAILED", csp)

    def test_sin_puerto_publicado_no_hay_verde(self):
        from pepper.isolate import Report, _check_live_browser_policy
        report = Report()
        _check_live_browser_policy("ingress", {"NetworkSettings": {"Ports": {}}}, report)
        self.assertEqual(report.verdict, "UNKNOWN")


class ConexionesVivasDelIngressTest(unittest.TestCase):
    """El hash demuestra QUÉ código se montó; esto, CON QUIÉN habla el proceso.

    El ingress es el único contenedor con salida: si tiene una conexión abierta a
    algo que no es el app interno, el entorno dejó de estar aislado aunque el
    archivo montado siga siendo el proxy correcto.
    """

    def _report(self, peers, upstream=("10.100.0.10", 8080), subnets=("10.100.0.0/24",)):
        import ipaddress
        from unittest import mock

        from pepper.isolate import Report, _check_live_ingress_peers

        report = Report()
        redes = [ipaddress.ip_network(s) for s in subnets]
        with mock.patch("pepper.isolate._live_remote_peers", return_value=peers):
            _check_live_ingress_peers("ingress", "c1", upstream, redes, report)
        return report

    def test_solo_habla_con_el_app_interno(self):
        report = self._report(["10.100.0.10"])
        self.assertEqual(report.errors, [])
        self.assertTrue(any("solo tiene conexiones" in f.check for f in report.findings))

    def test_una_conexion_entrante_del_navegador_no_es_salida(self):
        # /proc/net/tcp: LISTEN en :8080, el navegador del host entrando por ese puerto,
        # y el proxy saliendo hacia el app interno. Solo lo último es un destino remoto.
        from unittest import mock

        from pepper.isolate import _live_remote_peers

        def hx(ip, port):
            import ipaddress
            return int(ipaddress.ip_address(ip)).to_bytes(4, "little").hex().upper() + ":" + f"{port:04X}"
        tabla = "\n".join([
            "  sl  local_address rem_address   st",
            f"   0: {hx('0.0.0.0', 8080)} {hx('0.0.0.0', 0)} 0A",           # LISTEN
            f"   1: {hx('172.20.0.2', 8080)} {hx('172.20.0.1', 64068)} 01",  # ENTRANTE: el host al puerto publicado
            f"   2: {hx('10.100.0.4', 39288)} {hx('10.100.0.10', 8080)} 01",  # SALIENTE: al app
        ])
        fake = mock.Mock(returncode=0, stdout=tabla)
        with mock.patch("pepper.isolate.subprocess.run", return_value=fake):
            self.assertEqual(_live_remote_peers("c1"), ["10.100.0.10"])

    def test_una_conexion_hacia_fuera_es_fuga(self):
        report = self._report(["10.100.0.10", "1.1.1.1"])
        self.assertTrue(any("1.1.1.1" in f.check for f in report.errors), report.errors)

    def test_una_conexion_a_la_red_del_host_es_fuga(self):
        # el caso que importa: la laptop en la red institucional, con VPN
        report = self._report(["10.33.121.254"])
        self.assertTrue(any("10.33.121.254" in f.check for f in report.errors), report.errors)

    def test_si_no_se_puede_leer_no_hay_verde(self):
        report = self._report(None)
        self.assertEqual(report.verdict, "UNKNOWN")
        self.assertFalse(report.isolated)


class FailClosedTest(Base):
    """Lo no verificado bloquea: UNKNOWN nunca es verde (C-01)."""

    def test_compose_sin_resolver_es_unknown(self):
        report = self.check(AISLADO, resolved=False)
        self.assertEqual(report.verdict, "UNKNOWN")
        self.assertFalse(report.isolated)
        self.assertIn("NO VERIFICADO", render(report, "prueba"))

    def test_variable_sin_sustituir_es_unknown(self):
        report = self.leak(lambda c: c["networks"]["legacy"].__setitem__("internal", "${INTERNAL:-false}"))
        # la variable delata compose sin resolver Y el valor no-True cae como red no interna
        self.assertNotEqual(report.verdict, "VERIFIED")


class AvisoTest(Base):
    def test_publicar_la_base_al_host_es_aviso_no_fuga(self):
        report = self.leak(lambda c: c["services"]["db"].__setitem__(
            "ports", [{"published": "15432", "target": 5432}]))
        self.assertEqual(report.verdict, "VERIFIED", "publicar un puerto no da salida al contenedor")
        self.assertTrue(any("publica puertos al host" in f.check for f in report.warnings))

    def test_ingress_en_todas_las_interfaces_es_aviso(self):
        report = self.leak(lambda c: c["services"]["ingress"].__setitem__(
            "ports", [{"published": "18080", "target": 8080}]))
        self.assertEqual(report.verdict, "VERIFIED")
        self.assertTrue(any("todas las interfaces" in f.check for f in report.warnings))


if __name__ == "__main__":
    unittest.main()
