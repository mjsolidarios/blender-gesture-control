# Repository Guidelines

## Project Structure & Module Organization

This Blender extension is a flat Python package. `__init__.py` owns registration; `operators.py`, `ui.py`, `prefs.py`, and `settings.py` define Blender-facing behavior. Tracking and gesture logic lives in `tracker.py`, `gestures.py`, `filters.py`, and `landmarks.py`. `session.py` coordinates state, `overlay.py` renders viewport feedback, and `deps.py` manages external packages and the model. Packaging rules are in `blender_manifest.toml`; user documentation belongs in `README.md`.

## Build, Test, and Development Commands

- `python3 -m compileall -q .` checks every Python file for syntax errors without requiring Blender.
- `blender --command extension build --source-dir .` builds an installable archive. Run it from the root with Blender 4.2+ on `PATH`.
- Install the resulting ZIP through **Edit > Preferences > Add-ons > Install from Disk**. Use **Install Dependencies**, **Download Model**, and **Re-check** in the add-on preferences before manual testing.

There is no development server or lockfile. Runtime dependencies are installed into Blender's user directories through `deps.py`, not this repository.

## Coding Style & Naming Conventions

Use four-space indentation and PEP 8 conventions. Keep Blender identifiers under the `hgc` namespace; operator classes follow patterns such as `HGC_OT_stop`. Use `snake_case` for functions and variables and `UPPER_SNAKE_CASE` for constants. Preserve SPDX headers and concise module docstrings. Keep heavy MediaPipe/OpenCV imports out of package import paths so preferences remain usable before installation.

## Testing Guidelines

No automated suite or coverage threshold exists. Run the syntax check, enable/disable the add-on in supported Blender, and verify camera startup, gesture transforms, undo, overlay cleanup, and dependency reporting. Pure math or filtering changes should add isolated tests under `tests/`, named like `test_filters.py`.

## Commit & Pull Request Guidelines

The repository has no commit history, so no convention is established. Use short, imperative subjects such as `Fix overlay cleanup on unregister`. Keep commits focused. Pull requests should describe user-visible changes, list the Blender version and platform tested, include verification steps, and attach screenshots or recordings for UI, overlay, or gesture changes. Link issues and call out dependency or manifest changes.

## Security & Configuration

Do not commit downloaded models, private dependency folders, webcam captures, or credentials. Network, filesystem, and camera access must remain declared in `blender_manifest.toml` and explained in user-facing documentation.
