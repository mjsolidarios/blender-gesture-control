# SPDX-License-Identifier: GPL-3.0-or-later
"""The N-panel interface, under View3D > Sidebar > Gesture."""

from __future__ import annotations

import textwrap

import bpy
from bpy.types import Panel

from . import deps, session, settings as settings_mod

CATEGORY = "Gesture"

# Compact gesture map shown on the main panel and in the Gestures section.
_GESTURE_ROWS = (
    ("Pick (thumb + all tips)", "Move", "VIEW_PAN"),
    ("Pick + twist hand", "Rotate", "ORIENTATION_GIMBAL"),
    ("Both hands picking", "Scale", "FULLSCREEN_ENTER"),
    ("Point with index", "Toggle select", "RESTRICT_SELECT_OFF"),
)


def _draw_wrapped(layout, text, width=46, icon=None):
    """Draw readable multi-line copy in Blender's non-wrapping panel UI."""
    lines = textwrap.wrap(" ".join(str(text).split()), width=width) or [""]
    for index, line in enumerate(lines):
        if icon and index == 0:
            layout.label(text=line, icon=icon)
        else:
            layout.label(text=line)


def _draw_gesture_map(layout, title="How to control", show_hint=True):
    """Shared pick → action cheat sheet."""
    box = layout.box()
    box.label(text=title, icon="HAND")
    grid = box.grid_flow(row_major=True, columns=2, align=True)
    for gesture, effect, icon in _GESTURE_ROWS:
        grid.label(text=gesture)
        grid.label(text=effect, icon=icon)
    if show_hint:
        hint = box.column(align=True)
        hint.scale_y = 0.85
        hint.label(text="Gather all fingertips to the thumb to grab.")
        hint.label(text="Release to stop. Esc ends tracking.")


def _reset_button(layout, group, text):
    row = layout.row()
    row.alignment = "RIGHT"
    op = row.operator("hgc.reset_settings", icon="FILE_REFRESH", text=text)
    op.group = group
    return row


class _Base:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY


class HGC_PT_main(_Base, Panel):
    bl_label = "Hand Gesture Control"
    bl_idname = "HGC_PT_main"

    def draw(self, context):
        layout = self.layout
        cfg = settings_mod.get(context)
        status = deps.check()

        if not status["ready"]:
            self._draw_setup(layout, status)
            return

        running = session.is_running()
        selected = len(context.selected_objects)

        # ---- primary action --------------------------------------------
        row = layout.row()
        row.scale_y = 1.6
        if running:
            row.operator("hgc.stop", icon="PAUSE", text="Stop Tracking")
        else:
            row.operator("hgc.start", icon="PLAY", text="Start Tracking")

        # ---- live status / pending restart -----------------------------
        if running:
            engine = session.engine()
            tracker = session.tracker()
            box = layout.box()
            col = box.column(align=True)
            if engine is not None:
                col.label(text=engine.status, icon="HAND")
            if tracker is not None and tracker.fps:
                col.label(text=f"{tracker.fps:.0f} fps · camera "
                               f"{cfg.camera_index}", icon="CAMERA_DATA")
            if session.is_dirty():
                warn = box.column(align=True)
                warn.alert = True
                warn.label(text="Camera settings changed", icon="INFO")
                restart = box.row()
                restart.scale_y = 1.3
                restart.operator("hgc.restart", icon="FILE_REFRESH",
                                 text="Apply & Restart Tracking")

        # ---- errors when stopped ---------------------------------------
        error = session.last_error()
        if error and not running:
            box = layout.box()
            box.alert = True
            box.label(text="Tracking stopped", icon="ERROR")
            _draw_wrapped(box.column(align=True),
                          error.strip().splitlines()[-1], icon=None)

        # ---- selection guidance ----------------------------------------
        info = layout.box()
        if selected:
            info.label(
                text=(f"{selected} object selected" if selected == 1
                      else f"{selected} objects selected"),
                icon="OBJECT_DATA")
            if not running:
                tip = info.column(align=True)
                tip.scale_y = 0.85
                tip.label(text="Start tracking, then pick (all fingers) to move.")
        else:
            col = info.column(align=True)
            col.label(text="No object selected", icon="INFO")
            tip = info.column(align=True)
            tip.scale_y = 0.85
            if cfg.use_point_select:
                tip.label(text="Select in the viewport, or start tracking")
                tip.label(text="and point with your index finger to pick.")
            else:
                tip.label(text="Select an object in the viewport first.")

        # ---- always-visible gesture map (first-run discoverability) ----
        if not running:
            _draw_gesture_map(layout, title="Gestures", show_hint=True)
        else:
            # Compact reminder while live — full map is one click away.
            row = layout.row()
            row.scale_y = 0.9
            row.label(text="pick=move+rotate · two hands=scale",
                      icon="HAND")

    def _draw_setup(self, layout, status):
        box = layout.box()
        box.label(text="Setup required", icon="ERROR")
        col = box.column(align=True)

        def row(label, value, ok):
            line = col.row()
            line.label(text=f"{label}: {value}",
                       icon="CHECKMARK" if ok else "X")

        row("MediaPipe", status["mediapipe"] or "not installed",
            bool(status["mediapipe"]))
        row("OpenCV", status["cv2"] or "not installed", bool(status["cv2"]))
        row("NumPy", status["numpy"] or "not installed",
            bool(status["numpy"]))
        row("Model", "downloaded" if status["model"] else "missing",
            status["model"])

        if not deps.online_access_ok():
            warn = box.column()
            warn.alert = True
            warn.label(text="Blender's online access is disabled",
                       icon="INTERNET")
            warn.label(text="Preferences → System → Network")

        actions = box.column(align=True)
        actions.scale_y = 1.3
        actions.enabled = deps.online_access_ok()
        if (not status["mediapipe"] or not status["cv2"]
                or not status["numpy"]):
            actions.operator("hgc.install_dependencies",
                             icon="IMPORT", text="Install Dependencies")
        if not status["model"]:
            actions.operator("hgc.download_model", icon="URL",
                             text="Download Hand Model")
        box.operator("hgc.refresh_status", icon="FILE_REFRESH",
                     text="Re-check")

        if status["errors"]:
            problems = box.column(align=True)
            problems.alert = True
            _draw_wrapped(problems, status["errors"][0], width=44,
                          icon="ERROR")

        note = box.column(align=True)
        note.scale_y = 0.8
        note.label(text="Installation takes a few minutes and about 400 MB.")
        note.label(text="Watch the system console for progress.")


class HGC_PT_gestures(_Base, Panel):
    bl_label = "Gestures"
    bl_parent_id = "HGC_PT_main"
    # Open by default so the mapping is easy to find while tuning.

    @classmethod
    def poll(cls, context):
        return deps.check()["ready"]

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        cfg = settings_mod.get(context)

        _draw_gesture_map(layout, title="Gesture mapping", show_hint=False)

        col = layout.column(align=True)
        col.prop(cfg, "use_point_select")
        sub = col.row()
        sub.enabled = cfg.use_point_select
        sub.prop(cfg, "point_select_dwell")

        box = layout.box()
        box.label(text="Pick clutch", icon="OPTIONS")
        col = box.column(align=True)
        col.prop(cfg, "pinch_on", slider=True)
        col.prop(cfg, "pinch_off", slider=True)
        if cfg.pinch_off <= cfg.pinch_on:
            warn = col.row()
            warn.alert = True
            warn.label(text="'Opens' must exceed 'Closes'", icon="ERROR")
        tip = box.column(align=True)
        tip.scale_y = 0.8
        tip.label(text="Based on average thumb-to-tip distance for all")
        tip.label(text="four fingers. Widen the gap to stop flicker.")

        box = layout.box()
        box.label(text="Sensitivity", icon="DRIVER")
        col = box.column(align=True)
        col.prop(cfg, "move_sensitivity")
        col.prop(cfg, "rotate_sensitivity")
        col.prop(cfg, "scale_sensitivity")

        col = layout.column(align=True)
        col.prop(cfg, "use_depth")
        sub = col.row()
        sub.enabled = cfg.use_depth
        sub.prop(cfg, "depth_sensitivity")

        col = layout.column(align=True)
        col.prop(cfg, "use_two_hand")
        sub = col.row()
        sub.enabled = cfg.use_two_hand
        sub.prop(cfg, "two_hand_translate")

        _reset_button(layout, "GESTURES", "Reset Gesture Settings")


class HGC_PT_display(_Base, Panel):
    bl_label = "Visual Indicators"
    bl_parent_id = "HGC_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return deps.check()["ready"]

    def draw_header(self, context):
        self.layout.prop(settings_mod.get(context), "show_overlay", text="")

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        cfg = settings_mod.get(context)
        layout.enabled = cfg.show_overlay

        col = layout.column(align=True)
        col.prop(cfg, "overlay_detail", text="Detail")

        col = layout.column(align=True)
        col.prop(cfg, "show_skeleton")
        sub = col.column(align=True)
        sub.enabled = cfg.show_skeleton
        sub.prop(cfg, "landmark_size")
        sub.prop(cfg, "bone_width")
        sub.prop(cfg, "overlay_opacity", slider=True)

        col = layout.column(align=True)
        col.prop(cfg, "show_landmark_indices")
        col.prop(cfg, "show_hand_labels")
        col.prop(cfg, "show_hud")

        header = layout.row()
        header.prop(cfg, "show_preview")
        sub = layout.column(align=True)
        sub.enabled = cfg.show_preview
        sub.prop(cfg, "show_preview_landmarks")
        sub.prop(cfg, "preview_corner")
        sub.prop(cfg, "preview_size")
        sub.prop(cfg, "preview_margin")
        sub.prop(cfg, "preview_opacity", slider=True)

        legend = layout.box()
        legend.label(text="Joint colours", icon="COLOR")
        col = legend.column(align=True)
        col.scale_y = 0.75
        for name, hue in (("Palm / wrist", "white"), ("Thumb", "orange"),
                          ("Index", "yellow"), ("Middle", "green"),
                          ("Ring", "blue"), ("Pinky", "violet")):
            col.label(text=f"{name} — {hue}")

        _reset_button(layout, "DISPLAY", "Reset Visual Settings")


class HGC_PT_camera(_Base, Panel):
    bl_label = "Camera & Tracking"
    bl_parent_id = "HGC_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return deps.check()["ready"]

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        cfg = settings_mod.get(context)
        running = session.is_running()

        if running:
            note = layout.row()
            note.label(text="Stop or Apply & Restart to change camera",
                       icon="LOCKED")
            if session.is_dirty():
                row = layout.row()
                row.scale_y = 1.2
                row.operator("hgc.restart", icon="FILE_REFRESH",
                             text="Apply & Restart Tracking")

        col = layout.column(align=True)
        col.enabled = not running
        col.prop(cfg, "camera_choice")
        if cfg.camera_choice == "CUSTOM":
            col.prop(cfg, "camera_index")
        col.prop(cfg, "camera_backend")
        col.prop(cfg, "capture_preset")
        if cfg.capture_preset == "CUSTOM":
            col.prop(cfg, "capture_width")
            col.prop(cfg, "capture_height")
        col.prop(cfg, "num_hands")
        col.prop(cfg, "mirror")

        row = layout.row()
        row.enabled = not running
        row.operator("hgc.probe_cameras", icon="ZOOM_ALL")

        probe_message = session.camera_probe_message()
        if probe_message:
            probe = layout.box().column(align=True)
            _draw_wrapped(probe, probe_message, width=42, icon="CAMERA_DATA")
        elif not session.camera_list() and not running:
            tip = layout.column(align=True)
            tip.scale_y = 0.85
            tip.label(text="Tip: Detect Cameras if the wrong webcam opens.")

        box = layout.box()
        box.label(text="Model confidence", icon="SHADERFX")
        col = box.column(align=True)
        col.enabled = not running
        col.prop(cfg, "det_conf", slider=True)
        col.prop(cfg, "presence_conf", slider=True)
        col.prop(cfg, "track_conf", slider=True)
        help_row = box.column(align=True)
        help_row.scale_y = 0.8
        help_row.label(text="Raise if phantom hands appear; lower if dropouts.")

        box = layout.box()
        box.prop(cfg, "use_smoothing")
        col = box.column(align=True)
        col.enabled = cfg.use_smoothing
        col.prop(cfg, "smooth_min_cutoff")
        col.prop(cfg, "smooth_beta")
        help_row = box.column(align=True)
        help_row.scale_y = 0.8
        help_row.label(text="Still hand jitters → lower Steadiness.")
        help_row.label(text="Laggy drags → raise Responsiveness.")

        row = layout.row()
        row.enabled = not running
        row.prop(cfg, "update_rate")

        reset = _reset_button(layout, "TRACKING", "Reset Tracking Settings")
        reset.enabled = not running


classes = (
    HGC_PT_main,
    HGC_PT_gestures,
    HGC_PT_display,
    HGC_PT_camera,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
