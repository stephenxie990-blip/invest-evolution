import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.train import SelfLearningController  # noqa: F401
    from app.train import TrainingResult  # noqa: F401
    from app.train import set_event_callback  # noqa: F401
    from app.train import train_main  # noqa: F401

_impl = import_module("app.train")

if __name__ == "__main__":
    _impl.train_main()
else:
    sys.modules[__name__] = _impl
