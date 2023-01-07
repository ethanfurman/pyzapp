version = 0, 84, 0

from .path import *
import antipathy.path as _path

__all__ = _path.__all__

def set_py3_mode():
    _path.bPath.basecls = _path.bPath, bytes
    _path.uPath.basecls = _path.uPath, unicode
