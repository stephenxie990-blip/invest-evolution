import sys
from app import web_server as _impl

sys.modules[__name__] = _impl

if __name__ == "__main__":
    _impl.main()
