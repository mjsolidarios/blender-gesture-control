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
   `hand_gesture_control-1.1.0.zip`, and enable it.
2. Still in Preferences, open the add-on's panel and press
   **Install Dependencies**. This pip-installs MediaPipe and a headless OpenCV
   into a private folder — a few minutes and roughly 400 MB.
3. Press **Download Model** to fetch `hand_landmarker.task` (~7 MB).
4. Press **Re-check**. Everything should show a checkmark.

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

1. Select one or more objects.
2. Open the viewport sidebar (**N**) and choose the **Gesture** tab.
3. Press **Start Tracking**.
4. Press **Esc** in the viewport to stop.

### Gestures

A pinch is a clutch: nothing moves until your thumb and fingertip meet, and
motion stops the moment they part. Which fingertip you pinch with picks the
channel.

| Gesture | Effect |
|---|---|
| Thumb + **index** | Move in the view plane. Push your hand toward or away from the camera to move along the view axis. |
| Thumb + **middle** | Rotate. The object copies your hand's change in orientation on all three axes. |
| Thumb + **ring** | Scale. Hand toward the camera grows, away shrinks. |
| **Both** index fingers | Two-handed: spread to scale, twist to roll, move both together to translate. |

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
- **Pinch rings** — a yellow ring that tightens as thumb and fingertip
  approach, turning solid green with a connecting line the instant the pinch
  engages. This makes the threshold visible rather than something you have to
  guess at.
- **Palm anchor** — the white ring marking the exact point that drives the move
  gesture.
- **Hold line** — while a gesture is live, a green line runs from your hand to
  the pivot of whatever you are holding.
- **Landmark numbers** — optional 0–20 badges (**Visual Indicators >
  Landmark Numbers**), useful when tuning or debugging.
- **Camera preview** — the live feed in a viewport corner with the same
  skeleton drawn on it, plus camera index, frame rate and inference latency.

The overlay draws only in the viewport you started tracking from, so a
quad-view or a second window will not show duplicate hands.

---

## Tuning

**Gestures panel**

- *Pinch Closes* / *Pinch Opens* — the hysteresis band, measured as
  thumb-to-fingertip distance divided by hand size, so it is independent of how
  far you sit from the camera. Keep "Opens" above "Closes"; widening the gap
  stops the grip flickering, narrowing it makes the clutch feel snappier.
- *Move / Rotate / Scale* — sensitivity multipliers.

**Camera & Tracking panel**

- *Smooth Landmarks* — a One Euro filter. *Steadiness* (lower = smoother when
  still) and *Responsiveness* (higher = less lag when moving fast). If the
  overlay trembles while your hand is still, lower Steadiness; if dragging
  feels laggy, raise Responsiveness.
- *Detection / Presence / Tracking* confidence — raise if phantom hands appear,
  lower if your hand drops out.
- Camera settings apply when tracking restarts; the panel says so when a change
  is pending.
- Camera detection results stay visible in the panel, and each settings section
  has a reset action for quickly returning to the recommended defaults.
- Camera preview visibility and size can be adjusted while tracking. Mirror
  View keeps the preview, landmarks, and gesture direction in sync.

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
