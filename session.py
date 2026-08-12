# SPDX-License-Identifier: GPL-3.0-or-later
"""
Module-level handle on the running capture session.

The tracker thread, the gesture engine and the draw handler all outlive any
single operator invocation, and the panel needs to query their state without
holding a reference to the operator. Keeping them here - rather than on the
operator instance or in a scene property - means there is exactly one place
that knows whether tracking is live, and exactly one place to tear down from
``unregister()``.
"""

from __future__ import annotations

_tracker = None
_engine = None
_dirty = False
_last_error = ""
_camera_probe_message = ""


def set_active(tracker, engine):
    global _tracker, _engine, _dirty
    _tracker = tracker
    _engine = engine
    _dirty = False


def clear():
    global _tracker, _engine, _dirty
    _tracker = None
    _engine = None
    _dirty = False


def tracker():
    return _tracker


def engine():
    return _engine


def is_running() -> bool:
    return _tracker is not None and _tracker.running


def mark_dirty():
    """A setting changed that only applies when the camera is reopened."""
    global _dirty
    _dirty = True


def is_dirty() -> bool:
    return _dirty


def set_error(text: str):
    global _last_error
    _last_error = text or ""


def last_error() -> str:
    return _last_error


def set_camera_probe_message(text: str):
    global _camera_probe_message
    _camera_probe_message = text or ""


def camera_probe_message() -> str:
    return _camera_probe_message


def shutdown():
    """Stop everything. Safe to call when nothing is running."""
    global _tracker, _engine
    if _engine is not None:
        try:
            _engine.reset()
        except Exception:
            pass
    if _tracker is not None:
        try:
            _tracker.stop()
        except Exception:
            pass
    _tracker = None
    _engine = None
