# SPDX-License-Identifier: GPL-3.0-or-later
"""Add-on preferences: dependency installation and disk locations."""

from __future__ import annotations

import bpy
from bpy.types import AddonPreferences

from . import deps


def _package_name() -> str:
    """
    The key Blender filed this add-on under.

    Legacy add-ons register as ``hand_gesture_control``; extensions register as
    ``bl_ext.<repo>.hand_gesture_control``. ``__package__`` is correct for both.
    """
    return __package__


class HGC_Preferences(AddonPreferences):
    bl_idname = _package_name()

    def draw(self, context):
        layout = self.layout
        status = deps.check()

        header = layout.row()
        header.scale_y = 1.2
        if status["ready"]:
            header.label(text="Ready", icon="CHECKMARK")
        else:
            header.alert = True
            header.label(text="Setup incomplete", icon="ERROR")

        box = layout.box()
        grid = box.grid_flow(row_major=True, columns=2, align=True)
        grid.label(text="MediaPipe")
        grid.label(text=status["mediapipe"] or "not installed",
                   icon="CHECKMARK" if status["mediapipe"] else "X")
        grid.label(text="OpenCV")
        cv_text = status["cv2"] or "not installed"
        if status["cv2"] and not status["cv2_headless"]:
            cv_text += "  (GUI build)"
        grid.label(text=cv_text, icon="CHECKMARK" if status["cv2"] else "X")
        grid.label(text="NumPy")
        grid.label(text=status["numpy"] or "not found",
                   icon="CHECKMARK" if status["numpy"] else "X")
        grid.label(text="Hand model")
        grid.label(text="downloaded" if status["model"] else "missing",
                   icon="CHECKMARK" if status["model"] else "X")

        if status["cv2"] and not status["cv2_headless"]:
            note = box.column()
            note.label(text="A GUI build of OpenCV is in use. If Blender "
                            "becomes unstable, reinstall to replace it with "
                            "the headless build.", icon="INFO")

        if not deps.online_access_ok():
            warn = layout.box()
            warn.alert = True
            warn.label(text="Blender's online access is disabled",
                       icon="INTERNET")
            warn.label(text="Enable it in Preferences > System > Network "
                            "before installing.")

        row = layout.row(align=True)
        row.scale_y = 1.4
        row.enabled = deps.online_access_ok()
        install = row.operator("hgc.install_dependencies", icon="IMPORT",
                               text="Install Dependencies")
        install.upgrade = False
        row.operator("hgc.download_model", icon="URL", text="Download Model")

        row = layout.row(align=True)
        upgrade = row.operator("hgc.install_dependencies", icon="FILE_REFRESH",
                               text="Upgrade Packages")
        upgrade.upgrade = True
        row.operator("hgc.refresh_status", icon="CHECKMARK", text="Re-check")
        row.operator("hgc.uninstall_dependencies", icon="TRASH",
                     text="Remove Packages")

        if status["errors"]:
            box = layout.box()
            box.label(text="Import problems", icon="ERROR")
            col = box.column(align=True)
            col.scale_y = 0.8
            for line in status["errors"]:
                col.label(text=line[:120])

        box = layout.box()
        box.label(text="Locations", icon="FILE_FOLDER")
        col = box.column(align=True)
        col.scale_y = 0.8
        col.label(text=f"Packages: {deps.deps_dir()}")
        col.label(text=f"Model: {deps.model_path()}")
        if status["cv2_path"]:
            col.label(text=f"cv2: {status['cv2_path']}")

        box = layout.box()
        box.label(text="Usage", icon="HELP")
        col = box.column(align=True)
        col.scale_y = 0.8
        col.label(text="1. Select one or more objects in the 3D viewport.")
        col.label(text="2. Open the sidebar (N) and pick the Gesture tab.")
        col.label(text="3. Press Start Tracking, then pinch to grab.")
        col.label(text="   Thumb+index moves, +middle rotates, +ring scales.")
        col.label(text="4. Press Esc in the viewport to stop.")


classes = (HGC_Preferences,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
