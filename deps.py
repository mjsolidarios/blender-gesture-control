# SPDX-License-Identifier: GPL-3.0-or-later
"""
Dependency and model-asset management.

Design notes
------------
Packages are installed with ``pip --target`` into a directory *outside* the
add-on folder, under Blender's user scripts area. Two reasons:

1. Reinstalling or updating the add-on wipes the add-on folder. Keeping ~200 MB
   of wheels out of it means the user does not re-download them every update.
2. Blender's own ``site-packages`` is inside the application install, which is
   read-only on most Linux packages and on macOS app bundles, and needs an
   elevation prompt on Windows if Blender lives in Program Files.

MediaPipe depends on ``opencv-contrib-python``, which links a full GUI stack
(Qt/GTK on Linux). Loading that inside a running Blender process is a known
source of hard crashes and symbol clashes. So after MediaPipe is in place we
force ``opencv-python-headless`` over the top of it: same ``cv2`` module name,
same API for everything this add-on touches (VideoCapture, cvtColor, resize),
no window toolkit.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading

import bpy

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODEL_FILENAME = "hand_landmarker.task"

#: Google's published model bundles. Tried in order; float16 is roughly half
#: the size of full float32 and is the build Google recommends for live use.
MODEL_URLS = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/latest/hand_landmarker.task",
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/1/hand_landmarker.task",
)

REQUIREMENTS = ("mediapipe",)
HEADLESS_CV = "opencv-python-headless"

# Populated by check(); avoids paying the import cost on every UI redraw.
_status_cache = None
_status_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def deps_dir() -> str:
    """Directory holding pip-installed packages for this add-on."""
    base = bpy.utils.user_resource("SCRIPTS", path="addon_deps", create=True)
    path = os.path.join(base, "hand_gesture_control")
    os.makedirs(path, exist_ok=True)
    return path


def model_dir() -> str:
    """Directory holding the downloaded ``.task`` model bundle."""
    base = bpy.utils.user_resource("DATAFILES", path="hand_gesture_control",
                                   create=True)
    os.makedirs(base, exist_ok=True)
    return base


def model_path() -> str:
    return os.path.join(model_dir(), MODEL_FILENAME)


def model_present() -> bool:
    p = model_path()
    # A truncated download leaves a small file behind; the real bundle is ~7 MB.
    return os.path.isfile(p) and os.path.getsize(p) > 1_000_000


def ensure_sys_path() -> str:
    """Put the add-on's package directory on ``sys.path``. Idempotent."""
    path = deps_dir()
    if path not in sys.path:
        # Append rather than prepend: never shadow a module Blender ships.
        sys.path.append(path)
    return path


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def _is_headless(cv2) -> bool:
    """
    Report whether the loaded OpenCV was built without a window toolkit.

    Checking for the absence of ``cv2.imshow`` is unreliable: OpenCV 5's
    headless wheels still export the symbol and only raise when it is called.
    The build report is authoritative and stable across versions, listing
    ``GUI: NONE`` for headless builds and a toolkit name otherwise.
    """
    try:
        info = cv2.getBuildInformation()
    except Exception:
        return not hasattr(cv2, "imshow")
    for line in info.splitlines():
        stripped = line.strip()
        if stripped.startswith("GUI"):
            return "NONE" in stripped.upper()
    # No GUI section at all also means no window toolkit was compiled in.
    return True


def check(force: bool = False) -> dict:
    """
    Report which dependencies are importable.

    :returns: dict with keys ``mediapipe``, ``cv2``, ``numpy`` (version string
        or ``None``), ``cv2_headless`` (bool), ``model`` (bool) and ``ready``.
    """
    global _status_cache
    with _status_lock:
        if _status_cache is not None and not force:
            return dict(_status_cache)

    ensure_sys_path()
    status = {
        "mediapipe": None,
        "cv2": None,
        "numpy": None,
        "cv2_headless": False,
        "cv2_path": "",
        "model": model_present(),
        "errors": [],
    }

    try:
        import numpy
        status["numpy"] = getattr(numpy, "__version__", "?")
    except Exception as exc:                      # pragma: no cover
        status["errors"].append(f"numpy: {exc}")

    try:
        import cv2
        status["cv2"] = getattr(cv2, "__version__", "?")
        status["cv2_path"] = getattr(cv2, "__file__", "") or ""
        status["cv2_headless"] = _is_headless(cv2)
    except Exception as exc:
        status["errors"].append(f"cv2: {exc}")

    try:
        import mediapipe
        status["mediapipe"] = getattr(mediapipe, "__version__", "?")
        # Importing the module is not proof the Tasks API is usable.
        from mediapipe.tasks.python import vision  # noqa: F401
    except Exception as exc:
        status["errors"].append(f"mediapipe: {exc}")
        status["mediapipe"] = None

    status["ready"] = bool(status["mediapipe"] and status["cv2"]
                           and status["numpy"] and status["model"])

    with _status_lock:
        _status_cache = dict(status)
    return status


def invalidate():
    global _status_cache
    with _status_lock:
        _status_cache = None


# ---------------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------------

def _python_exe() -> str:
    """Absolute path to the interpreter running Blender."""
    exe = sys.executable
    if not exe or not os.path.exists(exe):
        # Very old builds left sys.executable pointing at the Blender binary.
        exe = getattr(bpy.app, "binary_path_python", "") or sys.executable
    return exe


def _run(args, log) -> bool:
    log(f"$ {' '.join(args)}")
    startupinfo = None
    if sys.platform == "win32":
        # Stop a console window flashing up over Blender on Windows.
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    try:
        proc = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=1800,
            startupinfo=startupinfo,
            env={**os.environ, "PYTHONNOUSERSITE": "1"},
        )
    except Exception as exc:
        log(f"failed to launch: {exc}")
        return False

    for line in (proc.stdout or "").splitlines():
        log(line.rstrip())
    if proc.returncode != 0:
        log(f"exited with code {proc.returncode}")
        return False
    return True


def install(log=print, upgrade: bool = False) -> bool:
    """
    Install MediaPipe and a headless OpenCV into :func:`deps_dir`.

    :param log: callable receiving progress lines.
    :param upgrade: pass ``--upgrade`` to pip.
    :returns: True on success.
    """
    target = deps_dir()
    py = _python_exe()
    log(f"interpreter: {py}")
    log(f"target:      {target}")

    # Blender ships pip but does not always bootstrap it.
    if not _run([py, "-m", "ensurepip", "--upgrade"], log):
        log("ensurepip reported a problem; continuing, pip may already exist")

    base = [py, "-m", "pip", "install", "--target", target,
            "--no-warn-script-location", "--disable-pip-version-check"]
    if upgrade:
        base.append("--upgrade")

    log("--- installing mediapipe ---")
    if not _run(base + list(REQUIREMENTS), log):
        return False

    # Overwrite MediaPipe's GUI-linked OpenCV with the headless build.
    log("--- installing headless opencv ---")
    if not _run(base + ["--upgrade", "--force-reinstall", "--no-deps",
                        HEADLESS_CV], log):
        log("headless OpenCV install failed; the GUI build may still work, "
            "but can be unstable inside Blender")

    ensure_sys_path()
    invalidate()
    log("done")
    return True


def download_model(log=print) -> bool:
    """Fetch ``hand_landmarker.task`` from Google's model store."""
    import urllib.error
    import urllib.request

    dest = model_path()
    tmp = dest + ".part"
    last_error = None

    for url in MODEL_URLS:
        log(f"downloading {url}")
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Blender-HandGestureControl"})
            with urllib.request.urlopen(req, timeout=120) as resp, \
                    open(tmp, "wb") as out:
                total = int(resp.headers.get("Content-Length") or 0)
                read = 0
                step = max(total // 10, 1) if total else 1 << 20
                next_mark = step
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    out.write(chunk)
                    read += len(chunk)
                    if read >= next_mark:
                        next_mark += step
                        if total:
                            log(f"  {read * 100 // total}%")
                        else:
                            log(f"  {read >> 20} MB")
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last_error = exc
            log(f"  failed: {exc}")
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            continue

        if os.path.getsize(tmp) < 1_000_000:
            log("  file is implausibly small, discarding")
            os.remove(tmp)
            last_error = "truncated download"
            continue

        os.replace(tmp, dest)
        log(f"saved to {dest}")
        invalidate()
        return True

    log(f"could not download the model ({last_error})")
    return False


def uninstall(log=print) -> bool:
    """Remove the installed packages. The model file is left in place."""
    import shutil
    target = deps_dir()
    log(f"removing {target}")
    try:
        shutil.rmtree(target)
    except Exception as exc:
        log(f"failed: {exc}")
        return False
    if target in sys.path:
        sys.path.remove(target)
    invalidate()
    log("done")
    return True


def online_access_ok() -> bool:
    """
    Blender 4.2+ blocks add-on network traffic unless the user allows it.

    Returns True on builds that predate the setting.
    """
    return bool(getattr(bpy.app, "online_access", True))
