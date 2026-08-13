# SPDX-License-Identifier: GPL-3.0-or-later
"""
Camera capture and MediaPipe Hand Landmarker inference, off the main thread.

Threading model
---------------
Three threads are involved and only one of them is ever allowed to touch ``bpy``:

* **Capture thread** (owned here) - blocks on ``cv2.VideoCapture.read()`` and
  hands each frame to ``HandLandmarker.detect_async``.
* **MediaPipe worker** (owned by MediaPipe) - invokes ``_on_result`` when
  inference for a frame completes.
* **Blender main thread** - calls :meth:`HandTracker.latest` from a modal
  operator timer.

Producer and consumer never block on each other: results are published by
swapping a single immutable snapshot object under a short-lived lock. A dropped
frame is always preferable to a stalled UI, so the capture thread also never
waits for inference.
"""

from __future__ import annotations

import threading
import time
import traceback

from . import deps
from .landmarks import NUM_LANDMARKS

# ---------------------------------------------------------------------------
# Snapshot types
# ---------------------------------------------------------------------------


class HandSnapshot:
    """One detected hand at one instant. Immutable once constructed."""

    __slots__ = ("slot", "handedness", "score", "image_pts", "world_pts")

    def __init__(self, slot, handedness, score, image_pts, world_pts):
        self.slot = slot                # stable index 0..num_hands-1
        self.handedness = handedness    # "Left" / "Right" / "?"
        self.score = score              # classification confidence
        self.image_pts = image_pts      # [(x, y, z)] x21, normalised 0..1
        self.world_pts = world_pts      # [(x, y, z)] x21, metres, wrist origin


class FrameSnapshot:
    """Everything the UI thread needs about one processed frame."""

    __slots__ = ("hands", "timestamp", "frame", "frame_id", "width", "height",
                 "latency_ms")

    def __init__(self, hands, timestamp, frame, frame_id, width, height,
                 latency_ms):
        self.hands = hands
        self.timestamp = timestamp
        self.frame = frame          # numpy uint8 RGB, may be None
        self.frame_id = frame_id
        self.width = width
        self.height = height
        self.latency_ms = latency_ms


EMPTY = FrameSnapshot((), 0.0, None, -1, 0, 0, 0.0)


# ---------------------------------------------------------------------------
# Tracker
# ---------------------------------------------------------------------------


class HandTracker:
    """Owns the camera and the landmarker for the lifetime of a session."""

    def __init__(self, camera_index=0, backend="AUTO", num_hands=2,
                 det_conf=0.5, presence_conf=0.5, track_conf=0.5,
                 capture_width=640, capture_height=480,
                 preview_width=320, want_preview=True, mirror=True):
        self.camera_index = int(camera_index)
        self.backend = backend
        self.num_hands = max(1, min(int(num_hands), 4))
        self.det_conf = float(det_conf)
        self.presence_conf = float(presence_conf)
        self.track_conf = float(track_conf)
        self.capture_width = int(capture_width)
        self.capture_height = int(capture_height)
        self.preview_width = int(preview_width)
        self.want_preview = bool(want_preview)
        self.mirror = bool(mirror)

        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._snapshot = EMPTY
        self._pending = {}          # frame_id -> (rgb_small, sent_at)
        self._pending_lock = threading.Lock()
        self._error = None
        self._running = False
        self._fps = 0.0
        self._landmarker = None
        self._capture = None

    # -- lifecycle ---------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._running

    @property
    def error(self):
        return self._error

    @property
    def fps(self) -> float:
        return self._fps

    def start(self) -> bool:
        if self._running:
            return True
        self._stop.clear()
        self._error = None
        self._snapshot = EMPTY
        self._thread = threading.Thread(target=self._run, name="HGC-Capture",
                                        daemon=True)
        self._running = True
        self._thread.start()
        # Give the camera a moment so the UI can report a failure immediately
        # rather than showing a blank overlay for several seconds.
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            if self._error or self._snapshot is not EMPTY:
                break
            time.sleep(0.03)
        return self._error is None

    def stop(self):
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
        self._thread = None
        self._running = False

    def latest(self) -> FrameSnapshot:
        """Most recent completed frame. Safe to call from the main thread."""
        with self._lock:
            return self._snapshot

    # -- internals ---------------------------------------------------------

    def _open_capture(self):
        import cv2

        flags = {
            "AUTO": 0,
            "V4L2": getattr(cv2, "CAP_V4L2", 200),
            "DSHOW": getattr(cv2, "CAP_DSHOW", 700),
            "MSMF": getattr(cv2, "CAP_MSMF", 1400),
            "AVFOUNDATION": getattr(cv2, "CAP_AVFOUNDATION", 1200),
            "GSTREAMER": getattr(cv2, "CAP_GSTREAMER", 1800),
        }
        api = flags.get(self.backend, 0)

        cap = cv2.VideoCapture(self.camera_index, api) if api else \
            cv2.VideoCapture(self.camera_index)

        if not cap.isOpened():
            cap.release()
            raise RuntimeError(
                f"could not open camera index {self.camera_index}"
                + (f" with backend {self.backend}" if api else "")
                + ". Check that no other application is using the webcam and "
                  "that Blender has camera permission.")

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.capture_width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.capture_height)
        # A one-frame buffer keeps latency low; not every backend honours it.
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        return cap

    def _build_landmarker(self):
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision

        options = vision.HandLandmarkerOptions(
            base_options=mp_python.BaseOptions(
                model_asset_path=deps.model_path()),
            running_mode=vision.RunningMode.LIVE_STREAM,
            num_hands=self.num_hands,
            min_hand_detection_confidence=self.det_conf,
            min_hand_presence_confidence=self.presence_conf,
            min_tracking_confidence=self.track_conf,
            result_callback=self._on_result,
        )
        return vision.HandLandmarker.create_from_options(options)

    def _on_result(self, result, output_image, timestamp_ms):
        """Called on a MediaPipe worker thread. Must not touch bpy."""
        try:
            now = time.monotonic()
            with self._pending_lock:
                rgb, sent_at = self._pending.pop(timestamp_ms, (None, now))
                # Drop stale entries if a frame's callback never arrived.
                if len(self._pending) > 8:
                    for key in sorted(self._pending)[:-4]:
                        self._pending.pop(key, None)

            hands = []
            landmark_lists = getattr(result, "hand_landmarks", None) or ()
            world_lists = getattr(result, "hand_world_landmarks", None) or ()
            handedness_lists = getattr(result, "handedness", None) or ()

            for i, lms in enumerate(landmark_lists):
                if len(lms) != NUM_LANDMARKS:
                    continue

                label, score = "?", 0.0
                if i < len(handedness_lists) and handedness_lists[i]:
                    cat = handedness_lists[i][0]
                    label = getattr(cat, "category_name", "?") or "?"
                    score = float(getattr(cat, "score", 0.0))
                    # MediaPipe labels handedness as seen by the camera. The
                    # preview and overlay are mirrored to read like a selfie,
                    # so flip the label to match what the user perceives.
                    if self.mirror:
                        label = {"Left": "Right", "Right": "Left"}.get(
                            label, label)

                image_pts = [(float(p.x), float(p.y), float(p.z))
                             for p in lms]
                if i < len(world_lists) and len(world_lists[i]) == NUM_LANDMARKS:
                    world_pts = [(float(p.x), float(p.y), float(p.z))
                                 for p in world_lists[i]]
                else:
                    world_pts = image_pts

                hands.append(HandSnapshot(i, label, score, image_pts,
                                          world_pts))

            # Sort by screen position so the same physical hand keeps the same
            # slot between frames even if MediaPipe reorders its output.
            hands.sort(key=lambda h: h.image_pts[0][0])
            for slot, hand in enumerate(hands):
                hand.slot = slot

            height = width = 0
            if rgb is not None:
                height, width = rgb.shape[0], rgb.shape[1]

            snapshot = FrameSnapshot(
                hands=tuple(hands),
                timestamp=now,
                frame=rgb,
                frame_id=timestamp_ms,
                width=width,
                height=height,
                latency_ms=(now - sent_at) * 1000.0,
            )
            with self._lock:
                self._snapshot = snapshot
        except Exception:
            self._error = traceback.format_exc(limit=6)

    def _run(self):
        cap = None
        landmarker = None
        try:
            deps.ensure_sys_path()
            import cv2
            import numpy as np
            import mediapipe as mp

            if not deps.model_present():
                raise RuntimeError(
                    "hand_landmarker.task is missing. Use 'Download Model' in "
                    "the add-on preferences.")

            cap = self._open_capture()
            self._capture = cap
            landmarker = self._build_landmarker()
            self._landmarker = landmarker

            frame_index = 0
            fps_window = []
            last_read = time.monotonic()

            while not self._stop.is_set():
                ok, frame_bgr = cap.read()
                if not ok or frame_bgr is None:
                    # Transient read failures happen on USB cameras.
                    elapsed = time.monotonic() - last_read
                    if elapsed > 3.0:
                        # Try to reconnect before giving up.
                        try:
                            cap.release()
                            cap = self._open_capture()
                            self._capture = cap
                            last_read = time.monotonic()
                            continue
                        except Exception:
                            raise RuntimeError(
                                "the camera stopped delivering frames "
                                "and reconnection failed")
                    time.sleep(0.01)
                    continue

                now = time.monotonic()
                fps_window.append(now)
                if len(fps_window) > 30:
                    fps_window.pop(0)
                if len(fps_window) > 1:
                    span = fps_window[-1] - fps_window[0]
                    self._fps = (len(fps_window) - 1) / span if span > 0 else 0.0
                last_read = now

                rgb_full = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)

                # Inference runs on the full captured frame; the preview copy is
                # downscaled separately so a large preview never costs accuracy
                # and a small one never costs upload bandwidth.
                preview = None
                want_preview = self.want_preview
                preview_width = self.preview_width
                if want_preview:
                    ph, pw = rgb_full.shape[0], rgb_full.shape[1]
                    if preview_width and pw > preview_width:
                        scale = preview_width / float(pw)
                        preview = cv2.resize(
                            rgb_full,
                            (preview_width, max(1, int(round(ph * scale)))),
                            interpolation=cv2.INTER_AREA)
                    else:
                        preview = rgb_full.copy()
                    if self.mirror:
                        preview = preview[:, ::-1]
                    # Blender textures are bottom-up; flip once here so the
                    # draw handler can upload without touching the data.
                    preview = np.ascontiguousarray(preview[::-1])

                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB,
                                    data=np.ascontiguousarray(rgb_full))
                frame_index += 1
                with self._pending_lock:
                    self._pending[frame_index] = (preview, now)

                # detect_async requires strictly increasing timestamps.
                landmarker.detect_async(mp_image, frame_index)

        except Exception:
            self._error = traceback.format_exc(limit=8)
        finally:
            self._running = False
            try:
                if landmarker is not None:
                    landmarker.close()
            except Exception:
                pass
            try:
                if cap is not None:
                    cap.release()
            except Exception:
                pass
            self._landmarker = None
            self._capture = None


# ---------------------------------------------------------------------------
# Camera probing
# ---------------------------------------------------------------------------


def probe_cameras(max_index: int = 6):
    """
    Return a list of ``(index, description)`` for cameras that opened.

    Called from the main thread by the preferences UI. Opening a camera is
    slow (up to ~1 s per miss on some backends) so this is never automatic.
    """
    deps.ensure_sys_path()
    try:
        import cv2
    except Exception:
        return []

    found = []
    for i in range(max_index):
        cap = None
        try:
            cap = cv2.VideoCapture(i)
            if cap.isOpened():
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
                found.append((i, f"Camera {i}  ({w}x{h})" if w else
                              f"Camera {i}"))
        except Exception:
            pass
        finally:
            if cap is not None:
                cap.release()
    return found
