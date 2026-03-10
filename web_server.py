from importlib import import_module
import sys

_impl = import_module("app.web_server")

if __name__ == "__main__":
    _impl.main()
else:
    sys.modules[__name__] = _impl
