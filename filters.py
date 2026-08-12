# SPDX-License-Identifier: GPL-3.0-or-later
"""
Signal smoothing for noisy landmark streams.

Raw MediaPipe landmarks jitter by a pixel or two even for a perfectly still
hand, which reads as an unusable tremble once it drives an object transform.
A plain low-pass filter fixes the tremble but adds constant lag, which feels
like dragging an object through syrup.

The One Euro filter (Casiez, Roussel & Vogel, CHI 2012) solves both: it varies
its own cutoff frequency with the observed speed of the signal, so slow motion
is filtered hard (no jitter) and fast motion is barely filtered at all (no lag).
"""

import math


class LowPass:
    """First-order exponential low-pass with externally supplied alpha."""

    __slots__ = ("y", "initialised")

    def __init__(self):
        self.y = 0.0
        self.initialised = False

    def reset(self):
        self.initialised = False
        self.y = 0.0

    def filter(self, value: float, alpha: float) -> float:
        if not self.initialised:
            self.y = value
            self.initialised = True
        else:
            self.y = alpha * value + (1.0 - alpha) * self.y
        return self.y


class OneEuro:
    """
    One Euro filter for a single scalar channel.

    :param min_cutoff: cutoff frequency at zero speed, in Hz. Lower means more
        smoothing when the hand is still. 1.0 is a good starting point.
    :param beta: speed coefficient. Higher means the filter opens up faster as
        the hand accelerates, trading jitter rejection for responsiveness.
    :param d_cutoff: cutoff for the internal derivative estimate, in Hz.
    """

    __slots__ = ("min_cutoff", "beta", "d_cutoff", "_x", "_dx", "_t_prev")

    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.02,
                 d_cutoff: float = 1.0):
        self.min_cutoff = float(min_cutoff)
        self.beta = float(beta)
        self.d_cutoff = float(d_cutoff)
        self._x = LowPass()
        self._dx = LowPass()
        self._t_prev = None

    def reset(self):
        self._x.reset()
        self._dx.reset()
        self._t_prev = None

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2.0 * math.pi * max(cutoff, 1e-6))
        return 1.0 / (1.0 + tau / max(dt, 1e-6))

    def filter(self, value: float, timestamp: float) -> float:
        if self._t_prev is None:
            self._t_prev = timestamp
            self._x.filter(value, 1.0)
            return value

        dt = timestamp - self._t_prev
        # Guard against a stalled or rewound clock.
        if dt <= 0.0 or dt > 1.0:
            dt = 1.0 / 60.0
        self._t_prev = timestamp

        prev = self._x.y
        # Rate of change, itself smoothed so noise does not inflate the cutoff.
        dx = (value - prev) / dt
        edx = self._dx.filter(dx, self._alpha(self.d_cutoff, dt))

        cutoff = self.min_cutoff + self.beta * abs(edx)
        return self._x.filter(value, self._alpha(cutoff, dt))


class OneEuroVec:
    """One Euro filter applied independently to each channel of a vector."""

    __slots__ = ("_channels",)

    def __init__(self, size: int, min_cutoff: float = 1.0,
                 beta: float = 0.02, d_cutoff: float = 1.0):
        self._channels = [OneEuro(min_cutoff, beta, d_cutoff)
                          for _ in range(size)]

    def configure(self, min_cutoff: float, beta: float):
        for c in self._channels:
            c.min_cutoff = float(min_cutoff)
            c.beta = float(beta)

    def reset(self):
        for c in self._channels:
            c.reset()

    def filter(self, values, timestamp: float):
        return [c.filter(v, timestamp)
                for c, v in zip(self._channels, values)]


class LandmarkSmoother:
    """
    Smooths a full 21x3 landmark set with one filter bank per hand slot.

    Filters are keyed by ``(slot, channel)`` rather than by handedness so a
    brief left/right misclassification does not swap two filter histories and
    produce a visible snap.
    """

    def __init__(self, num_hands: int = 2, num_points: int = 21):
        self._banks = [OneEuroVec(num_points * 3) for _ in range(num_hands)]
        self._num_points = num_points

    def configure(self, min_cutoff: float, beta: float):
        for b in self._banks:
            b.configure(min_cutoff, beta)

    def reset(self, slot: int = None):
        if slot is None:
            for b in self._banks:
                b.reset()
        elif 0 <= slot < len(self._banks):
            self._banks[slot].reset()

    def filter(self, slot: int, points, timestamp: float):
        """
        :param points: flat sequence of ``num_points * 3`` floats.
        :returns: the smoothed flat sequence.
        """
        if not (0 <= slot < len(self._banks)):
            return list(points)
        return self._banks[slot].filter(points, timestamp)
