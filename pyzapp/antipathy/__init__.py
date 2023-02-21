version = 0, 85, 3

from .path import *
from . import path as _path

__all__ = _path.__all__

def set_py3_mode():
    _path.bPath.basecls = _path.bPath, bytes
    _path.uPath.basecls = _path.uPath, unicode
