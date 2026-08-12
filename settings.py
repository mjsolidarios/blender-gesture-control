# SPDX-License-Identifier: GPL-3.0-or-later
"""
Scene-level settings for the add-on, exposed in the N-panel.

Note the deliberate absence of ``from __future__ import annotations`` here.
Blender registers properties by reading the *values* of a class's annotations,
and PEP 563 would turn every one of them into a string, so registration would
fail with "expected a property definition". The same applies to any class that
declares ``bpy.props`` fields.
"""

import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty,
                       IntProperty)
from bpy.types import PropertyGroup


def _restart_note(self, context):
    """Flag settings that only take effect when the camera is reopened."""
    from . import session
    if session.is_running():
        session.mark_dirty()


def _pinch_close_changed(self, context):
    """Keep the hysteresis band valid when the close threshold moves."""
    if self.pinch_off <= self.pinch_on:
        self.pinch_off = min(self.pinch_on + 0.02, 1.50)


def _pinch_open_changed(self, context):
    """Keep the hysteresis band valid when the open threshold moves."""
    if self.pinch_off <= self.pinch_on:
        self.pinch_on = max(self.pinch_off - 0.02, 0.10)


class HGC_Settings(PropertyGroup):
    # -- camera ------------------------------------------------------------

    camera_index: IntProperty(
        name="Camera",
        description="Index of the capture device. 0 is the default webcam",
        default=0, min=0, max=15,
        update=_restart_note,
    )
    camera_backend: EnumProperty(
        name="Backend",
        description="Capture API. Leave on Auto unless the camera fails to "
                    "open or takes several seconds to start",
        items=(
            ("AUTO", "Auto", "Let OpenCV choose"),
            ("V4L2", "V4L2", "Video4Linux2 (Linux)"),
            ("DSHOW", "DirectShow", "DirectShow (Windows)"),
            ("MSMF", "Media Foundation", "Media Foundation (Windows)"),
            ("AVFOUNDATION", "AVFoundation", "AVFoundation (macOS)"),
            ("GSTREAMER", "GStreamer", "GStreamer"),
        ),
        default="AUTO",
        update=_restart_note,
    )
    capture_width: IntProperty(
        name="Capture Width", default=640, min=160, max=1920, step=32,
        description="Frame width requested from the camera. Larger frames "
                    "track small hand motions better but cost more per frame",
        update=_restart_note,
    )
    capture_height: IntProperty(
        name="Capture Height", default=480, min=120, max=1080, step=32,
        update=_restart_note,
    )
    num_hands: IntProperty(
        name="Max Hands", default=2, min=1, max=4,
        description="How many hands the model may track at once",
        update=_restart_note,
    )
    mirror: BoolProperty(
        name="Mirror View", default=True,
        description="Flip the image horizontally so it behaves like a mirror",
        update=_restart_note,
    )

    # -- detection ---------------------------------------------------------

    det_conf: FloatProperty(
        name="Detection", default=0.5, min=0.0, max=1.0, subtype="FACTOR",
        description="Minimum confidence for the palm detector to report a hand",
        update=_restart_note,
    )
    presence_conf: FloatProperty(
        name="Presence", default=0.5, min=0.0, max=1.0, subtype="FACTOR",
        description="Minimum confidence that a hand is still in frame",
        update=_restart_note,
    )
    track_conf: FloatProperty(
        name="Tracking", default=0.5, min=0.0, max=1.0, subtype="FACTOR",
        description="Minimum confidence to keep following a hand between "
                    "frames instead of re-detecting it",
        update=_restart_note,
    )

    # -- smoothing ---------------------------------------------------------

    use_smoothing: BoolProperty(
        name="Smooth Landmarks", default=True,
        description="Apply a One Euro filter to the landmark stream. Removes "
                    "tremble when the hand is still without adding lag when "
                    "it moves",
    )
    smooth_min_cutoff: FloatProperty(
        name="Steadiness", default=1.2, min=0.05, max=10.0,
        description="Lower values smooth harder while the hand is still",
    )
    smooth_beta: FloatProperty(
        name="Responsiveness", default=0.02, min=0.0, max=1.0,
        description="Higher values let fast motion through with less lag",
    )

    # -- gesture -----------------------------------------------------------

    pinch_on: FloatProperty(
        name="Pinch Closes", default=0.40, min=0.10, max=1.20,
        description="Thumb-to-fingertip distance, as a fraction of hand size, "
                    "at which a pinch grabs",
        update=_pinch_close_changed,
    )
    pinch_off: FloatProperty(
        name="Pinch Opens", default=0.58, min=0.12, max=1.50,
        description="Distance at which the pinch lets go. Keeping this above "
                    "'Pinch Closes' stops the grip flickering",
        update=_pinch_open_changed,
    )
    move_sensitivity: FloatProperty(
        name="Move", default=1.0, min=0.05, max=5.0,
        description="Screen distance travelled per unit of hand movement",
    )
    use_depth: BoolProperty(
        name="Depth From Hand Size", default=True,
        description="Move the object along the view axis as the hand moves "
                    "toward or away from the camera",
    )
    depth_sensitivity: FloatProperty(
        name="Depth", default=1.0, min=0.0, max=5.0,
    )
    rotate_sensitivity: FloatProperty(
        name="Rotate", default=1.0, min=0.05, max=5.0,
        description="Multiplier on the rotation copied from your hand",
    )
    scale_sensitivity: FloatProperty(
        name="Scale", default=1.5, min=0.05, max=8.0,
    )
    use_two_hand: BoolProperty(
        name="Two-Handed Gestures", default=True,
        description="Pinch with both index fingers to scale and roll at once",
    )
    two_hand_translate: BoolProperty(
        name="Two-Handed Move", default=True,
        description="Also translate when both hands move together",
    )

    # -- display -----------------------------------------------------------

    show_overlay: BoolProperty(
        name="Show Overlay", default=True,
        description="Master switch for everything drawn in the viewport",
    )
    show_skeleton: BoolProperty(
        name="Hand Skeleton", default=True,
        description="Draw all 21 landmarks and the bones between them over "
                    "the viewport",
    )
    show_preview: BoolProperty(
        name="Camera Preview", default=True,
        description="Show the live camera image in a corner of the viewport",
    )
    show_preview_landmarks: BoolProperty(
        name="Landmarks On Preview", default=True,
        description="Also draw the skeleton inside the camera preview",
    )
    show_landmark_indices: BoolProperty(
        name="Landmark Numbers", default=False,
        description="Label each joint with its MediaPipe index, 0 to 20",
    )
    show_hand_labels: BoolProperty(
        name="Hand Labels", default=True,
        description="Show Left/Right and the classifier's confidence",
    )
    show_hud: BoolProperty(
        name="Status Readout", default=True,
    )
    landmark_size: FloatProperty(
        name="Joint Size", default=5.0, min=1.0, max=20.0, subtype="PIXEL",
    )
    bone_width: FloatProperty(
        name="Bone Width", default=2.5, min=0.5, max=12.0, subtype="PIXEL",
    )
    overlay_opacity: FloatProperty(
        name="Opacity", default=0.65, min=0.05, max=1.0, subtype="FACTOR",
    )
    preview_size: IntProperty(
        name="Preview Width", default=320, min=120, max=960, subtype="PIXEL",
    )
    preview_margin: IntProperty(
        name="Margin", default=16, min=0, max=200, subtype="PIXEL",
    )
    preview_opacity: FloatProperty(
        name="Preview Opacity", default=0.95, min=0.1, max=1.0,
        subtype="FACTOR",
    )
    preview_corner: EnumProperty(
        name="Corner",
        items=(
            ("BOTTOM_LEFT", "Bottom Left", ""),
            ("BOTTOM_RIGHT", "Bottom Right", ""),
            ("TOP_LEFT", "Top Left", ""),
            ("TOP_RIGHT", "Top Right", ""),
        ),
        default="BOTTOM_RIGHT",
    )

    # -- performance -------------------------------------------------------

    update_rate: IntProperty(
        name="Update Rate", default=60, min=10, max=120,
        description="How often per second Blender polls the tracker and "
                    "redraws the overlay",
        update=_restart_note,
    )


SETTING_GROUPS = {
    "GESTURES": (
        "pinch_on", "pinch_off", "move_sensitivity", "use_depth",
        "depth_sensitivity", "rotate_sensitivity", "scale_sensitivity",
        "use_two_hand", "two_hand_translate",
    ),
    "DISPLAY": (
        "show_overlay", "show_skeleton", "show_preview",
        "show_preview_landmarks", "show_landmark_indices",
        "show_hand_labels", "show_hud", "landmark_size", "bone_width",
        "overlay_opacity", "preview_size", "preview_margin",
        "preview_opacity", "preview_corner",
    ),
    "TRACKING": (
        "camera_index", "camera_backend", "capture_width",
        "capture_height", "num_hands", "mirror", "det_conf",
        "presence_conf", "track_conf", "use_smoothing",
        "smooth_min_cutoff", "smooth_beta", "update_rate",
    ),
}


def reset_group(settings, group: str):
    """Restore one UI section to its RNA-declared defaults."""
    for name in SETTING_GROUPS.get(group, ()):
        settings.property_unset(name)


def get(context) -> HGC_Settings:
    return context.scene.hgc_settings


classes = (HGC_Settings,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.hgc_settings = bpy.props.PointerProperty(type=HGC_Settings)


def unregister():
    if hasattr(bpy.types.Scene, "hgc_settings"):
        del bpy.types.Scene.hgc_settings
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
