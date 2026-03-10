from importlib import import_module
import sys

_impl = import_module("app.train")

if __name__ == "__main__":
    _impl.train_main()
else:
    sys.modules[__name__] = _impl
