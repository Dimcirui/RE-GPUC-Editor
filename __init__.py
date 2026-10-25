"""RE GPUC Editor: Wilds / RE9 / Onimusha cloth generation and RE4 preservation-only editing."""
from . import autorebuild, canon, collision, draw, ops, props, sim, ui, weld
from . import wd_api  # noqa: F401 - loaded for Wilds-Dynamics, which finds it in sys.modules (no registering)
from . import presets

_MODULES = (props, canon, collision, sim, ops, presets, ui, weld, draw, autorebuild)


def register():
    for m in _MODULES:
        m.register()


def unregister():
    for m in reversed(_MODULES):
        m.unregister()
