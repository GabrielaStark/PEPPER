"""Aislamiento: un entorno rehidratado no puede alcanzar nada fuera de su red.

Los casos de fuga son los que ocurrieron de verdad en el primer legacy real
(una vista con dblink alcanzó la base de producción por la VPN de la máquina).
"""

import sys
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
        "app": {"image": "jboss/wildfly:21.0.2.Final", "networks": {"legacy": {"ipv4_address": "10.4.2.10"}}},
        "ingress": {"image": "alpine/socat", "networks": {"legacy": {}, "edge": {}},
                    "ports": [{"published": "18080", "target": 8080}]},
    },
    "networks": {
        "legacy": {"internal": True, "ipam": {"config": [{"subnet": "10.4.2.0/24"}]}},
        "edge": {"driver": "bridge"},
    },
}


def leak(mutate):
    import copy
    compose = copy.deepcopy(AISLADO)
    mutate(compose)
    return check_static(compose)


class AisladoTest(unittest.TestCase):
    def test_el_entorno_de_referencia_esta_aislado(self):
        report = check_static(AISLADO, external_hosts=["bus.institucion.example", "smtp.gmail.com"])
        self.assertTrue(report.isolated, [f.check for f in report.errors])
        self.assertIn("AISLADO", render(report, "prueba"))

    def test_el_ingress_es_la_unica_excepcion(self):
        report = check_static(AISLADO)
        salidas = [f for f in report.findings if "ingress" in f.check and "salida" in f.check]
        self.assertEqual(len(salidas), 1)
        self.assertEqual(salidas[0].level, "ok")


class FugaTest(unittest.TestCase):
    def test_red_sin_internal_es_fuga(self):
        report = leak(lambda c: c["networks"]["legacy"].__setitem__("internal", False))
        self.assertFalse(report.isolated)
        self.assertTrue(any("NO es internal" in f.check for f in report.errors))

    def test_ninguna_red_interna_se_reporta_aunque_no_haya_servicios_fuera(self):
        report = leak(lambda c: c["networks"].__setitem__("legacy", {"driver": "bridge"}))
        self.assertTrue(any("ninguna red es" in f.check for f in report.errors))

    def test_network_mode_host_es_fuga_total(self):
        report = leak(lambda c: c["services"]["app"].__setitem__("network_mode", "host"))
        self.assertTrue(any("network_mode" in f.check for f in report.errors))

    def test_servicio_en_red_no_declarada_es_fuga(self):
        report = leak(lambda c: c["services"]["app"].__setitem__("networks", {"otra": {}}))
        self.assertTrue(any("no declara" in f.check for f in report.errors))

    def test_servicio_sin_redes_cae_en_default_y_es_fuga(self):
        report = leak(lambda c: c["services"]["app"].pop("networks"))
        self.assertTrue(any("`default`" in f.check for f in report.errors))

    def test_extra_hosts_a_ip_externa_es_fuga(self):
        report = leak(lambda c: c["services"]["db"].__setitem__("extra_hosts", ["sidecoprod:10.10.39.19"]))
        self.assertTrue(any("10.10.39.19" in f.check for f in report.errors))

    def test_extra_hosts_dentro_de_la_red_interna_es_valido(self):
        report = leak(lambda c: c["services"]["app"].__setitem__("extra_hosts", {"sidecoprod": "10.4.2.185"}))
        self.assertTrue(report.isolated, [f.check for f in report.errors])

    def test_host_externo_sin_alias_al_stub_es_fuga(self):
        report = check_static(AISLADO, external_hosts=["desabus.institucion.example"])
        self.assertFalse(report.isolated)
        self.assertTrue(any("no está declarado como alias" in f.check for f in report.errors))


class AvisoTest(unittest.TestCase):
    def test_publicar_la_base_al_host_es_aviso_no_fuga(self):
        report = leak(lambda c: c["services"]["db"].__setitem__("ports", [{"published": "15432", "target": 5432}]))
        self.assertTrue(report.isolated, "publicar un puerto no da salida al contenedor")
        self.assertTrue(any("publica puertos al host" in f.check for f in report.warnings))

    def test_ingress_con_volumenes_es_aviso(self):
        report = leak(lambda c: c["services"]["ingress"].__setitem__("volumes", ["../../legacy:/legacy:ro"]))
        self.assertTrue(any("ingress monta volúmenes" in f.check for f in report.warnings))

    def test_ingress_con_el_proxy_de_pepper_no_es_aviso(self):
        report = leak(lambda c: c["services"]["ingress"].__setitem__(
            "volumes", ["./proxy/proxy.py:/pepper-proxy.py:ro"]))
        self.assertFalse(any("volúmenes" in f.check for f in report.warnings))
        self.assertTrue(any("proxy de PEPPER" in f.check for f in report.findings if f.level == "ok"))
        self.assertTrue(report.isolated)


if __name__ == "__main__":
    unittest.main()
