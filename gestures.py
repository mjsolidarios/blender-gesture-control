# SPDX-License-Identifier: GPL-3.0-or-later
"""
Gesture recognition and the transform engine that acts on it.

Interaction model
-----------------
A pinch is a clutch: nothing moves until thumb and fingertip meet, and motion
stops the instant they part. Which fingertip meets the thumb selects the
channel, so the user never has to reach for the keyboard:

======================  ==========================================
Gesture                 Effect
======================  ==========================================
Thumb + index           Grab and move in the view plane. Pushing
                        the hand toward or away from the camera
                        moves along the view axis.
Thumb + middle          Rotate. The object copies the change in
                        your hand's orientation, all three axes.
Thumb + ring            Scale. Hand toward camera grows, away
                        shrinks.
Both hands, index       Two-handed transform: spread the hands to
                        scale, twist to roll, move both together
                        to translate.
======================  ==========================================

Two mechanisms keep this usable rather than twitchy:

* **Hysteresis.** The pinch engages at a tighter threshold than it releases at,
  so a hand hovering near the boundary does not chatter on and off.
* **Relative deltas.** Every gesture records the pose of the hand *and* of the
  objects at the moment of engagement, then applies the difference. Absolute
  mappings would make the object leap to the hand as soon as you pinched.
"""

from __future__ import annotations

import math

from mathutils import Matrix, Quaternion, Vector

from . import landmarks as LM

# MediaPipe world landmarks use +x right, +y down, +z away from the camera.
# Blender's view space uses +x right, +y up, +z toward the viewer. This flip
# converts between them and is its own inverse.
_AXIS_FLIP = Matrix.Diagonal((1.0, -1.0, -1.0))

MODE_NONE = "NONE"
MODE_MOVE = "MOVE"
MODE_ROTATE = "ROTATE"
MODE_SCALE = "SCALE"
MODE_TWO_HAND = "TWO_HAND"

MODE_LABELS = {
    MODE_NONE: "Idle",
    MODE_MOVE: "Move",
    MODE_ROTATE: "Rotate",
    MODE_SCALE: "Scale",
    MODE_TWO_HAND: "Two-handed",
}

#: Fingertip -> gesture channel.
FINGER_TO_MODE = {
    LM.INDEX_TIP: MODE_MOVE,
    LM.MIDDLE_TIP: MODE_ROTATE,
    LM.RING_TIP: MODE_SCALE,
}


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _v(point) -> Vector:
    return Vector((point[0], point[1], point[2]))


def hand_scale(world_pts) -> float:
    """
    Reference length for the hand, in metres.

    Wrist to middle knuckle is used because it barely changes as fingers curl,
    which makes every ratio derived from it independent of hand pose.
    """
    length = (_v(world_pts[LM.MIDDLE_MCP]) - _v(world_pts[LM.WRIST])).length
    return length if length > 1e-5 else 0.09


def pinch_ratio(world_pts, tip_index: int) -> float:
    """Thumb-to-fingertip distance, normalised by hand size."""
    gap = (_v(world_pts[LM.THUMB_TIP]) - _v(world_pts[tip_index])).length
    return gap / hand_scale(world_pts)


def apparent_size(image_pts, aspect: float) -> float:
    """
    On-screen span of the hand, used as a depth proxy.

    A hand moved toward the camera covers more of the frame. That is a far more
    stable depth cue than the per-landmark ``z``, which MediaPipe only estimates
    to within a few centimetres.
    """
    wrist = image_pts[LM.WRIST]
    mid = image_pts[LM.MIDDLE_MCP]
    dx = (mid[0] - wrist[0]) * aspect
    dy = mid[1] - wrist[1]
    size = math.hypot(dx, dy)
    return size if size > 1e-4 else 1e-4


def palm_center(image_pts):
    """Centroid of wrist and the four finger knuckles, in normalised coords."""
    idx = (LM.WRIST, LM.INDEX_MCP, LM.MIDDLE_MCP, LM.RING_MCP, LM.PINKY_MCP)
    x = sum(image_pts[i][0] for i in idx) / len(idx)
    y = sum(image_pts[i][1] for i in idx) / len(idx)
    return x, y


def hand_orientation(world_pts) -> Matrix:
    """
    Orthonormal 3x3 frame describing the hand's pose, in Blender view axes.

    Built from the three palm landmarks, which form a rigid triangle regardless
    of what the fingers are doing.
    """
    wrist = _v(world_pts[LM.WRIST])
    index_mcp = _v(world_pts[LM.INDEX_MCP])
    pinky_mcp = _v(world_pts[LM.PINKY_MCP])

    across = index_mcp - pinky_mcp          # thumb-ward across the knuckles
    along = _v(world_pts[LM.MIDDLE_MCP]) - wrist   # wrist toward fingers

    if across.length < 1e-6 or along.length < 1e-6:
        return Matrix.Identity(3)

    normal = across.cross(along)
    if normal.length < 1e-6:
        return Matrix.Identity(3)

    x_axis = across.normalized()
    z_axis = normal.normalized()
    y_axis = z_axis.cross(x_axis).normalized()
    x_axis = y_axis.cross(z_axis).normalized()   # re-orthogonalise

    frame = Matrix((
        (x_axis.x, y_axis.x, z_axis.x),
        (x_axis.y, y_axis.y, z_axis.y),
        (x_axis.z, y_axis.z, z_axis.z),
    ))
    # Express the frame in Blender's view-space axis convention.
    return _AXIS_FLIP @ frame @ _AXIS_FLIP


def to_region(point, region, mirror: bool = True) -> Vector:
    """
    Normalised MediaPipe coordinates -> Blender region pixels.

    MediaPipe's origin is top-left with y growing downward; Blender regions are
    bottom-left with y growing upward, hence the vertical flip. The horizontal
    flip makes the overlay behave like a mirror, which is what people expect
    when watching their own hand.
    """
    x = (1.0 - point[0]) if mirror else point[0]
    y = 1.0 - point[1]
    return Vector((x * region.width, y * region.height))


# ---------------------------------------------------------------------------
# Per-hand pinch state
# ---------------------------------------------------------------------------


class PinchState:
    """Hysteretic latch for one thumb-fingertip pair."""

    __slots__ = ("engaged", "ratio")

    def __init__(self):
        self.engaged = False
        self.ratio = 1.0

    def update(self, ratio: float, on_threshold: float,
               off_threshold: float) -> bool:
        self.ratio = ratio
        if self.engaged:
            if ratio > off_threshold:
                self.engaged = False
        else:
            if ratio < on_threshold:
                self.engaged = True
        return self.engaged


class HandState:
    """Tracks the pinch latches for one hand slot across frames."""

    def __init__(self):
        self.pinches = {tip: PinchState() for tip in FINGER_TO_MODE}
        self.present = False

    def update(self, world_pts, on_threshold, off_threshold):
        active = None
        for tip, state in self.pinches.items():
            if state.update(pinch_ratio(world_pts, tip),
                            on_threshold, off_threshold) and active is None:
                # Priority order matters when two fingers are near the thumb:
                # index wins, then middle, then ring.
                active = tip
        # Enforce the priority explicitly rather than relying on dict order.
        for tip in (LM.INDEX_TIP, LM.MIDDLE_TIP, LM.RING_TIP):
            if self.pinches[tip].engaged:
                return tip
        return active

    def reset(self):
        for state in self.pinches.values():
            state.engaged = False


# ---------------------------------------------------------------------------
# Transform engine
# ---------------------------------------------------------------------------


class GrabSession:
    """State captured at the moment a gesture engaged."""

    def __init__(self, mode, objects, pivot):
        self.mode = mode
        self.objects = list(objects)
        self.start_matrices = [obj.matrix_world.copy() for obj in self.objects]
        self.pivot = pivot.copy()
        # Filled in by whichever gesture owns the session.
        self.hand_2d = None
        self.hand_size = 1.0
        self.hand_frame = None
        self.pivot_2d = None
        self.two_hand_distance = 1.0
        self.two_hand_angle = 0.0
        self.two_hand_mid = None
        self.changed = False


class GestureEngine:
    """
    Turns a stream of :class:`~.tracker.FrameSnapshot` into object transforms.

    One instance lives for the duration of a capture session and is driven from
    the modal operator's timer, always on Blender's main thread.
    """

    def __init__(self):
        self.hands = [HandState() for _ in range(4)]
        self.session = None
        self.mode = MODE_NONE
        self.status = "Idle"
        self.last_error = ""

    # -- public API --------------------------------------------------------

    def reset(self):
        for hand in self.hands:
            hand.reset()
        self.session = None
        self.mode = MODE_NONE
        self.status = "Idle"

    def cancel(self):
        """Restore every object touched by the live gesture."""
        if self.session is not None:
            for obj, matrix in zip(self.session.objects,
                                   self.session.start_matrices):
                try:
                    obj.matrix_world = matrix
                except ReferenceError:
                    pass
            self.session = None
        self.reset()

    def update(self, context, region, rv3d, snapshot, settings) -> bool:
        """
        Advance the engine by one frame.

        :returns: True if any object was modified.
        """
        if region is None or rv3d is None:
            return False

        aspect = (snapshot.width / snapshot.height) if snapshot.height else \
            (region.width / max(region.height, 1))

        on_threshold = settings.pinch_on
        off_threshold = max(settings.pinch_off, on_threshold + 0.02)

        # Which mode does each visible hand request this frame?
        requests = []
        seen = set()
        for hand in snapshot.hands:
            if hand.slot >= len(self.hands):
                continue
            seen.add(hand.slot)
            state = self.hands[hand.slot]
            state.present = True
            tip = state.update(hand.world_pts, on_threshold, off_threshold)
            if tip is not None:
                requests.append((hand, tip))

        for slot, state in enumerate(self.hands):
            if slot not in seen:
                state.present = False
                state.reset()

        two_handed = (len(requests) == 2
                      and all(tip == LM.INDEX_TIP for _, tip in requests)
                      and settings.use_two_hand)

        if two_handed:
            desired = MODE_TWO_HAND
        elif requests:
            desired = FINGER_TO_MODE.get(requests[0][1], MODE_NONE)
        else:
            desired = MODE_NONE

        # Releasing, or switching channel, ends the current session.
        if self.session is not None and desired != self.session.mode:
            self.end_session()

        if desired == MODE_NONE:
            self.mode = MODE_NONE
            self.status = "Open hand - no object held"
            return False

        objects = self._targets(context)
        if not objects:
            self.mode = desired
            self.status = "Nothing selected"
            return False

        if self.session is None:
            self.session = self._begin_session(context, region, rv3d, desired,
                                               objects, requests, snapshot,
                                               settings, aspect)
            if self.session is None:
                return False

        self.mode = desired
        try:
            if desired == MODE_TWO_HAND:
                return self._apply_two_hand(region, rv3d, requests, settings)
            hand = requests[0][0]
            if desired == MODE_MOVE:
                return self._apply_move(region, rv3d, hand, settings, aspect)
            if desired == MODE_ROTATE:
                return self._apply_rotate(rv3d, hand, settings)
            if desired == MODE_SCALE:
                return self._apply_scale(hand, settings, aspect)
        except Exception as exc:                  # pragma: no cover
            self.last_error = str(exc)
            self.status = f"Gesture error: {exc}"
            self.end_session()
        return False

    # -- session management ------------------------------------------------

    def _targets(self, context):
        """Selected, visible, editable objects."""
        out = []
        for obj in context.selected_objects:
            if obj is None or not obj.visible_get():
                continue
            if obj.library is not None and obj.override_library is None:
                continue   # linked data cannot be transformed
            out.append(obj)
        return out

    def _pivot_point(self, context, objects) -> Vector:
        active = context.view_layer.objects.active
        if active is not None and active in objects:
            return active.matrix_world.translation.copy()
        total = Vector((0.0, 0.0, 0.0))
        for obj in objects:
            total += obj.matrix_world.translation
        return total / len(objects)

    def _begin_session(self, context, region, rv3d, mode, objects, requests,
                       snapshot, settings, aspect):
        from bpy_extras import view3d_utils

        pivot = self._pivot_point(context, objects)
        session = GrabSession(mode, objects, pivot)

        pivot_2d = view3d_utils.location_3d_to_region_2d(region, rv3d, pivot)
        if pivot_2d is None:
            # Pivot is behind the camera; screen-space mapping is meaningless.
            self.status = "Pivot is off-screen"
            return None
        session.pivot_2d = Vector(pivot_2d)

        if mode == MODE_TWO_HAND:
            a, b = requests[0][0], requests[1][0]
            pa = to_region(a.image_pts[LM.INDEX_TIP], region,
                           mirror=settings.mirror)
            pb = to_region(b.image_pts[LM.INDEX_TIP], region,
                           mirror=settings.mirror)
            delta = pb - pa
            session.two_hand_distance = max(delta.length, 1.0)
            session.two_hand_angle = math.atan2(delta.y, delta.x)
            session.two_hand_mid = (pa + pb) * 0.5
        else:
            hand = requests[0][0]
            cx, cy = palm_center(hand.image_pts)
            session.hand_2d = to_region((cx, cy, 0.0), region,
                                        mirror=settings.mirror)
            session.hand_size = apparent_size(hand.image_pts, aspect)
            session.hand_frame = hand_orientation(hand.world_pts)

        self.status = f"{MODE_LABELS[mode]} - {len(objects)} object(s)"
        return session

    def end_session(self):
        """Close the live gesture and, if anything moved, make it undoable."""
        import bpy
        session = self.session
        self.session = None
        if session is None:
            return
        if session.changed:
            try:
                bpy.ops.ed.undo_push(
                    message=f"Hand Gesture {MODE_LABELS.get(session.mode, '')}")
            except Exception:
                pass

    def _commit(self, delta: Matrix) -> bool:
        """Apply a world-space delta around the session pivot."""
        session = self.session
        pivot = session.pivot
        to_origin = Matrix.Translation(-pivot)
        from_origin = Matrix.Translation(pivot)
        full = from_origin @ delta @ to_origin

        moved = False
        for obj, start in zip(session.objects, session.start_matrices):
            try:
                obj.matrix_world = full @ start
                moved = True
            except (ReferenceError, AttributeError):
                continue
        if moved:
            session.changed = True
        return moved

    # -- individual gestures ----------------------------------------------

    def _apply_move(self, region, rv3d, hand, settings, aspect) -> bool:
        from bpy_extras import view3d_utils

        session = self.session
        cx, cy = palm_center(hand.image_pts)
        now_2d = to_region((cx, cy, 0.0), region,
                           mirror=settings.mirror)
        screen_delta = (now_2d - session.hand_2d) * settings.move_sensitivity

        target_2d = session.pivot_2d + screen_delta
        # Unproject at the pivot's own depth so dragging tracks the cursor
        # exactly, in perspective as well as orthographic views.
        new_pivot = view3d_utils.region_2d_to_location_3d(
            region, rv3d, target_2d, session.pivot)
        if new_pivot is None:
            return False
        translation = new_pivot - session.pivot

        if settings.use_depth:
            size_now = apparent_size(hand.image_pts, aspect)
            ratio = size_now / max(session.hand_size, 1e-4)
            # Log keeps push and pull symmetric: halving and doubling the
            # apparent size travel the same distance in opposite directions.
            amount = math.log(max(ratio, 1e-3))
            view_dir = rv3d.view_rotation @ Vector((0.0, 0.0, -1.0))
            reach = (session.pivot - rv3d.view_matrix.inverted()
                     .translation).length if rv3d.is_perspective else 10.0
            translation = translation + view_dir * (
                amount * settings.depth_sensitivity * max(reach, 0.1))

        return self._commit(Matrix.Translation(translation))

    def _apply_rotate(self, rv3d, hand, settings) -> bool:
        session = self.session
        frame_now = hand_orientation(hand.world_pts)
        try:
            relative = frame_now @ session.hand_frame.inverted()
        except ValueError:
            return False

        quat = relative.to_quaternion()
        if settings.rotate_sensitivity != 1.0:
            axis, angle = quat.to_axis_angle()
            quat = Quaternion(axis, angle * settings.rotate_sensitivity)

        # The hand frame is expressed in view axes; conjugating by the view
        # rotation re-expresses the same motion in world space, so the object
        # turns the way the hand turns no matter where the viewport is orbited.
        view_rot = rv3d.view_rotation
        world_quat = view_rot @ quat @ view_rot.inverted()
        return self._commit(world_quat.to_matrix().to_4x4())

    def _apply_scale(self, hand, settings, aspect) -> bool:
        session = self.session
        size_now = apparent_size(hand.image_pts, aspect)
        ratio = size_now / max(session.hand_size, 1e-4)
        factor = math.exp(math.log(max(ratio, 1e-3))
                          * settings.scale_sensitivity)
        factor = max(0.01, min(factor, 100.0))
        return self._commit(Matrix.Diagonal(
            (factor, factor, factor, 1.0)))

    def _apply_two_hand(self, region, rv3d, requests, settings) -> bool:
        session = self.session
        a, b = requests[0][0], requests[1][0]
        pa = to_region(a.image_pts[LM.INDEX_TIP], region,
                       mirror=settings.mirror)
        pb = to_region(b.image_pts[LM.INDEX_TIP], region,
                       mirror=settings.mirror)
        delta = pb - pa

        distance = max(delta.length, 1.0)
        factor = distance / session.two_hand_distance
        factor = max(0.01, min(factor, 100.0))

        angle = math.atan2(delta.y, delta.x) - session.two_hand_angle
        # Unwrap so passing through +/-pi does not spin the object 360 degrees.
        angle = (angle + math.pi) % (2.0 * math.pi) - math.pi

        view_axis = rv3d.view_rotation @ Vector((0.0, 0.0, 1.0))
        rotation = Quaternion(view_axis, angle).to_matrix().to_4x4()
        scale = Matrix.Diagonal((factor, factor, factor, 1.0))

        combined = rotation @ scale

        if settings.two_hand_translate:
            from bpy_extras import view3d_utils
            mid_now = (pa + pb) * 0.5
            screen_delta = mid_now - session.two_hand_mid
            target_2d = session.pivot_2d + screen_delta
            new_pivot = view3d_utils.region_2d_to_location_3d(
                region, rv3d, target_2d, session.pivot)
            if new_pivot is not None:
                combined = Matrix.Translation(
                    new_pivot - session.pivot) @ combined

        return self._commit(combined)

    # -- reporting ---------------------------------------------------------

    def describe(self, snapshot) -> str:
        if not snapshot.hands:
            return "No hands detected"
        parts = []
        for hand in snapshot.hands:
            state = self.hands[hand.slot] if hand.slot < len(self.hands) \
                else None
            tag = ""
            if state is not None:
                engaged = [name for name, tip in
                           (("index", LM.INDEX_TIP), ("middle", LM.MIDDLE_TIP),
                            ("ring", LM.RING_TIP))
                           if state.pinches[tip].engaged]
                tag = " +".join(engaged)
            parts.append(f"{hand.handedness}{' [' + tag + ']' if tag else ''}")
        return ", ".join(parts)
