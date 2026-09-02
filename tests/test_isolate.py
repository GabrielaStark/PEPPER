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
        "db": {"image": "postgres:16", "networks": {"legacy": {"ipv4_address": "10.4.2.186"}}},
        "stub": {"image": "python:3-alpine",
                 "networks": {"legacy": {"ipv4_address": "10.4.2.185",
                                         "aliases": ["bus.institucion.example", "smtp.gmail.com"]}}},
        "app": {"image": "jboss/wildfly:21.0.2.Final",
                "networks": {"legacy": {"ipv4_address": "10.4.2.10"}},
                "volumes": ["../../legacy/app.war:/opt/jboss/wildfly/standalone/deployments/app.war:ro"]},
        "ingress": {"image": "python:3-alpine",
                    "command": ["python3", "-u", "/pepper-proxy.py",
                                "--listen", "0.0.0.0:8080", "--upstream", "10.4.2.10:8080"],
                    "networks": {"legacy": {}, "edge": {}},
                    "ports": [{"published": "18080", "target": 8080, "host_ip": "127.0.0.1"}],
                    "volumes": ["./proxy/proxy.py:/pepper-proxy.py:ro"]},
    },
    "networks": {
        "legacy": {"internal": True, "ipam": {"config": [{"subnet": "10.4.2.0/24"}]}},
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

    def test_el_ingress_es_la_unica_excepcion(self):
        report = self.check(AISLADO)
        salidas = [f for f in report.findings if "ingress" in f.check and "salida" in f.check]
        self.assertEqual(len(salidas), 1)
        self.assertEqual(salidas[0].level, "ok")

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
                                        "networks": {"legacy": {}, "edge": {}},
                                        "ports": [{"published": "18080", "target": 8080, "host_ip": "127.0.0.1"}]}
        report = self.leak(mutate)
        self.assertEqual(report.verdict, "FAILED")
        self.assertTrue(any("no python" in f.check for f in report.errors))

    def test_el_exfiltrador_de_la_auditoria_ya_no_pasa(self):
        def mutate(c):
            c["services"]["ingress"] = {"image": "alpine",
                                        "command": ["sh", "-c", "exfiltrar"],
                                        "networks": {"legacy": {}, "edge": {}}}
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
