import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.commander import CommanderConfig  # noqa: F401
    from app.commander import CommanderRuntime  # noqa: F401
    from app.commander import StrategyGene  # noqa: F401
    from app.commander import StrategyGeneRegistry  # noqa: F401
    from app.commander import main  # noqa: F401

_impl = import_module("app.commander")

if __name__ == "__main__":
    raise SystemExit(_impl.main())
else:
    sys.modules[__name__] = _impl
