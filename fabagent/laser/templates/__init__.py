"""Registar laserskih sablona: ime -> build funkcija. Seme parametara su u core/config.py."""

from fabagent.laser.templates import burence, casa, oznaka, plocica, privesak

BUILDERS = {
    "privesak": privesak.build,
    "casa": casa.build,
    "plocica": plocica.build,
    "oznaka_serijski": oznaka.build_serijski,
    "oznaka_qr": oznaka.build_qr,
    "oznaka_barkod": oznaka.build_barkod,
    "burence": burence.build,
}
