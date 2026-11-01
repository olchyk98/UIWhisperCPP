"""Single source of truth for the app version and its macOS floor.

The PyInstaller spec reads both values (bundle version and
LSMinimumSystemVersion), the updater compares `__version__` against GitHub
release tags, and the startup check warns on Macs older than `MIN_MACOS`.
"""

__version__ = "1.5"

# The bundled Qt bindings (PySide6/shiboken6) and MLX are built for macOS 15,
# so the packaged app cannot run on anything older. Raise this when a bundled
# dependency raises its own floor (check with `vtool -show-build <dylib>`).
MIN_MACOS = (15, 0)

# GitHub repository the updater pulls releases from, unless changed in Settings.
DEFAULT_UPDATE_REPO = "olchyk98/UIWhisperCPP"
