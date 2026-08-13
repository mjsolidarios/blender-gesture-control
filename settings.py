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


# Capture resolution presets: (width, height).
_CAPTURE_PRESETS = {
    "LOW": (320, 240),
    "MEDIUM": (640, 480),
    "HIGH": (1280, 720),
}


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


def _capture_preset_changed(self, context):
    """Apply a named resolution preset, or leave custom dimensions alone."""
    size = _CAPTURE_PRESETS.get(self.capture_preset)
    if size is not None:
        width, height = size
        if self.capture_width != width:
            self.capture_width = width
        if self.capture_height != height:
            self.capture_height = height
    _restart_note(self, context)


def _capture_size_changed(self, context):
    """Keep the preset enum in sync when width/height are edited directly."""
    matched = "CUSTOM"
    for key, (width, height) in _CAPTURE_PRESETS.items():
        if self.capture_width == width and self.capture_height == height:
            matched = key
            break
    if self.capture_preset != matched:
        # Avoid re-entering the preset callback with a no-op assignment.
        self["capture_preset"] = matched
    _restart_note(self, context)


def _camera_items(self, context):
    """Dynamic camera list from the last Detect Cameras probe."""
    from . import session
    found = session.camera_list()
    if found:
        items = [(str(index), desc, f"Use camera index {index}")
                 for index, desc in found]
    else:
        items = [(str(i), f"Camera {i}", f"Device index {i}")
                 for i in range(6)]
    items.append(("CUSTOM", "Other index…",
                  "Type a camera index manually"))
    return items


def _camera_choice_changed(self, context):
    if self.camera_choice != "CUSTOM":
        try:
            index = int(self.camera_choice)
        except ValueError:
            index = 0
        if self.camera_index != index:
            self.camera_index = index
    _restart_note(self, context)


def _camera_index_changed(self, context):
    """Keep the picker enum aligned with the numeric index."""
    from . import session
    found = {str(index) for index, _ in session.camera_list()}
    key = str(self.camera_index)
    if found and key not in found:
        key = "CUSTOM"
    elif not found and self.camera_index > 5:
        key = "CUSTOM"
    if self.camera_choice != key:
        self["camera_choice"] = key
    _restart_note(self, context)


def _overlay_detail_changed(self, context):
    """Apply a display density preset without wiping custom preview layout."""
    if self.overlay_detail == "MINIMAL":
        self.show_skeleton = True
        self.show_landmark_indices = False
        self.show_hand_labels = False
        self.show_hud = True
        self.overlay_opacity = 0.45
        self.landmark_size = 4.0
        self.bone_width = 1.5
    elif self.overlay_detail == "FULL":
        self.show_skeleton = True
        self.show_landmark_indices = False
        self.show_hand_labels = True
        self.show_hud = True
        self.overlay_opacity = 0.65
        self.landmark_size = 5.0
        self.bone_width = 2.5


def _preset_changed(self, context):
    """Apply a named preset profile."""
    if self.preset_profile == "PRECISE":
        self.move_sensitivity = 0.5
        self.rotate_sensitivity = 0.6
        self.scale_sensitivity = 1.0
        self.pinch_on = 0.40
        self.pinch_off = 0.60
        self.dead_zone = 0.05
        self.use_velocity_curve = True
        self.smooth_min_cutoff = 0.8
        self.smooth_beta = 0.01
    elif self.preset_profile == "FAST":
        self.move_sensitivity = 2.0
        self.rotate_sensitivity = 1.5
        self.scale_sensitivity = 2.5
        self.pinch_on = 0.55
        self.pinch_off = 0.75
        self.dead_zone = 0.01
        self.use_velocity_curve = True
        self.smooth_min_cutoff = 2.0
        self.smooth_beta = 0.05
    elif self.preset_profile == "PRESENTATION":
        self.move_sensitivity = 1.2
        self.rotate_sensitivity = 1.0
        self.scale_sensitivity = 1.5
        self.pinch_on = 0.50
        self.pinch_off = 0.70
        self.dead_zone = 0.04
        self.use_velocity_curve = True
        self.hand_loss_grace = 1.0
        self.smooth_min_cutoff = 1.0
        self.smooth_beta = 0.02


class HGC_Settings(PropertyGroup):
    # -- camera ------------------------------------------------------------

    camera_choice: EnumProperty(
        name="Camera",
        description="Capture device. Press Detect Cameras to list devices "
                    "that respond on this machine",
        items=_camera_items,
        update=_camera_choice_changed,
    )
    camera_index: IntProperty(
        name="Camera Index",
        description="Index of the capture device. 0 is usually the default "
                    "webcam",
        default=0, min=0, max=15,
        update=_camera_index_changed,
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
    capture_preset: EnumProperty(
        name="Resolution",
        description="Capture size. Lower is faster; higher tracks small "
                    "motions better",
        items=(
            ("LOW", "320 × 240 (Fast)", "Lowest cost, coarser tracking"),
            ("MEDIUM", "640 × 480 (Default)", "Balanced speed and accuracy"),
            ("HIGH", "1280 × 720 (Detail)", "Best for small hand motions"),
            ("CUSTOM", "Custom", "Set width and height manually"),
        ),
        default="MEDIUM",
        update=_capture_preset_changed,
    )
    capture_width: IntProperty(
        name="Capture Width", default=640, min=160, max=1920, step=32,
        description="Frame width requested from the camera. Larger frames "
                    "track small hand motions better but cost more per frame",
        update=_capture_size_changed,
    )
    capture_height: IntProperty(
        name="Capture Height", default=480, min=120, max=1080, step=32,
        update=_capture_size_changed,
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
        name="Pick Closes", default=0.48, min=0.10, max=1.20,
        description="Average thumb-to-fingertip distance (index, middle, ring, "
                    "pinky), as a fraction of hand size, at which the pick grab "
                    "engages",
        update=_pinch_close_changed,
    )
    pinch_off: FloatProperty(
        name="Pick Opens", default=0.68, min=0.12, max=1.50,
        description="Average distance at which the pick lets go. Keep this "
                    "above 'Pick Closes' to stop the grip flickering",
        update=_pinch_open_changed,
    )
    move_sensitivity: FloatProperty(
        name="Move", default=1.0, min=0.05, max=5.0,
        description="Screen distance travelled per unit of hand movement "
                    "while picking",
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
        description="Multiplier on rotation copied from hand twist while "
                    "picking",
    )
    scale_sensitivity: FloatProperty(
        name="Scale", default=1.5, min=0.05, max=8.0,
        description="Multiplier on two-handed scale (spread / close hands)",
    )
    use_two_hand: BoolProperty(
        name="Two-Handed Scale", default=True,
        description="Pick with both hands to scale (spread or close). "
                    "Single-hand scale is not available",
    )
    two_hand_translate: BoolProperty(
        name="Two-Handed Move", default=True,
        description="Also translate when both picking hands move together",
    )
    use_point_select: BoolProperty(
        name="Point to Select", default=True,
        description="Extend only the index finger and hold it over an object "
                    "to toggle its selection (adds to the selection or removes it). "
                    "Supports selecting multiple objects and deselecting.",
    )
    point_select_dwell: FloatProperty(
        name="Selection Dwell", default=0.6, min=0.1, max=2.0,
        subtype="TIME",
        description="How long the index pointer must stay over an object "
                    "before toggling its selection",
    )
    use_sticky_pick: BoolProperty(
        name="Sticky Pick",
        default=False,
        description="Quick pick-and-release locks the grab on; a second "
                    "pick-and-release unlocks it. Reduces hand fatigue",
    )
    dead_zone: FloatProperty(
        name="Dead Zone",
        default=0.03,
        min=0.0,
        max=0.15,
        description="Normalised distance the hand must move after a pick "
                    "engages before the object starts following. Prevents "
                    "the initial grab jitter",
    )
    use_velocity_curve: BoolProperty(
        name="Velocity Curve",
        default=True,
        description="Slow hand motion maps to fine object movement; fast "
                    "motion maps to larger translations. Replaces the need "
                    "to constantly adjust the sensitivity slider",
    )
    use_single_hand_scale: BoolProperty(
        name="Single-Hand Scale",
        default=False,
        description="Spread thumb and index finger apart to scale with one "
                    "hand (like a phone zoom gesture)",
    )
    single_hand_scale_sensitivity: FloatProperty(
        name="Single Scale",
        default=2.0,
        min=0.1,
        max=10.0,
        description="Multiplier for single-hand thumb-index scale gesture",
    )
    use_snap_select: BoolProperty(
        name="Snap to Nearest",
        default=True,
        description="Magnetise the pointing ray to the nearest object "
                    "within a small threshold when pointing to select",
    )
    snap_select_radius: FloatProperty(
        name="Snap Radius",
        default=30.0,
        min=5.0,
        max=100.0,
        subtype="PIXEL",
        description="Pixel radius within which the pointer snaps to the "
                    "nearest selectable object",
    )
    use_tap_select: BoolProperty(
        name="Tap to Select",
        default=False,
        description="Curl the index finger while pointing to instantly "
                    "select, instead of waiting for the dwell timer",
    )
    hand_loss_grace: FloatProperty(
        name="Hand Loss Grace",
        default=0.5,
        min=0.0,
        max=2.0,
        subtype="TIME",
        description="Seconds to hold the last object state when the hand "
                    "disappears mid-gesture before dropping the transform",
    )
    preset_profile: EnumProperty(
        name="Preset",
        description="Bundles of sensitivity, smoothing and threshold "
                    "values for common workflows",
        items=(
            ("CUSTOM", "Custom", "Current manual settings"),
            ("PRECISE", "Precise Modeling", "Low sensitivity, tight thresholds"),
            ("FAST", "Fast Layout", "High sensitivity, loose thresholds"),
            ("PRESENTATION", "Presentation", "Balanced, generous grace period"),
        ),
        default="CUSTOM",
        update=_preset_changed,
    )

    # -- display -----------------------------------------------------------

    show_overlay: BoolProperty(
        name="Show Overlay", default=True,
        description="Master switch for everything drawn in the viewport",
    )
    overlay_detail: EnumProperty(
        name="Overlay Detail",
        description="How much hand feedback to draw in the viewport",
        items=(
            ("FULL", "Full", "All joints, bones, labels and pick cues"),
            ("MINIMAL", "Minimal", "Key joints, pick rings and status only"),
        ),
        default="FULL",
        update=_overlay_detail_changed,
    )
    show_skeleton: BoolProperty(
        name="Hand Skeleton", default=True,
        description="Draw hand landmarks and the bones between them over "
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
    use_audio_cue: BoolProperty(
        name="Audio Cue",
        default=False,
        description="Play a subtle click sound when the pick grab "
                    "engages or disengages",
    )
    tutorial_shown: BoolProperty(
        name="Tutorial Shown",
        default=False,
        description="Whether the interactive tutorial has been displayed",
        options={"HIDDEN"},
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
        "use_two_hand", "two_hand_translate", "use_point_select",
        "point_select_dwell", "use_sticky_pick", "dead_zone",
        "use_velocity_curve", "use_single_hand_scale",
        "single_hand_scale_sensitivity", "use_snap_select",
        "snap_select_radius", "use_tap_select", "hand_loss_grace",
        "preset_profile",
    ),
    "DISPLAY": (
        "show_overlay", "overlay_detail", "show_skeleton", "show_preview",
        "show_preview_landmarks", "show_landmark_indices",
        "show_hand_labels", "show_hud", "landmark_size", "bone_width",
        "overlay_opacity", "preview_size", "preview_margin",
        "preview_opacity", "preview_corner", "use_audio_cue",
        "tutorial_shown",
    ),
    "TRACKING": (
        "camera_index", "camera_backend", "capture_preset",
        "capture_width", "capture_height", "num_hands", "mirror",
        "det_conf", "presence_conf", "track_conf", "use_smoothing",
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
