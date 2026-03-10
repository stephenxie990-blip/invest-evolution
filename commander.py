from importlib import import_module
import sys

_impl = import_module("app.commander")

if __name__ == "__main__":
    raise SystemExit(_impl.main())
else:
    sys.modules[__name__] = _impl
