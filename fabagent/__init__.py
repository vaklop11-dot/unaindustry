"""FabAgent - deterministicki generator proizvodnih fajlova za mikro-manufakturu.

Laser (SVG, ArgoLaser MK20) i FDM 3D stampa (STL/3MF). Sva geometrija nastaje iz
eksplicitnih parametara i pravila iz ``fabagent.core.config``.
"""

import logging

__version__ = "0.1.0"

logging.getLogger("fabagent").addHandler(logging.NullHandler())
