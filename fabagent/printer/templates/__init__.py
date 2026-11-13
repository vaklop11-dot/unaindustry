"""Registar 3D sablona. Svaki build vraca PrintModel (tijela + napomene)."""

from fabagent.printer.templates import adapter, drzac_cijevi, kutija, nosac_l, poklopac

BUILDERS = {
    "nosac_l": nosac_l.build,
    "kutija": kutija.build,
    "adapter": adapter.build,
    "poklopac": poklopac.build,
    "drzac_cijevi": drzac_cijevi.build,
}
