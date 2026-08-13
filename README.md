# Hand Gesture Control

Control Blender objects with your hands over a webcam, using Google's
**MediaPipe Tasks Vision Hand Landmarker**. All 21 hand landmarks are drawn as
live viewport indicators, so you can always see exactly what the model is
tracking.

Built and tested against **Blender 5.2 LTS** (Python 3.13); requires Blender
4.2 or newer.

---

## Install

1. **Edit > Preferences > Add-ons > Install from Disk**, pick
   `hand_gesture_control-1.2.0.zip`, and enable it.
2. Still in Preferences, open the add-on's panel and press **Set Up Now**.
   This installs MediaPipe, a headless OpenCV, and downloads the hand model
   in one step — a few minutes and roughly 400 MB.
3. Press **Re-check**. Everything should show a checkmark.

Blender 4.2+ blocks add-on network access by default. If the installer refuses
to run, enable **Preferences > System > Network > Allow Online Access** first.

Nothing is written inside the add-on folder, so updating or reinstalling the
add-on does not force you to download the dependencies again.

| What | Where |
|---|---|
| Packages | `<Blender user scripts>/addon_deps/hand_gesture_control/` |
| Model | `<Blender user data>/hand_gesture_control/hand_landmarker.task` |

---

## Use

1. Open the viewport sidebar (**N**) and choose the **Gesture** tab.
2. Select one or more objects, or start tracking and **point** with your
   index finger to select objects.
3. Press **Start Tracking**. The main panel shows the gesture map. A **pick**
   (thumb gathering all four fingertips) is the clutch — nothing moves until
   it closes.
4. Press **Esc** in the viewport (or **Stop Tracking**) to stop.

If you change camera settings while tracking, press **Apply & Restart
Tracking** so they take effect.

### Gestures

| Gesture | Effect |
|---|---|
| **Pick** — thumb + index + middle + ring + pinky | Move in the view plane. Push toward or away from the camera for depth. |
| **Pick + twist the hand** | Rotate. The object copies your hand's change in orientation on all three axes. |
| **Both hands picking** | Scale: spread the hands to grow, close them to shrink. Optional twist (roll) and two-handed move. |
| **Index extended**, other fingers folded | Point at an object and dwell to toggle its selection. Supports multi-object select / deselect. |

Optional: **Single-hand scale** — spread thumb and index apart (like a phone
zoom) to scale with one hand. Enable in Gestures > Single-Hand Scale.

Transforms apply to every selected object, pivoting on the active one. Each
completed gesture is a single undo step.

---

## The visual indicators

Every one of the 21 landmarks is drawn every frame, in two places at once: over
the viewport, and inside the live camera preview.

- **Joints** — all 21, coloured by finger: palm/wrist white, thumb orange,
  index yellow, middle green, ring blue, pinky violet. Landmarks a gesture
  reads from (wrist, knuckles, fingertips) are drawn larger. Each has a dark
  halo so it stays legible over light scenes.
- **Bones** — all 21 connections of the MediaPipe hand topology, faded from the
  proximal to the distal joint so finger direction is readable at a glance.
- **Pick readiness arc** — a radial progress arc around the palm that fills
  as fingers approach the thumb, giving continuous feedback on how close you
  are to engaging the pick clutch. Turns solid green when engaged.
- **Palm anchor** — the white ring marking the exact point that drives the move
  gesture.
- **Dwell progress arc** — while pointing at an object, a radial arc around
  the fingertip fills to show how much dwell time remains.
- **Hold line** — while a gesture is live, a green line runs from your hand to
  the pivot of whatever you are holding.
- **Object highlight** — when a pick engages, a bounding-rect outline briefly
  highlights the affected objects.
- **Gesture badge** — the HUD shows a `[GRAB]`, `[SCALE]`, or `[IDLE]` badge
  so you always know which gesture the system recognises.
- **Landmark numbers** — optional 0–20 badges (**Visual Indicators >
  Landmark Numbers**), useful when tuning or debugging.
- **Camera preview** — the live feed in a viewport corner with the same
  skeleton drawn on it, plus camera index, frame rate and inference latency.

The overlay draws only in the viewport you started tracking from, so a
quad-view or a second window will not show duplicate hands.

---

## Tuning

**Gestures panel**

- *Point to Select* — extend the index and fold the other fingers to use the
  fingertip as a viewport pointer (the pick grab is blocked while pointing).
  Hold it over an object for *Selection Dwell* seconds; the pointer rings turn
  green as the dwell completes. Available in Object Mode; toggles the object
  under the pointer (add or remove), including multi-object select / deselect.
- *Tap to Select* — when enabled, curling the index finger while pointing
  instantly toggles selection without waiting for the dwell timer.
- *Snap to Nearest* — magnetises the pointing ray to the nearest object
  within the snap radius, so you do not need sub-pixel steadiness.
- *Pick Closes* / *Pick Opens* — the hysteresis band for the full-hand pick,
  measured as the average thumb-to-tip distance (index, middle, ring, pinky)
  divided by hand size. Keep "Opens" above "Closes"; widening the gap stops
  the grip flickering.
- *Move / Rotate / Scale* — sensitivity multipliers (scale applies to
  two-handed gestures only).
- *Sticky Pick* — when enabled, a quick pick-and-release locks the grab on;
  a second pick-and-release unlocks it. Reduces hand fatigue.
- *Dead Zone* — the hand must move this far (normalised) after a pick engages
  before the object starts following. Prevents initial grab jitter.
- *Velocity Curve* — slow hand motions map to fine-grained object movement;
  fast motions map to larger translations.
- *Hand Loss Grace* — seconds to hold the last object state when a hand
  disappears mid-gesture before dropping the transform.
- *Preset Profile* — named bundles (Precise Modeling, Fast Layout,
  Presentation) that set sensitivity, smoothing, and threshold values.

**Camera & Tracking panel**

- *Camera* — pick a device from the list (cameras are auto-detected on first
  start; press **Detect Cameras** to refresh). Choose **Other index…** for a
  manual index.
- *Resolution* — Fast / Default / Detail presets, or Custom width and height.
- *Smooth Landmarks* — a One Euro filter. *Steadiness* (lower = smoother when
  still) and *Responsiveness* (higher = less lag when moving fast). If the
  overlay trembles while your hand is still, lower Steadiness; if dragging
  feels laggy, raise Responsiveness.
- *Detection / Presence / Tracking* confidence — raise if phantom hands appear,
  lower if your hand drops out.
- Camera settings apply when tracking restarts. While tracking, pending
  changes show **Apply & Restart Tracking**.
- Each settings section has a reset action for recommended defaults.
- Camera preview visibility and size can be adjusted while tracking. Mirror
  View keeps the preview, landmarks, and gesture direction in sync.

**Visual Indicators panel**

- *Overlay Detail* — **Full** draws the complete skeleton; **Minimal** keeps
  key joints, pinch rings, and the status readout only.

---

## Quick access

Press **Shift+G** in the 3D Viewport to open a pie menu with Start/Stop,
Point to Select, Mirror, and Camera Preview toggles.

---

## Troubleshooting

**The camera will not open.** Press *Detect Cameras* to see which indices
respond. Close anything else using the webcam. On Windows try the *DirectShow*
backend, on Linux *V4L2*.

**Tracking is slow.** Lower the capture resolution, set *Max Hands* to 1, or
reduce *Update Rate*. Inference runs on a background thread, so a slow camera
degrades tracking rather than freezing Blender.

**The preview looks washed out or too dark.** The frame is uploaded without a
colour transform, which is correct for a standard sRGB webcam. An unusual
display setup can make it look off; turn the preview off and use the viewport
skeleton instead.

**The overlay is too busy.** Lower *Opacity* and *Joint Size*, turn off
*Hand Skeleton* and rely on the camera preview, or turn off *Landmark Numbers*.

**Blender is unstable after installing.** Check the preferences panel — if
OpenCV shows "(GUI build)", MediaPipe's Qt-linked OpenCV won the install race.
Press *Install Dependencies* again to force the headless build over it.

---

## Notes on the implementation

- Camera capture and MediaPipe inference run on background threads and never
  touch `bpy`. Results are published as an immutable snapshot picked up by a
  modal operator's timer on Blender's main thread.
- Dropped frames are preferred over a stalled UI: the capture thread never
  waits on inference.
- Landmark topology is hardcoded rather than imported from
  `mediapipe.solutions`, which was removed in MediaPipe 0.10.30+.
- The rotation gesture builds an orthonormal frame from the three palm
  landmarks (wrist, index MCP, pinky MCP) using MediaPipe's *world* landmarks,
  then conjugates it by the viewport's rotation — so the object turns the way
  your hand turns regardless of where the view is orbited.
- Movement unprojects at the pivot's own depth, so dragging tracks your hand
  exactly in perspective as well as orthographic views.

Licence: GPL-3.0-or-later.
