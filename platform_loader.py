import importlib
import os
import platform
import shutil
import sys


COMPILED_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "compiled")


def _detect_platform():
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    return "linux"


def install_platform_binaries():
    plat = _detect_platform()
    plat_dir = os.path.join(COMPILED_DIR, plat)

    if not os.path.isdir(plat_dir):
        return

    root = os.path.dirname(os.path.abspath(__file__))

    for dirpath, _, filenames in os.walk(plat_dir):
        for fname in filenames:
            if not fname.endswith(".so"):
                continue
            src = os.path.join(dirpath, fname)
            rel = os.path.relpath(dirpath, plat_dir)
            dst_dir = os.path.join(root, rel)
            dst = os.path.join(dst_dir, fname)
            if os.path.exists(dst):
                continue
            os.makedirs(dst_dir, exist_ok=True)
            shutil.copy2(src, dst)


install_platform_binaries()
