# SPDX-License-Identifier: GPL-3.0-or-later
"""
Hand Gesture Control - drive Blender objects with your hands over a webcam.

Uses Google MediaPipe Tasks Vision Hand Landmarker for 21-point hand tracking,
draws every landmark as a viewport indicator, and maps pinch gestures onto
object transforms.

The add-on imports cleanly whether or not MediaPipe is installed: heavy imports
live inside :mod:`.tracker`, which is only touched once a session starts. That
way the preferences panel is always reachable to run the installer.
"""

bl_info = {
    "name": "Hand Gesture Control",
    "author": "mjsolidarios",
    "version": (1, 1, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar (N) > Gesture",
    "description": "Control 3D objects with hand gestures via webcam, using "
                   "MediaPipe Tasks Vision Hand Landmarker",
    "category": "3D View",
    "doc_url": "",
    "tracker_url": "",
}

import importlib

from . import (deps, filters, gestures, landmarks, operators, overlay, prefs,
               session, settings, tracker, ui)

# Reloading matters during development; without it Blender keeps the stale
# module objects from the first import and edits appear to have no effect.
_modules = (landmarks, filters, deps, session, tracker, gestures, overlay,
            settings, operators, ui, prefs)

if "_loaded" in locals():                          # pragma: no cover
    for _module in _modules:
        importlib.reload(_module)
_loaded = True

_registered = (settings, operators, ui, prefs)


def register():
    # Make previously installed packages importable before anything tries to
    # import them, so the panel reports an accurate status on first draw.
    try:
        deps.ensure_sys_path()
    except Exception as exc:
        print("[HandGestureControl] could not extend sys.path:", exc)

    for module in _registered:
        module.register()


def unregister():
    # Tear the live session down first: a running capture thread holding the
    # camera, or a draw handler referencing classes about to be unregistered,
    # will crash Blender if left in place.
    try:
        session.shutdown()
    except Exception:
        pass
    try:
        overlay.overlay.disable()
    except Exception:
        pass

    for module in reversed(_registered):
        try:
            module.unregister()
        except Exception as exc:
            print("[HandGestureControl] unregister failed for",
                  module.__name__, exc)


if __name__ == "__main__":                         # pragma: no cover
    register()
