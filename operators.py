# SPDX-License-Identifier: GPL-3.0-or-later
"""
Operators: dependency setup, and the modal loop that drives tracking.

``from __future__ import annotations`` is intentionally not used in this module
because ``HGC_OT_install_dependencies`` declares a ``bpy.props`` field, and PEP
563 would stringify it and break registration.
"""

import time

import bpy
from bpy.props import BoolProperty, EnumProperty
from bpy.types import Operator

from . import deps, session, settings as settings_mod
from .filters import LandmarkSmoother
from .gestures import GestureEngine
from .overlay import overlay
from .tracker import HandTracker, probe_cameras

# ---------------------------------------------------------------------------
# Dependency management
# ---------------------------------------------------------------------------


class HGC_OT_install_dependencies(Operator):
    bl_idname = "hgc.install_dependencies"
    bl_label = "Install Dependencies"
    bl_description = ("Download and install MediaPipe and OpenCV into a "
                      "private folder for this add-on. Needs an internet "
                      "connection and roughly 400 MB of disk space")
    bl_options = {"REGISTER", "INTERNAL"}

    upgrade: BoolProperty(name="Upgrade", default=False)

    def execute(self, context):
        if not deps.online_access_ok():
            self.report({"ERROR"},
                        "Blender's online access is off. Enable it in "
                        "Preferences > System > Network, then try again")
            return {"CANCELLED"}

        lines = []

        def log(text):
            lines.append(str(text))
            print("[HandGestureControl]", text)

        wm = context.window_manager
        wm.progress_begin(0, 1)
        try:
            ok = deps.install(log=log, upgrade=self.upgrade)
        finally:
            wm.progress_end()

        deps.invalidate()
        status = deps.check(force=True)

        if (ok and status["mediapipe"] and status["cv2"]
                and status["numpy"]):
            self.report({"INFO"},
                        f"Installed mediapipe {status['mediapipe']}, "
                        f"opencv {status['cv2']}")
            return {"FINISHED"}

        tail = " | ".join(lines[-3:]) if lines else "see the system console"
        self.report({"ERROR"}, f"Install failed: {tail}")
        return {"CANCELLED"}


class HGC_OT_download_model(Operator):
    bl_idname = "hgc.download_model"
    bl_label = "Download Model"
    bl_description = ("Fetch Google's hand_landmarker.task bundle, about 7 MB")
    bl_options = {"REGISTER", "INTERNAL"}

    def execute(self, context):
        if not deps.online_access_ok():
            self.report({"ERROR"},
                        "Blender's online access is off. Enable it in "
                        "Preferences > System > Network, then try again")
            return {"CANCELLED"}

        messages = []

        def log(text):
            messages.append(str(text))
            print("[HandGestureControl]", text)

        ok = deps.download_model(log=log)
        deps.check(force=True)
        if ok:
            self.report({"INFO"}, f"Model saved to {deps.model_path()}")
            return {"FINISHED"}
        self.report({"ERROR"}, messages[-1] if messages else "download failed")
        return {"CANCELLED"}


class HGC_OT_uninstall_dependencies(Operator):
    bl_idname = "hgc.uninstall_dependencies"
    bl_label = "Remove Dependencies"
    bl_description = "Delete the installed MediaPipe and OpenCV packages"
    bl_options = {"REGISTER", "INTERNAL"}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        if session.is_running():
            self.report({"ERROR"}, "Stop tracking first")
            return {"CANCELLED"}
        ok = deps.uninstall(log=lambda t: print("[HandGestureControl]", t))
        deps.check(force=True)
        self.report({"INFO"} if ok else {"ERROR"},
                    "Removed" if ok else "Could not remove the folder")
        return {"FINISHED"} if ok else {"CANCELLED"}


class HGC_OT_refresh_status(Operator):
    bl_idname = "hgc.refresh_status"
    bl_label = "Re-check"
    bl_description = "Re-test whether the dependencies import correctly"
    bl_options = {"REGISTER", "INTERNAL"}

    def execute(self, context):
        status = deps.check(force=True)
        self.report({"INFO"},
                    "Ready" if status["ready"] else
                    "; ".join(status["errors"]) or "Model missing")
        return {"FINISHED"}


class HGC_OT_probe_cameras(Operator):
    bl_idname = "hgc.probe_cameras"
    bl_label = "Detect Cameras"
    bl_description = ("Try camera indices 0-5 and show which respond. This "
                      "may take a few seconds")
    bl_options = {"REGISTER", "INTERNAL"}

    def execute(self, context):
        if session.is_running():
            self.report({"ERROR"}, "Stop tracking first")
            return {"CANCELLED"}
        found = probe_cameras()
        if not found:
            session.set_camera_list([])
            session.set_camera_probe_message(
                "No cameras found. Close other camera apps and try again.")
            self.report({"WARNING"}, "No cameras responded")
            return {"CANCELLED"}

        session.set_camera_list(found)
        cfg = settings_mod.get(context)
        indices = {index for index, _ in found}
        selected = ""
        if cfg.camera_index not in indices:
            cfg.camera_index = found[0][0]
            selected = f" Selected camera {cfg.camera_index}."
        else:
            # Refresh the picker label even when the index is unchanged.
            cfg.camera_index = cfg.camera_index
        message = "Found " + ", ".join(desc for _, desc in found) + selected
        session.set_camera_probe_message(message)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class HGC_OT_reset_settings(Operator):
    bl_idname = "hgc.reset_settings"
    bl_label = "Reset Settings"
    bl_description = "Restore this section to its recommended defaults"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    group: EnumProperty(
        items=(
            ("GESTURES", "Gestures", "Reset gesture controls"),
            ("DISPLAY", "Display", "Reset visual indicators"),
            ("TRACKING", "Tracking", "Reset camera and tracking controls"),
        ),
        default="GESTURES",
        options={"HIDDEN"},
    )

    def execute(self, context):
        if self.group == "TRACKING" and session.is_running():
            self.report({"ERROR"},
                        "Stop tracking before resetting camera settings")
            return {"CANCELLED"}
        settings_mod.reset_group(settings_mod.get(context), self.group)
        self.report({"INFO"}, f"Reset {self.group.lower()} settings")
        overlay.tag_redraw()
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# The tracking session
# ---------------------------------------------------------------------------


def _stop_tracking():
    """Tear down the live session without requiring an operator instance."""
    engine = session.engine()
    if engine is not None:
        try:
            engine.end_session()
        except Exception:
            pass
    session.shutdown()
    overlay.disable()
    overlay.tag_redraw()


class HGC_OT_stop(Operator):
    bl_idname = "hgc.stop"
    bl_label = "Stop Hand Tracking"
    bl_description = "Release the camera and remove the viewport overlay"

    def execute(self, context):
        _stop_tracking()
        self.report({"INFO"}, "Hand tracking stopped")
        return {"FINISHED"}


class HGC_OT_restart(Operator):
    bl_idname = "hgc.restart"
    bl_label = "Restart Tracking"
    bl_description = ("Stop and start tracking again so pending camera "
                      "settings take effect")

    @classmethod
    def poll(cls, context):
        return (context.area is not None
                and context.area.type == "VIEW_3D"
                and session.is_running())

    def execute(self, context):
        _stop_tracking()
        # invoke() on start runs the full camera setup path.
        return bpy.ops.hgc.start("INVOKE_DEFAULT")


class HGC_OT_start(Operator):
    bl_idname = "hgc.start"
    bl_label = "Start Hand Tracking"
    bl_description = ("Open the camera and begin controlling objects with "
                      "hand gestures. Pinch to grab; Esc stops")

    _timer = None
    _area = None
    _smooth_image = None
    _smooth_world = None
    _last_frame_id = -1
    _generation = 0

    @classmethod
    def poll(cls, context):
        return (context.area is not None
                and context.area.type == "VIEW_3D"
                and not session.is_running())

    # -- setup -------------------------------------------------------------

    def invoke(self, context, event):
        if session.is_running():
            self.report({"WARNING"}, "Tracking is already running")
            return {"CANCELLED"}

        status = deps.check(force=True)
        if (not status["mediapipe"] or not status["cv2"]
                or not status["numpy"]):
            self.report({"ERROR"},
                        "A runtime package is missing. Use Install "
                        "Dependencies in this panel or in Preferences")
            return {"CANCELLED"}
        if not status["model"]:
            self.report({"ERROR"},
                        "Hand model is missing. Use Download Hand Model in "
                        "this panel or in Preferences")
            return {"CANCELLED"}

        cfg = settings_mod.get(context)

        tracker = HandTracker(
            camera_index=cfg.camera_index,
            backend=cfg.camera_backend,
            num_hands=cfg.num_hands,
            det_conf=cfg.det_conf,
            presence_conf=cfg.presence_conf,
            track_conf=cfg.track_conf,
            capture_width=cfg.capture_width,
            capture_height=cfg.capture_height,
            preview_width=cfg.preview_size,
            want_preview=cfg.show_preview,
            mirror=cfg.mirror,
        )

        if not tracker.start():
            message = (tracker.error or "unknown error").strip().splitlines()
            session.set_error(tracker.error or "")
            tracker.stop()
            self.report({"ERROR"}, f"Camera failed: {message[-1][:180]}")
            return {"CANCELLED"}

        engine = GestureEngine()
        self._generation = session.set_active(tracker, engine)
        session.set_error("")

        self._smooth_image = LandmarkSmoother(num_hands=4)
        self._smooth_world = LandmarkSmoother(num_hands=4)
        self._last_frame_id = -1
        self._area = context.area

        overlay.engine = engine
        overlay.settings = cfg
        overlay.target_area = context.area
        overlay.error_text = ""
        overlay.status_text = "Show your hand to the camera"
        overlay.enable()

        wm = context.window_manager
        self._timer = wm.event_timer_add(1.0 / max(cfg.update_rate, 10),
                                         window=context.window)
        wm.modal_handler_add(self)
        overlay.tag_redraw()
        self.report({"INFO"}, "Hand tracking started — press Esc to stop")
        return {"RUNNING_MODAL"}

    # -- loop --------------------------------------------------------------

    def modal(self, context, event):
        # A panel Stop/Restart (or a newer Start) may have superseded us.
        if session.generation() != self._generation:
            self._release_timer(context)
            return {"CANCELLED"}

        if event.type in {"ESC"} and event.value == "PRESS":
            self._finish(context)
            return {"CANCELLED"}

        if event.type != "TIMER":
            # Everything else belongs to Blender: navigation, selection, tools.
            return {"PASS_THROUGH"}

        tracker = session.tracker()
        engine = session.engine()
        if tracker is None or engine is None:
            self._finish(context)
            return {"CANCELLED"}

        if tracker.error:
            overlay.error_text = tracker.error.strip().splitlines()[-1][:160]
            session.set_error(tracker.error)
            self._finish(context)
            return {"CANCELLED"}

        if not tracker.running:
            self._finish(context)
            return {"CANCELLED"}

        try:
            self._step(context, tracker, engine)
        except Exception as exc:                   # pragma: no cover
            message = f"Tracking update failed: {exc}"
            session.set_error(message)
            self.report({"ERROR"}, message[:180])
            self._finish(context)
            return {"CANCELLED"}

        overlay.tag_redraw()
        return {"PASS_THROUGH"}

    def _step(self, context, tracker, engine):
        cfg = settings_mod.get(context)
        overlay.settings = cfg

        # Preview visibility and size can change live. The capture thread only
        # reads these scalar values, so no camera restart is needed.
        tracker.want_preview = bool(cfg.show_preview)
        tracker.preview_width = int(cfg.preview_size)

        snapshot = tracker.latest()
        overlay.fps = tracker.fps

        fresh = snapshot.frame_id != self._last_frame_id
        self._last_frame_id = snapshot.frame_id

        if cfg.use_smoothing and snapshot.hands and fresh:
            self._smooth_image.configure(cfg.smooth_min_cutoff, cfg.smooth_beta)
            self._smooth_world.configure(cfg.smooth_min_cutoff, cfg.smooth_beta)
            now = time.monotonic()
            for hand in snapshot.hands:
                hand.image_pts = _unflatten(self._smooth_image.filter(
                    hand.slot, _flatten(hand.image_pts), now))
                hand.world_pts = _unflatten(self._smooth_world.filter(
                    hand.slot, _flatten(hand.world_pts), now))
        elif not snapshot.hands:
            self._smooth_image.reset()
            self._smooth_world.reset()

        overlay.snapshot = snapshot

        region, rv3d = self._view(context)
        engine.update(context, region, rv3d, snapshot, cfg)
        if engine.last_error:
            overlay.error_text = f"Gesture error: {engine.last_error}"[:160]
            engine.last_error = ""

        parts = [engine.status]
        if session.is_dirty():
            parts.append("restart to apply camera settings")
        overlay.status_text = "  |  ".join(parts)

    def _view(self, context):
        """The region and view matrix of the viewport this session owns."""
        area = self._area
        if area is None:
            return None, None
        try:
            space = area.spaces.active
        except (AttributeError, ReferenceError):
            return None, None
        if space is None or space.type != "VIEW_3D":
            return None, None

        region = None
        for candidate in area.regions:
            if candidate.type == "WINDOW":
                region = candidate
                break
        return region, getattr(space, "region_3d", None)

    # -- teardown ----------------------------------------------------------

    def _release_timer(self, context):
        wm = context.window_manager
        if self._timer is not None:
            try:
                wm.event_timer_remove(self._timer)
            except Exception:
                pass
            self._timer = None

    def _finish(self, context):
        self._release_timer(context)
        # Only tear the session down if we still own it.
        if session.generation() == self._generation:
            _stop_tracking()

    def cancel(self, context):
        self._finish(context)


def _flatten(points):
    out = []
    for p in points:
        out.append(p[0])
        out.append(p[1])
        out.append(p[2])
    return out


def _unflatten(values):
    return [(values[i], values[i + 1], values[i + 2])
            for i in range(0, len(values), 3)]


classes = (
    HGC_OT_install_dependencies,
    HGC_OT_download_model,
    HGC_OT_uninstall_dependencies,
    HGC_OT_refresh_status,
    HGC_OT_probe_cameras,
    HGC_OT_reset_settings,
    HGC_OT_start,
    HGC_OT_stop,
    HGC_OT_restart,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
