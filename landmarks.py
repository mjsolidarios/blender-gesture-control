# SPDX-License-Identifier: GPL-3.0-or-later
"""
Static description of the MediaPipe Hand Landmarker topology.

MediaPipe >= 0.10.30 no longer ships the legacy ``mediapipe.solutions`` package,
so ``mp.solutions.hands.HAND_CONNECTIONS`` and ``HandLandmark`` are unavailable.
Everything the add-on needs about the 21-point hand model is defined here so the
add-on never depends on that removed module.

Landmark index reference (Google MediaPipe hand landmark model):

        8   12  16  20
        |   |   |   |
        7   11  15  19
        |   |   |   |     4
        6   10  14  18   /
        |   |   |   |   3
        5---9--13--17   |
         \\     |     /  2
          \\    |    /  /
           `---0---'--1
             (wrist)
"""

# ---------------------------------------------------------------------------
# Landmark identity
# ---------------------------------------------------------------------------

#: Canonical index -> name, matching the MediaPipe HandLandmark enum exactly.
LANDMARK_NAMES = (
    "WRIST",              # 0
    "THUMB_CMC",          # 1
    "THUMB_MCP",          # 2
    "THUMB_IP",           # 3
    "THUMB_TIP",          # 4
    "INDEX_FINGER_MCP",   # 5
    "INDEX_FINGER_PIP",   # 6
    "INDEX_FINGER_DIP",   # 7
    "INDEX_FINGER_TIP",   # 8
    "MIDDLE_FINGER_MCP",  # 9
    "MIDDLE_FINGER_PIP",  # 10
    "MIDDLE_FINGER_DIP",  # 11
    "MIDDLE_FINGER_TIP",  # 12
    "RING_FINGER_MCP",    # 13
    "RING_FINGER_PIP",    # 14
    "RING_FINGER_DIP",    # 15
    "RING_FINGER_TIP",    # 16
    "PINKY_MCP",          # 17
    "PINKY_PIP",          # 18
    "PINKY_DIP",          # 19
    "PINKY_TIP",          # 20
)

NUM_LANDMARKS = len(LANDMARK_NAMES)
assert NUM_LANDMARKS == 21

#: Short labels used by the on-screen index badges.
LANDMARK_SHORT = (
    "WRI", "CMC", "TMC", "TIP", "THU",
    "IMC", "IPP", "IDP", "IDX",
    "MMC", "MPP", "MDP", "MID",
    "RMC", "RPP", "RDP", "RNG",
    "PMC", "PPP", "PDP", "PKY",
)

# Named indices, so the rest of the add-on never uses magic numbers.
WRIST = 0
THUMB_CMC, THUMB_MCP, THUMB_IP, THUMB_TIP = 1, 2, 3, 4
INDEX_MCP, INDEX_PIP, INDEX_DIP, INDEX_TIP = 5, 6, 7, 8
MIDDLE_MCP, MIDDLE_PIP, MIDDLE_DIP, MIDDLE_TIP = 9, 10, 11, 12
RING_MCP, RING_PIP, RING_DIP, RING_TIP = 13, 14, 15, 16
PINKY_MCP, PINKY_PIP, PINKY_DIP, PINKY_TIP = 17, 18, 19, 20

#: Fingertip indices, in thumb -> pinky order.
FINGER_TIPS = (THUMB_TIP, INDEX_TIP, MIDDLE_TIP, RING_TIP, PINKY_TIP)

#: Knuckle (MCP) indices, in thumb -> pinky order.
FINGER_MCPS = (THUMB_MCP, INDEX_MCP, MIDDLE_MCP, RING_MCP, PINKY_MCP)

# ---------------------------------------------------------------------------
# Skeleton topology
# ---------------------------------------------------------------------------

#: The 21 bones of the hand skeleton, identical to MediaPipe's HAND_CONNECTIONS.
HAND_CONNECTIONS = (
    # thumb
    (0, 1), (1, 2), (2, 3), (3, 4),
    # index
    (0, 5), (5, 6), (6, 7), (7, 8),
    # middle
    (5, 9), (9, 10), (10, 11), (11, 12),
    # ring
    (9, 13), (13, 14), (14, 15), (15, 16),
    # pinky
    (13, 17), (17, 18), (18, 19), (19, 20),
    # palm closing edge
    (0, 17),
)

# ---------------------------------------------------------------------------
# Grouping and colour
# ---------------------------------------------------------------------------

#: Which chain each landmark belongs to.
PALM, THUMB, INDEX, MIDDLE, RING, PINKY = range(6)

FINGER_OF_LANDMARK = (
    PALM,                          # 0
    THUMB, THUMB, THUMB, THUMB,    # 1-4
    INDEX, INDEX, INDEX, INDEX,    # 5-8
    MIDDLE, MIDDLE, MIDDLE, MIDDLE,  # 9-12
    RING, RING, RING, RING,        # 13-16
    PINKY, PINKY, PINKY, PINKY,    # 17-20
)

FINGER_NAMES = ("Palm", "Thumb", "Index", "Middle", "Ring", "Pinky")

#: Per-chain RGB colours. Chosen to stay legible over both dark and light
#: viewport backgrounds, and to remain distinguishable under the most common
#: forms of colour vision deficiency (no red/green-only pairing).
FINGER_COLORS = (
    (0.95, 0.95, 0.98),  # palm / wrist  - near white
    (1.00, 0.45, 0.20),  # thumb         - orange
    (1.00, 0.85, 0.15),  # index         - yellow
    (0.35, 0.90, 0.45),  # middle        - green
    (0.30, 0.75, 1.00),  # ring          - sky blue
    (0.80, 0.50, 1.00),  # pinky         - violet
)


def landmark_color(index: int):
    """RGB tuple for the given landmark index."""
    return FINGER_COLORS[FINGER_OF_LANDMARK[index]]


#: Landmarks drawn larger because a gesture reads directly from them.
KEY_LANDMARKS = frozenset((WRIST, THUMB_TIP, INDEX_TIP, MIDDLE_TIP,
                           RING_TIP, PINKY_TIP, INDEX_MCP, MIDDLE_MCP,
                           PINKY_MCP))

#: Landmarks that define the palm plane, used to derive hand orientation.
PALM_BASIS = (WRIST, INDEX_MCP, PINKY_MCP)

#: Pairs used for pinch detection, keyed by the finger meeting the thumb.
PINCH_PAIRS = {
    "INDEX": (THUMB_TIP, INDEX_TIP),
    "MIDDLE": (THUMB_TIP, MIDDLE_TIP),
    "RING": (THUMB_TIP, RING_TIP),
    "PINKY": (THUMB_TIP, PINKY_TIP),
}
