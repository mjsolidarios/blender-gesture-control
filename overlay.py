# SPDX-License-Identifier: GPL-3.0-or-later
"""
Viewport drawing: the 21-landmark skeleton and the live camera preview.

All drawing happens in a ``POST_PIXEL`` handler on ``SpaceView3D``, which runs
after the scene is rendered and works in region pixel coordinates. That suits
hand landmarks well, since MediaPipe reports them in normalised image space and
the gestures themselves act in screen space.

Every landmark is drawn, not just the ones a gesture reads, so the user can see
exactly what the model is tracking: if a finger is being mis-detected it shows
up immediately as a joint in the wrong place. Joints are coloured by finger,
sized by role, and can be labelled with their MediaPipe index.

Geometry is rebuilt each frame rather than cached. At 21 joints and 21 bones
per hand this is a few hundred vertices, which costs far less than the
bookkeeping needed to invalidate a cache correctly.
"""

from __future__ import annotations

import math

import blf
import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from . import landmarks as LM
from .gestures import (MODE_GRAB, MODE_LABELS, MODE_NONE, MODE_TWO_HAND,
                       palm_center, to_region)

# ---------------------------------------------------------------------------
# Shader helpers
# ---------------------------------------------------------------------------

_shader_cache = {}


def _shader(name: str):
    shader = _shader_cache.get(name)
    if shader is None:
        shader = gpu.shader.from_builtin(name)
        _shader_cache[name] = shader
    return shader


def _fit(points):
    """
    Expand screen-space 2D points to the vec3 the built-in shaders declare.

    Every built-in colour shader declares ``pos`` as VEC3. Blender will pad a
    2-component list silently, but emitting the third component explicitly
    keeps the intent obvious and avoids relying on that convenience.
    """
    return [(p[0], p[1], 0.0) for p in points]


def _set_font_size(font_id: int, size: float):
    """``blf.size`` lost its dpi argument in Blender 4.0."""
    try:
        blf.size(font_id, size)
    except TypeError:                              # pragma: no cover
        blf.size(font_id, size, 72)


# Unit circle, reused for every disc.
_CIRCLE_SEGMENTS = 14
_UNIT_CIRCLE = [
    (math.cos(i * 2.0 * math.pi / _CIRCLE_SEGMENTS),
     math.sin(i * 2.0 * math.pi / _CIRCLE_SEGMENTS))
    for i in range(_CIRCLE_SEGMENTS)
]


def _append_disc(verts, colors, center, radius, color):
    """Emit a filled circle as a triangle fan flattened into a TRIS list."""
    cx, cy = center[0], center[1]
    for i in range(_CIRCLE_SEGMENTS):
        ax, ay = _UNIT_CIRCLE[i]
        bx, by = _UNIT_CIRCLE[(i + 1) % _CIRCLE_SEGMENTS]
        verts.append((cx, cy))
        verts.append((cx + ax * radius, cy + ay * radius))
        verts.append((cx + bx * radius, cy + by * radius))
        colors.append(color)
        colors.append(color)
        colors.append(color)


def _append_ring(verts, colors, center, radius, width, color, segments=28):
    """Emit an annulus as TRIS, used for the pinch indicator."""
    cx, cy = center[0], center[1]
    inner = max(radius - width * 0.5, 0.1)
    outer = radius + width * 0.5
    for i in range(segments):
        a0 = i * 2.0 * math.pi / segments
        a1 = (i + 1) * 2.0 * math.pi / segments
        c0, s0 = math.cos(a0), math.sin(a0)
        c1, s1 = math.cos(a1), math.sin(a1)
        p0i = (cx + c0 * inner, cy + s0 * inner)
        p0o = (cx + c0 * outer, cy + s0 * outer)
        p1i = (cx + c1 * inner, cy + s1 * inner)
        p1o = (cx + c1 * outer, cy + s1 * outer)
        verts.extend((p0i, p0o, p1o, p0i, p1o, p1i))
        colors.extend((color,) * 6)


def _append_arc(verts, colors, center, radius, width, color, progress,
                segments=28):
    """Emit a partial annulus (arc) as TRIS, showing progress 0..1."""
    cx, cy = center[0], center[1]
    inner = max(radius - width * 0.5, 0.1)
    outer = radius + width * 0.5
    arc_segments = max(1, int(segments * max(0.0, min(progress, 1.0))))
    for i in range(arc_segments):
        a0 = i * 2.0 * math.pi * progress / arc_segments
        a1 = (i + 1) * 2.0 * math.pi * progress / arc_segments
        c0, s0 = math.cos(a0 - math.pi / 2), math.sin(a0 - math.pi / 2)
        c1, s1 = math.cos(a1 - math.pi / 2), math.sin(a1 - math.pi / 2)
        p0i = (cx + c0 * inner, cy + s0 * inner)
        p0o = (cx + c0 * outer, cy + s0 * outer)
        p1i = (cx + c1 * inner, cy + s1 * inner)
        p1o = (cx + c1 * outer, cy + s1 * outer)
        verts.extend((p0i, p0o, p1o, p0i, p1o, p1i))
        colors.extend((color,) * 6)


def _draw_tris(verts, colors):
    if not verts:
        return
    shader = _shader("SMOOTH_COLOR")
    batch = batch_for_shader(shader, "TRIS",
                             {"pos": _fit(verts), "color": colors})
    shader.bind()
    batch.draw(shader)


def _draw_lines(segments, colors, width, region):
    """Thick anti-aliased lines via the polyline shader."""
    if not segments:
        return
    try:
        shader = _shader("POLYLINE_SMOOTH_COLOR")
        batch = batch_for_shader(shader, "LINES",
                                 {"pos": _fit(segments), "color": colors})
        shader.bind()
        shader.uniform_float("viewportSize", (region.width, region.height))
        shader.uniform_float("lineWidth", width)
        batch.draw(shader)
    except Exception:
        # Fall back to fixed-function lines: thinner and not anti-aliased,
        # but available on every driver.
        shader = _shader("SMOOTH_COLOR")
        batch = batch_for_shader(shader, "LINES",
                                 {"pos": _fit(segments), "color": colors})
        gpu.state.line_width_set(max(1.0, min(width, 8.0)))
        shader.bind()
        batch.draw(shader)
        gpu.state.line_width_set(1.0)


def _draw_rect(x, y, w, h, color):
    verts = [(x, y), (x + w, y), (x + w, y + h), (x, y), (x + w, y + h),
             (x, y + h)]
    _draw_tris(verts, [color] * 6)


def _draw_rect_outline(x, y, w, h, color, width, region):
    pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    segs, cols = [], []
    for i in range(4):
        segs.append(pts[i])
        segs.append(pts[(i + 1) % 4])
        cols.append(color)
        cols.append(color)
    _draw_lines(segs, cols, width, region)


# ---------------------------------------------------------------------------
# Camera preview texture
# ---------------------------------------------------------------------------


class PreviewTexture:
    """Wraps a GPUTexture, re-uploading only when a new frame arrives."""

    def __init__(self):
        self.texture = None
        self.frame_id = -1
        self.width = 0
        self.height = 0
        self._format = None
        self._failed = False

    def release(self):
        self.texture = None
        self.frame_id = -1

    def update(self, frame, frame_id: int) -> bool:
        if frame is None or self._failed:
            return self.texture is not None
        if frame_id == self.frame_id and self.texture is not None:
            return True

        try:
            import numpy as np
            height, width = frame.shape[0], frame.shape[1]

            if frame.shape[2] == 3:
                # GPUTexture has no 3-channel byte format; pad to RGBA.
                rgba = np.empty((height, width, 4), dtype=np.uint8)
                rgba[:, :, :3] = frame
                rgba[:, :, 3] = 255
            else:
                rgba = np.ascontiguousarray(frame)

            texture = self._make_texture(rgba, width, height)
            if texture is None:
                self._failed = True
                return False

            self.texture = texture
            self.frame_id = frame_id
            self.width = width
            self.height = height
            return True
        except Exception:
            self._failed = True
            return False

    def _make_texture(self, rgba, width, height):
        import numpy as np

        # gpu.types.Buffer only accepts FLOAT data for texture uploads, so the
        # byte frame is normalised to 0..1 regardless of the target format.
        # RGBA8 is preferred for the texture itself: Blender converts on
        # upload, and one byte per channel keeps a 320-wide preview at a
        # quarter the VRAM of RGBA32F. No colour transform is applied, because
        # POST_PIXEL draws into a display-referred buffer and the webcam frame
        # is already display-referred sRGB.
        data = np.ascontiguousarray(rgba.astype(np.float32) / 255.0)
        try:
            buffer = gpu.types.Buffer("FLOAT", data.shape, data)
        except Exception:
            return None

        formats = ("RGBA8", "RGBA16F", "RGBA32F")
        if self._format is not None:
            formats = (self._format,) + tuple(f for f in formats
                                              if f != self._format)

        for tex_format in formats:
            try:
                texture = gpu.types.GPUTexture(size=(width, height),
                                               format=tex_format,
                                               data=buffer)
                self._format = tex_format
                return texture
            except Exception:
                continue
        return None


# ---------------------------------------------------------------------------
# The overlay
# ---------------------------------------------------------------------------


class Overlay:
    """Owns the draw handler and the data it renders."""

    def __init__(self):
        self._handle = None
        self.snapshot = None
        self.engine = None
        self.settings = None
        self._fade_alpha = {}
        self.status_text = ""
        self.error_text = ""
        self.fps = 0.0
        self.preview = PreviewTexture()
        self.target_area = None
        self.tutorial_step = -1  # -1 = not showing, 0..3 = active steps
        self._tutorial_start = 0.0

    # -- lifecycle ---------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._handle is not None

    def enable(self):
        if self._handle is None:
            self._handle = bpy.types.SpaceView3D.draw_handler_add(
                self._draw, (), "WINDOW", "POST_PIXEL")

    def disable(self):
        if self._handle is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(self._handle,
                                                          "WINDOW")
            except Exception:
                pass
            self._handle = None
        self.preview.release()
        self.snapshot = None

    def tag_redraw(self):
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()

    # -- drawing -----------------------------------------------------------

    def _draw(self):
        try:
            self._draw_impl()
        except Exception as exc:                   # pragma: no cover
            # A raising draw handler spams the console every redraw and can
            # leave GPU state dirty; swallow and surface it in the panel.
            self.error_text = f"draw error: {exc}"

    def _draw_impl(self):
        context = bpy.context
        # Re-read the settings from the scene rather than trusting the cached
        # pointer: loading a file or undoing can invalidate a held reference to
        # a PropertyGroup, and a draw handler is a bad place to discover that.
        settings = getattr(context.scene, "hgc_settings", None) or self.settings
        snapshot = self.snapshot
        if settings is None or not settings.show_overlay:
            return

        area = context.area
        region = context.region
        if area is None or region is None or area.type != "VIEW_3D":
            return
        if region.type != "WINDOW":
            return
        # Only decorate the viewport the session is bound to, otherwise a
        # quad-view or second window shows four unrelated copies of the hand.
        if self.target_area is not None and area != self.target_area:
            return

        gpu.state.blend_set("ALPHA")
        try:
            if settings.show_preview and snapshot is not None:
                self._draw_preview(region, snapshot, settings)
            if snapshot is not None and settings.show_skeleton:
                self._draw_hands(region, snapshot, settings)
            if snapshot is not None:
                self._draw_pivot_link(context, region, snapshot, settings)
                self._draw_grab_highlight(context, region)
                self._draw_point_pointer(region)
            if settings.show_hud:
                self._draw_hud(region, snapshot, settings)
            self._draw_tutorial(region)
        finally:
            gpu.state.blend_set("NONE")

    # -- camera preview ----------------------------------------------------

    def _preview_rect(self, region, snapshot, settings):
        width = float(settings.preview_size)
        aspect = 3.0 / 4.0
        if snapshot is not None and snapshot.width and snapshot.height:
            aspect = snapshot.height / snapshot.width
        height = width * aspect

        margin = float(settings.preview_margin)
        corner = settings.preview_corner
        if "LEFT" in corner:
            x = margin
        else:
            x = region.width - width - margin
        if "BOTTOM" in corner:
            y = margin
        else:
            y = region.height - height - margin
        return x, y, width, height

    def _draw_preview(self, region, snapshot, settings):
        if snapshot.frame is None:
            return
        if not self.preview.update(snapshot.frame, snapshot.frame_id):
            return

        from gpu_extras.presets import draw_texture_2d

        x, y, w, h = self._preview_rect(region, snapshot, settings)
        alpha = float(settings.preview_opacity)

        # Backing plate so the preview reads on a light viewport too.
        _draw_rect(x - 2, y - 2, w + 4, h + 4, (0.05, 0.05, 0.06, 0.85 * alpha))

        gpu.state.blend_set("ALPHA")
        try:
            draw_texture_2d(self.preview.texture, (x, y), w, h)
        except TypeError:                          # pragma: no cover
            return

        if alpha < 0.999:
            # draw_texture_2d has no opacity argument; fade with a scrim.
            _draw_rect(x, y, w, h, (0.05, 0.05, 0.06, 1.0 - alpha))

        _draw_rect_outline(x, y, w, h, (0.55, 0.58, 0.65, 0.9), 1.5, region)

        # Landmarks again, drawn inside the preview at preview scale.
        if settings.show_preview_landmarks:
            scale = w / max(region.width, 1)
            self._draw_hands(region, snapshot, settings,
                             rect=(x, y, w, h), scale=scale, labels=False)

        self._preview_caption(x, y, w, h, snapshot, settings)

    def _preview_caption(self, x, y, w, h, snapshot, settings):
        font = 0
        _set_font_size(font, 11.0)
        blf.color(font, 0.85, 0.88, 0.95, 0.95)
        label = f"cam {settings.camera_index}"
        if self.fps:
            label += f"  {self.fps:.0f} fps"
        if snapshot is not None and snapshot.latency_ms:
            label += f"  {snapshot.latency_ms:.0f} ms"
        blf.position(font, x + 4, y + h + 6, 0)
        blf.draw(font, label)

    # -- hand skeleton -----------------------------------------------------

    def _map(self, point, region, rect, scale, mirror):
        """Landmark -> pixels, either full-region or inside the preview rect."""
        if rect is None:
            return to_region(point, region, mirror=mirror)
        rx, ry, rw, rh = rect
        x = ((1.0 - point[0]) if mirror else point[0]) * rw + rx
        y = (1.0 - point[1]) * rh + ry
        return (x, y)

    def _draw_hands(self, region, snapshot, settings, rect=None, scale=1.0,
                    labels=True):
        if not snapshot.hands:
            return

        minimal = (getattr(settings, "overlay_detail", "FULL") == "MINIMAL"
                   and rect is None)
        joint_radius = settings.landmark_size * (scale if rect else 1.0)
        bone_width = settings.bone_width * (scale if rect else 1.0)
        alpha = settings.overlay_opacity if rect is None else 1.0

        tris_v, tris_c = [], []
        line_v, line_c = [], []

        for hand in snapshot.hands:
            pts = [self._map(p, region, rect, scale, settings.mirror)
                   for p in hand.image_pts]
            state = None
            if self.engine is not None and hand.slot < len(self.engine.hands):
                state = self.engine.hands[hand.slot]

            # --- bones (full skeleton only; minimal skips them for clarity) ---
            if not minimal:
                for a, b in LM.HAND_CONNECTIONS:
                    ca = LM.landmark_color(b)   # distal-end colour
                    line_v.append(pts[a])
                    line_v.append(pts[b])
                    line_c.append((ca[0], ca[1], ca[2], 0.55 * alpha))
                    line_c.append((ca[0], ca[1], ca[2], 0.95 * alpha))

            # --- joints: all 21, or just the ones gestures care about ---
            joint_iter = (sorted(LM.KEY_LANDMARKS) if minimal
                          else range(LM.NUM_LANDMARKS))
            for index in joint_iter:
                colour = LM.landmark_color(index)
                radius = joint_radius * (1.45 if index in LM.KEY_LANDMARKS
                                         else 1.0)
                # Dark halo first so light joints stay visible on pale scenes.
                _append_disc(tris_v, tris_c, pts[index], radius + 1.6 * scale,
                             (0.02, 0.02, 0.03, 0.7 * alpha))
                _append_disc(tris_v, tris_c, pts[index], radius,
                             (colour[0], colour[1], colour[2], 0.98 * alpha))

            # --- full-hand pick indicator (thumb gathers all four tips) ---
            if state is not None:
                thumb = pts[LM.THUMB_TIP]
                grab = state.grab
                if grab.engaged:
                    colour = (0.25, 1.0, 0.55)
                    for tip in LM.PICK_TIPS:
                        other = pts[tip]
                        line_v.extend((thumb, other))
                        line_c.extend(((*colour, 0.9 * alpha),) * 2)
                    cx, cy = palm_center(hand.image_pts)
                    anchor = self._map((cx, cy, 0.0), region, rect, scale,
                                       settings.mirror)
                    _append_ring(tris_v, tris_c, anchor,
                                 joint_radius * 3.2, 2.8 * scale,
                                 (colour[0], colour[1], colour[2],
                                  0.95 * alpha))
                elif grab.ratio < settings.pinch_off * 1.6:
                    # Proximity: radial arc fills as the hand gathers.
                    t = max(0.0, min(1.0,
                            (settings.pinch_off * 1.6 - grab.ratio)
                            / max(settings.pinch_off * 1.2, 1e-3)))
                    cx, cy = palm_center(hand.image_pts)
                    anchor = self._map((cx, cy, 0.0), region, rect, scale,
                                       settings.mirror)
                    _append_ring(tris_v, tris_c, anchor,
                                 joint_radius * 3.0, 1.4 * scale,
                                 (0.5, 0.45, 0.2, 0.12 * alpha))
                    arc_color = (1.0 - 0.6 * t, 0.72 + 0.28 * t,
                                 0.18 + 0.37 * t, (0.3 + 0.65 * t) * alpha)
                    _append_arc(tris_v, tris_c, anchor,
                                joint_radius * 3.0, 2.2 * scale,
                                arc_color, t)

            # --- palm anchor: the point the move gesture actually follows ---
            cx, cy = palm_center(hand.image_pts)
            anchor = self._map((cx, cy, 0.0), region, rect, scale,
                               settings.mirror)
            _append_ring(tris_v, tris_c, anchor, joint_radius * 2.2,
                         1.4 * scale, (1.0, 1.0, 1.0, 0.5 * alpha))

        _draw_lines(line_v, line_c, max(bone_width, 0.5), region)
        _draw_tris(tris_v, tris_c)

        if labels and settings.show_landmark_indices and not minimal:
            self._draw_indices(region, snapshot, settings, rect, scale, alpha)
        if labels and settings.show_hand_labels and not minimal:
            self._draw_hand_labels(region, snapshot, settings, rect, scale,
                                   alpha)

    def _draw_point_pointer(self, region):
        """Show ray-cast feedback and dwell progress around the index tip."""
        engine = self.engine
        if engine is None or engine.point_2d is None:
            return

        progress = max(0.0, min(float(engine.point_progress), 1.0))
        if engine.pointed_object is None:
            colour = (0.75, 0.78, 0.85, 0.75)
        else:
            colour = (1.0 - 0.65 * progress,
                      0.72 + 0.28 * progress,
                      0.18 + 0.35 * progress,
                      0.95)

        verts, colors = [], []
        _append_ring(verts, colors, engine.point_2d, 12.0, 2.5, colour)
        if engine.pointed_object is not None:
            _append_ring(verts, colors, engine.point_2d, 18.0, 2.0,
                         (0.4, 0.4, 0.45, 0.25))
            if progress > 0.0 and progress < 1.0:
                _append_arc(verts, colors, engine.point_2d, 18.0, 3.0,
                            colour, progress)
            elif progress >= 1.0:
                _append_ring(verts, colors, engine.point_2d, 18.0, 3.0,
                             (0.25, 1.0, 0.55, 0.95))
        _draw_tris(verts, colors)

    def _draw_indices(self, region, snapshot, settings, rect, scale, alpha):
        font = 0
        _set_font_size(font, 10.0)
        for hand in snapshot.hands:
            for index in range(LM.NUM_LANDMARKS):
                x, y = self._map(hand.image_pts[index], region, rect, scale,
                                 settings.mirror)
                colour = LM.landmark_color(index)
                blf.color(font, colour[0], colour[1], colour[2], 0.9 * alpha)
                blf.position(font, x + 7, y + 5, 0)
                blf.draw(font, str(index))

    def _draw_hand_labels(self, region, snapshot, settings, rect, scale,
                          alpha):
        font = 0
        _set_font_size(font, 13.0)
        for hand in snapshot.hands:
            x, y = self._map(hand.image_pts[LM.WRIST], region, rect, scale,
                             settings.mirror)
            blf.color(font, 0.95, 0.96, 1.0, 0.95 * alpha)
            blf.position(font, x - 16, y - 24, 0)
            blf.draw(font, f"{hand.handedness}  {hand.score:.2f}")

    # -- link from hand to the object being held ---------------------------

    def _draw_pivot_link(self, context, region, snapshot, settings):
        engine = self.engine
        if engine is None or engine.session is None or not snapshot.hands:
            return
        rv3d = context.region_data
        if rv3d is None:
            return
        from bpy_extras import view3d_utils

        pivot_2d = view3d_utils.location_3d_to_region_2d(
            region, rv3d, engine.session.pivot)
        if pivot_2d is None:
            return

        cx, cy = palm_center(snapshot.hands[0].image_pts)
        anchor = to_region((cx, cy, 0.0), region, mirror=settings.mirror)

        colour = (0.25, 1.0, 0.55, 0.55)
        _draw_lines([tuple(anchor), tuple(pivot_2d)], [colour, colour],
                    1.5, region)
        tris_v, tris_c = [], []
        _append_ring(tris_v, tris_c, tuple(pivot_2d), 11.0, 2.0,
                     (0.25, 1.0, 0.55, 0.9))
        _draw_tris(tris_v, tris_c)

    def _draw_grab_highlight(self, context, region):
        """Flash a highlight outline around grabbed objects."""
        engine = self.engine
        if engine is None or engine.session is None:
            return
        rv3d = context.region_data
        if rv3d is None:
            return
        from bpy_extras import view3d_utils

        for obj in engine.session.objects:
            try:
                bbox = [obj.matrix_world @ Vector(corner)
                        for corner in obj.bound_box]
            except (ReferenceError, AttributeError):
                continue
            pts_2d = []
            for corner in bbox:
                p = view3d_utils.location_3d_to_region_2d(region, rv3d, corner)
                if p is not None:
                    pts_2d.append(p)
            if len(pts_2d) < 2:
                continue
            xs = [p.x for p in pts_2d]
            ys = [p.y for p in pts_2d]
            x_min, x_max = min(xs) - 4, max(xs) + 4
            y_min, y_max = min(ys) - 4, max(ys) + 4
            highlight_color = (0.25, 1.0, 0.55, 0.35)
            _draw_rect_outline(x_min, y_min, x_max - x_min, y_max - y_min,
                               highlight_color, 2.0, region)

    # -- heads-up display --------------------------------------------------

    def _draw_hud(self, region, snapshot, settings):
        """
        Status block, anchored bottom-left.

        Blender already writes the view name, collection and active object into
        the top-left of the viewport, so putting the readout there overlaps it.
        The bottom-left corner is empty in a default layout, and the block grows
        upward from there.
        """
        font = 0
        pad = 12
        x = pad

        mode = MODE_NONE
        if self.engine is not None:
            mode = self.engine.mode
        active = mode != MODE_NONE
        minimal = getattr(settings, "overlay_detail", "FULL") == "MINIMAL"

        # Colour and badge the mode title by channel.
        if mode == MODE_GRAB:
            title_color = (0.35, 1.0, 0.55, 1.0)
            badge = "[GRAB]"
        elif mode == MODE_TWO_HAND:
            title_color = (0.75, 0.55, 1.0, 1.0)
            badge = "[SCALE]"
        elif active:
            title_color = (0.35, 1.0, 0.6, 1.0)
            badge = "[ACTIVE]"
        else:
            title_color = (0.85, 0.88, 0.95, 0.95)
            badge = "[IDLE]"

        # Lines are listed top-to-bottom, then drawn bottom-up.
        lines = [
            (15.0, title_color,
             f"Gesture {badge} · {MODE_LABELS.get(mode, mode)}"),
            (12.0, (0.78, 0.80, 0.86, 0.9),
             self.status_text or "Show your hand to the camera"),
        ]
        if snapshot is not None and snapshot.hands and not minimal:
            hands = ", ".join(h.handedness for h in snapshot.hands)
            lines.append((12.0, (0.65, 0.70, 0.80, 0.85),
                          f"{len(snapshot.hands)} hand(s): {hands}"))
        if self.error_text:
            lines.append((12.0, (1.0, 0.45, 0.4, 0.95), self.error_text[:110]))
        if minimal:
            lines.append((11.0, (0.55, 0.58, 0.66, 0.8),
                          "pick=move+rotate  two hands=scale   Esc"))
        else:
            lines.append((11.0, (0.55, 0.58, 0.66, 0.8),
                          "point=select  pick(all fingers)=move+rotate  "
                          "two hands=scale   Esc"))
        # Step over the camera preview if it occupies this corner.
        base = pad
        if settings.show_preview and settings.preview_corner == "BOTTOM_LEFT":
            _, _, _, preview_h = self._preview_rect(region, snapshot, settings)
            base += preview_h + settings.preview_margin + 14

        line_height = 19
        y = base + line_height * (len(lines) - 1)
        for size, color, text in lines:
            _set_font_size(font, size)
            blf.color(font, *color)
            blf.position(font, x, y, 0)
            blf.draw(font, text)
            y -= line_height

    def _draw_tutorial(self, region):
        """Draw a semi-transparent tutorial overlay with step-by-step guidance."""
        if self.tutorial_step < 0:
            return

        steps = [
            "Step 1: Show your hand to the camera",
            "Step 2: Close all fingers to your thumb (pick gesture)",
            "Step 3: Move your hand to move the object",
            "Step 4: Open your hand to release — you're ready!",
        ]

        engine = self.engine
        step = self.tutorial_step

        # Auto-advance based on gesture state.
        if engine is not None:
            import time
            now = time.monotonic()
            if step == 0 and self.snapshot and self.snapshot.hands:
                self.tutorial_step = 1
                self._tutorial_start = now
            elif step == 1 and engine.mode != "NONE":
                self.tutorial_step = 2
                self._tutorial_start = now
            elif step == 2 and now - self._tutorial_start > 3.0:
                self.tutorial_step = 3
                self._tutorial_start = now
            elif step == 3 and now - self._tutorial_start > 3.0:
                self.tutorial_step = -1  # Done
                return
            step = self.tutorial_step
            if step < 0:
                return

        if step >= len(steps):
            self.tutorial_step = -1
            return

        # Draw semi-transparent backdrop at the top.
        w = region.width
        box_h = 60
        y = region.height - box_h - 10
        _draw_rect(10, y, w - 20, box_h, (0.05, 0.05, 0.08, 0.75))
        _draw_rect_outline(10, y, w - 20, box_h,
                           (0.4, 0.85, 0.55, 0.8), 1.5, region)

        font = 0
        _set_font_size(font, 16.0)
        blf.color(font, 0.9, 0.95, 1.0, 1.0)
        blf.position(font, 24, y + 34, 0)
        blf.draw(font, steps[step])

        _set_font_size(font, 11.0)
        blf.color(font, 0.6, 0.65, 0.7, 0.9)
        blf.position(font, 24, y + 12, 0)
        blf.draw(font, f"Step {step + 1} of {len(steps)}  ·  Press Esc to skip tutorial")


#: Module-level singleton; the operator and the panel both talk to this.
overlay = Overlay()
