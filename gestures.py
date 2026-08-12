# SPDX-License-Identifier: GPL-3.0-or-later
"""
Gesture recognition and the transform engine that acts on it.

Interaction model
-----------------
A full-hand **pick** is the clutch: thumb plus index, middle, ring and pinky
all gather together. Nothing moves until that grab closes, and motion stops
the instant it opens.

======================  ==========================================
Gesture                 Effect
======================  ==========================================
Pick (thumb + all       Move in the view plane. Push the hand
 four fingertips)       toward / away from the camera for depth.
                        Rotate the hand while holding to rotate
                        the object on all three axes.
Both hands picking      Scale: spread or close the hands. Optional
                        twist (roll) and two-handed move.
Index finger extended   Point and dwell to toggle selection.
======================  ==========================================

There is no single-hand scale channel. Scaling always needs both hands.

Two mechanisms keep this usable rather than twitchy:

* **Hysteresis.** The pick engages at a tighter threshold than it releases at,
  so a hand hovering near the boundary does not chatter on and off.
* **Relative deltas.** Every gesture records the pose of the hand *and* of the
  objects at the moment of engagement, then applies the difference. Absolute
  mappings would make the object leap to the hand as soon as you grabbed.
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
MODE_GRAB = "GRAB"          # single-hand pick: move + rotate
MODE_TWO_HAND = "TWO_HAND"  # both hands: scale (+ optional roll / translate)

# Kept as aliases so older overlays / docs snippets do not hard-crash.
MODE_MOVE = MODE_GRAB
MODE_ROTATE = MODE_GRAB
MODE_SCALE = MODE_TWO_HAND

MODE_LABELS = {
    MODE_NONE: "Idle",
    MODE_GRAB: "Move / Rotate",
    MODE_TWO_HAND: "Scale",
}

#: After a pointing pose ends, wait this long before a pick may re-arm.
_PINCH_REARM_SEC = 0.25


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


def pick_ratio(world_pts) -> float:
    """
    How closed a full-hand pick is (lower = tighter grab).

    Mean of thumb-to-tip distance for index, middle, ring and pinky, so the
    clutch only engages when the whole hand gathers toward the thumb — not a
    single-finger pinch.
    """
    tips = LM.PICK_TIPS
    total = 0.0
    for tip in tips:
        total += pinch_ratio(world_pts, tip)
    return total / float(len(tips))


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
# Per-hand pick / grab state
# ---------------------------------------------------------------------------


class PinchState:
    """Hysteretic latch for a continuous ratio (pick closeness or a tip)."""

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
    """Tracks the full-hand pick latch for one hand slot across frames."""

    def __init__(self):
        self.grab = PinchState()
        # Per-tip ratios for overlay feedback (not used as separate channels).
        self.pinches = {tip: PinchState() for tip in LM.PICK_TIPS}
        self.present = False

    def update(self, world_pts, on_threshold, off_threshold,
               allow_grab: bool = True) -> bool:
        """
        Advance the pick latch.

        When ``allow_grab`` is False (pointing pose / re-arm cooldown), tip
        ratios still update for the overlay but the grab is forced open.
        """
        for tip, state in self.pinches.items():
            state.ratio = pinch_ratio(world_pts, tip)
            # Tips no longer drive modes; keep them visually in sync with grab.
            state.engaged = False

        ratio = pick_ratio(world_pts)
        if not allow_grab:
            self.grab.ratio = ratio
            self.grab.engaged = False
            return False

        engaged = self.grab.update(ratio, on_threshold, off_threshold)
        if engaged:
            for state in self.pinches.values():
                state.engaged = True
        return engaged

    def reset(self):
        self.grab.engaged = False
        self.grab.ratio = 1.0
        for state in self.pinches.values():
            state.engaged = False
            state.ratio = 1.0


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
        self.point_2d = None
        self.pointed_object = None
        self.point_progress = 0.0
        self._point_candidate = None
        self._point_started = 0.0
        self._point_committed = None
        # Per-slot monotonic time when transform pinches may engage again.
        self._pinch_unlock_at = [0.0] * 4

    # -- public API --------------------------------------------------------

    def reset(self):
        for hand in self.hands:
            hand.reset()
        self.session = None
        self.mode = MODE_NONE
        self.status = "Idle"
        self._pinch_unlock_at = [0.0] * 4
        self._clear_pointing()

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
        now = float(snapshot.timestamp)

        # Which hands are actively picking this frame?
        grabbing = []
        seen = set()
        for hand in snapshot.hands:
            if hand.slot >= len(self.hands):
                continue
            seen.add(hand.slot)
            state = self.hands[hand.slot]
            state.present = True

            allow = LM.grab_allowed(hand.world_pts)
            if not allow:
                self._pinch_unlock_at[hand.slot] = now + _PINCH_REARM_SEC
            elif now < self._pinch_unlock_at[hand.slot]:
                allow = False

            if state.update(hand.world_pts, on_threshold, off_threshold,
                            allow_grab=allow):
                grabbing.append(hand)

        for slot, state in enumerate(self.hands):
            if slot not in seen:
                state.present = False
                state.reset()
                if slot < len(self._pinch_unlock_at):
                    self._pinch_unlock_at[slot] = 0.0

        use_two = getattr(settings, "use_two_hand", True)
        if use_two and len(grabbing) >= 2:
            desired = MODE_TWO_HAND
            # Stable order by slot so scale distance does not flip hands.
            grabbing = sorted(grabbing, key=lambda h: h.slot)[:2]
        elif grabbing:
            desired = MODE_GRAB
            grabbing = grabbing[:1]
        else:
            desired = MODE_NONE

        # Releasing, or switching channel, ends the current session.
        if self.session is not None and desired != self.session.mode:
            self.end_session()

        if desired == MODE_NONE:
            self.mode = MODE_NONE
            if getattr(settings, "use_point_select", True):
                try:
                    if self._update_point_selection(
                            context, region, rv3d, snapshot, settings):
                        return False
                except Exception as exc:              # pragma: no cover
                    self.last_error = str(exc)
                    self.status = f"Point selection error: {exc}"
                    self._clear_pointing()
                    return False
            else:
                self._clear_pointing()
            if not snapshot.hands:
                self.status = "Show your hand to the camera"
            elif not self._targets(context):
                if getattr(settings, "use_point_select", True):
                    self.status = "Point to select an object, or select one"
                else:
                    self.status = "Select an object to control"
            else:
                self.status = "Pick (all fingers) to move · two hands to scale"
            return False

        self._clear_pointing()

        objects = self._targets(context)
        if not objects:
            self.mode = desired
            if getattr(settings, "use_point_select", True):
                self.status = "Grabbing, but nothing selected — point to pick"
            else:
                self.status = "Grabbing, but nothing selected"
            return False

        if self.session is None:
            self.session = self._begin_session(context, region, rv3d, desired,
                                               objects, grabbing, snapshot,
                                               settings, aspect)
            if self.session is None:
                return False

        self.mode = desired
        try:
            if desired == MODE_TWO_HAND:
                return self._apply_two_hand(region, rv3d, grabbing, settings)
            return self._apply_grab(region, rv3d, grabbing[0], settings,
                                    aspect)
        except Exception as exc:                  # pragma: no cover
            self.last_error = str(exc)
            self.status = f"Gesture error: {exc}"
            self.end_session()
        return False

    # -- point selection --------------------------------------------------

    def _clear_pointing(self):
        self.point_2d = None
        self.pointed_object = None
        self.point_progress = 0.0
        self._point_candidate = None
        self._point_started = 0.0
        self._point_committed = None

    def _update_point_selection(self, context, region, rv3d, snapshot,
                                settings) -> bool:
        """Dwell the index pointer over an object to toggle its selection."""
        pointing = [hand for hand in snapshot.hands
                    if LM.is_pointing_pose(hand.world_pts)]
        if len(pointing) != 1:
            self._clear_pointing()
            return False

        hand = pointing[0]
        self.point_2d = to_region(hand.image_pts[LM.INDEX_TIP], region,
                                  mirror=settings.mirror)
        if context.mode != "OBJECT":
            self.pointed_object = None
            self.point_progress = 0.0
            self._point_candidate = None
            self._point_committed = None
            self.status = "Point selection requires Object Mode"
            return True

        candidate = self._raycast_object(context, region, rv3d, self.point_2d)
        self.pointed_object = candidate
        if candidate is None:
            self.point_progress = 0.0
            self._point_candidate = None
            self._point_committed = None
            self.status = "Point at a visible object"
            return True

        now = snapshot.timestamp
        if candidate != self._point_candidate:
            self._point_candidate = candidate
            self._point_started = now
            self._point_committed = None
            self.point_progress = 0.0

        if candidate == self._point_committed:
            self.point_progress = 1.0
            is_sel = candidate.select_get(view_layer=context.view_layer)
            self.status = f"{'Selected' if is_sel else 'Deselected'}: {candidate.name}"
            return True

        dwell = max(float(getattr(settings, "point_select_dwell", 0.6)),
                    0.05)
        self.point_progress = max(
            0.0, min((now - self._point_started) / dwell, 1.0))
        if self.point_progress >= 1.0:
            was_selected = candidate.select_get(view_layer=context.view_layer)
            if self._toggle_object(context, candidate):
                self._point_committed = candidate
                verb = "Deselected" if was_selected else "Selected"
                self.status = f"{verb}: {candidate.name}"
            else:
                self.status = f"Could not toggle {candidate.name}"
            return True

        percent = round(self.point_progress * 100.0)
        self.status = f"Pointing at {candidate.name} - {percent}%"
        return True

    def _raycast_object(self, context, region, rv3d, coordinate):
        """Return the selectable original object under a region coordinate."""
        from bpy_extras import view3d_utils

        origin = view3d_utils.region_2d_to_origin_3d(
            region, rv3d, coordinate)
        direction = view3d_utils.region_2d_to_vector_3d(
            region, rv3d, coordinate)
        if origin is None or direction is None or direction.length < 1e-6:
            return None

        depsgraph = context.evaluated_depsgraph_get()
        hit, _location, _normal, _face, hit_object, _matrix = \
            context.scene.ray_cast(depsgraph, origin, direction.normalized())
        if not hit or hit_object is None:
            return None

        try:
            original = hit_object.original
        except (AttributeError, ReferenceError):
            original = hit_object
        obj = context.view_layer.objects.get(original.name)
        if obj is None or obj.hide_select:
            return None
        try:
            if not obj.visible_get(view_layer=context.view_layer):
                return None
        except (ReferenceError, TypeError):
            return None
        return obj

    def _toggle_object(self, context, obj) -> bool:
        """Toggle selection of obj (add if unselected, remove if selected).

        When selecting, make it the active object. When deselecting the active
        object, choose another remaining selected object as active if possible.
        """
        try:
            currently = obj.select_get(view_layer=context.view_layer)
            if currently:
                # Deselect
                obj.select_set(False, view_layer=context.view_layer)
                if context.view_layer.objects.active == obj:
                    remaining = [
                        o for o in context.selected_objects
                        if o != obj and o.select_get(view_layer=context.view_layer)
                    ]
                    context.view_layer.objects.active = remaining[0] if remaining else None
                return True
            else:
                # Add to selection and make active
                obj.select_set(True, view_layer=context.view_layer)
                context.view_layer.objects.active = obj
                return True
        except (AttributeError, RuntimeError, ReferenceError):
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

    def _begin_session(self, context, region, rv3d, mode, objects, hands,
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
            a, b = hands[0], hands[1]
            pa = self._hand_anchor_2d(a, region, settings)
            pb = self._hand_anchor_2d(b, region, settings)
            delta = pb - pa
            session.two_hand_distance = max(delta.length, 1.0)
            session.two_hand_angle = math.atan2(delta.y, delta.x)
            session.two_hand_mid = (pa + pb) * 0.5
        else:
            hand = hands[0]
            cx, cy = palm_center(hand.image_pts)
            session.hand_2d = to_region((cx, cy, 0.0), region,
                                        mirror=settings.mirror)
            session.hand_size = apparent_size(hand.image_pts, aspect)
            session.hand_frame = hand_orientation(hand.world_pts)

        self.status = f"{MODE_LABELS[mode]} - {len(objects)} object(s)"
        return session

    def _hand_anchor_2d(self, hand, region, settings):
        """Screen-space point used for two-hand distance / mid (palm centre)."""
        cx, cy = palm_center(hand.image_pts)
        return to_region((cx, cy, 0.0), region, mirror=settings.mirror)

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

    def _grab_translation(self, region, rv3d, hand, settings, aspect):
        """World-space translation from palm motion (+ optional depth)."""
        from bpy_extras import view3d_utils

        session = self.session
        cx, cy = palm_center(hand.image_pts)
        now_2d = to_region((cx, cy, 0.0), region,
                           mirror=settings.mirror)
        screen_delta = (now_2d - session.hand_2d) * settings.move_sensitivity

        target_2d = session.pivot_2d + screen_delta
        # Unproject at the pivot's own depth so dragging tracks the hand
        # exactly, in perspective as well as orthographic views.
        new_pivot = view3d_utils.region_2d_to_location_3d(
            region, rv3d, target_2d, session.pivot)
        if new_pivot is None:
            return Vector((0.0, 0.0, 0.0))
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
        return translation

    def _grab_rotation(self, rv3d, hand, settings):
        """World-space rotation from the change in hand orientation."""
        session = self.session
        frame_now = hand_orientation(hand.world_pts)
        try:
            # Delta that takes the start frame to the current frame.
            relative = frame_now @ session.hand_frame.inverted()
        except ValueError:
            return Matrix.Identity(4)

        quat = relative.to_quaternion()
        # Invert so the object turns the same way the hand turns. Applying the
        # raw frame delta maps the opposite sense onto the object.
        quat.invert()

        if settings.rotate_sensitivity != 1.0:
            axis, angle = quat.to_axis_angle()
            quat = Quaternion(axis, angle * settings.rotate_sensitivity)

        # The hand frame is expressed in view axes; conjugating by the view
        # rotation re-expresses the same motion in world space, so the object
        # turns the way the hand turns no matter where the viewport is orbited.
        view_rot = rv3d.view_rotation
        world_quat = view_rot @ quat @ view_rot.inverted()
        return world_quat.to_matrix().to_4x4()

    def _apply_grab(self, region, rv3d, hand, settings, aspect) -> bool:
        """
        Single-hand pick: move from palm translation, rotate from hand twist.

        Both channels share one clutch so a natural pick-and-turn feels like
        holding the object.
        """
        translation = self._grab_translation(region, rv3d, hand, settings,
                                             aspect)
        rotation = self._grab_rotation(rv3d, hand, settings)
        combined = Matrix.Translation(translation) @ rotation

        # Status reflects which channel is doing useful work this frame.
        rot_angle = rotation.to_quaternion().angle
        moved = translation.length > 1e-5
        if moved and rot_angle > 0.02:
            label = "Move / Rotate"
        elif rot_angle > 0.02:
            label = "Rotate"
        else:
            label = "Move"
        self.status = f"{label} - {len(self.session.objects)} object(s)"
        return self._commit(combined)

    def _apply_two_hand(self, region, rv3d, hands, settings) -> bool:
        """Both hands picking: scale from separation (optional roll / move)."""
        session = self.session
        a, b = hands[0], hands[1]
        pa = self._hand_anchor_2d(a, region, settings)
        pb = self._hand_anchor_2d(b, region, settings)
        delta = pb - pa

        distance = max(delta.length, 1.0)
        factor = distance / session.two_hand_distance
        # Two-hand scale reuses scale_sensitivity as a log-ish gain on the
        # separation ratio so the same slider still feels right.
        if settings.scale_sensitivity != 1.0:
            factor = math.exp(math.log(max(factor, 1e-3))
                              * settings.scale_sensitivity)
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

        self.status = f"Scale - {len(session.objects)} object(s)"
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
            if state is not None and state.grab.engaged:
                tag = " pick"
            parts.append(f"{hand.handedness}{tag}")
        return ", ".join(parts)
