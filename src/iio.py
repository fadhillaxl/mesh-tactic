"""
Compatibility shim for libiio python bindings.
Automatically prefers system-installed iio (e.g. from python3-libiio on Debian/Ubuntu/Armbian)
for exact C library ABI compatibility.
Falls back to vendored ctypes bindings on systems without python3-libiio (such as macOS).
"""
import importlib.util
import os
import sys

_curr_dir = os.path.dirname(os.path.abspath(__file__))
_system_iio_path = None

for _p in sys.path:
    if not _p:
        continue
    try:
        if os.path.samefile(_p, _curr_dir):
            continue
    except Exception:
        if os.path.abspath(_p) == _curr_dir:
            continue
    _cand = os.path.join(_p, "iio.py")
    if os.path.isfile(_cand):
        try:
            if os.path.samefile(_cand, __file__):
                continue
        except Exception:
            if os.path.abspath(_cand) == os.path.abspath(__file__):
                continue
        _system_iio_path = _cand
        break

_target_path = _system_iio_path if _system_iio_path else os.path.join(_curr_dir, "_iio_vendored.py")

_spec = importlib.util.spec_from_file_location("iio", _target_path)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["iio"] = _mod
_spec.loader.exec_module(_mod)

# Populate module globals so 'from iio import Context' works
for _k, _v in _mod.__dict__.items():
    globals()[_k] = _v
