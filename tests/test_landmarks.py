# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for pure landmark-pose recognition."""

import unittest

import landmarks as LM


def _hand(extended):
    points = [(0.0, 0.0, 0.0) for _ in range(LM.NUM_LANDMARKS)]
    points[LM.WRIST] = (0.0, -1.0, 0.0)
    # Thumb chain rests near the folded fingertips (common while pointing).
    points[LM.THUMB_CMC] = (-0.4, -0.2, 0.0)
    points[LM.THUMB_MCP] = (-0.3, 0.0, 0.0)
    points[LM.THUMB_IP] = (-0.2, 0.15, 0.0)
    points[LM.THUMB_TIP] = (-0.05, 0.25, 0.0)
    for index, chain in enumerate(LM.FINGER_CHAINS):
        x = float(index) - 1.5
        mcp, pip, dip, tip = chain
        points[mcp] = (x, 0.0, 0.0)
        points[pip] = (x, 1.0, 0.0)
        if index in extended:
            points[dip] = (x, 2.0, 0.0)
            points[tip] = (x, 3.0, 0.0)
        else:
            points[dip] = (x, 0.7, 0.0)
            points[tip] = (x, 0.3, 0.0)
    return points


def _transform(points):
    """Rotate in-plane, scale, and translate a synthetic hand."""
    return [(10.0 - point[1] * 2.5,
             -4.0 + point[0] * 2.5,
             3.0 + point[2] * 2.5)
            for point in points]


class PointingPoseTests(unittest.TestCase):
    def test_index_only_is_pointing(self):
        self.assertTrue(LM.is_pointing_pose(_hand({0})))

    def test_pointing_is_rotation_and_scale_independent(self):
        self.assertTrue(LM.is_pointing_pose(_transform(_hand({0}))))

    def test_open_hand_is_not_pointing(self):
        self.assertFalse(LM.is_pointing_pose(_hand({0, 1, 2, 3})))

    def test_folded_index_is_not_pointing(self):
        self.assertFalse(LM.is_pointing_pose(_hand(set())))

    def test_wrong_landmark_count_is_not_pointing(self):
        self.assertFalse(LM.is_pointing_pose([]))

    def test_pointing_ignores_noisy_pinky(self):
        # Pinky often reads slightly extended while tucked; selection should
        # still recognise a point when middle and ring are folded.
        self.assertTrue(LM.is_pointing_pose(_hand({0, 3})))


class GrabSuppressionTests(unittest.TestCase):
    def test_pointing_blocks_grab(self):
        self.assertFalse(LM.grab_allowed(_hand({0})))
        self.assertEqual(LM.allowed_pinch_tips(_hand({0})), frozenset())

    def test_pointing_with_thumb_on_middle_still_blocks(self):
        # Thumb rests on folded middle tip while pointing — must not grab.
        points = _hand({0})
        points[LM.THUMB_TIP] = points[LM.MIDDLE_TIP]
        self.assertFalse(LM.grab_allowed(points))

    def test_fist_allows_grab(self):
        self.assertTrue(LM.grab_allowed(_hand(set())))
        self.assertEqual(LM.allowed_pinch_tips(_hand(set())),
                         frozenset(LM.PICK_TIPS))

    def test_open_hand_allows_grab(self):
        # Open hand may approach a pick; grab_allowed is only about pose block.
        self.assertTrue(LM.grab_allowed(_hand({0, 1, 2, 3})))

    def test_index_up_over_fist_blocks_even_with_pinky_noise(self):
        self.assertFalse(LM.grab_allowed(_hand({0, 3})))


if __name__ == "__main__":
    unittest.main()
