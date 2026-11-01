"""Persistent user preferences (stored with QSettings in the user's defaults).

Set UIWHISPERCPP_SETTINGS_FILE=/path/to/file.ini to use a throwaway INI file
instead (for tests, so real preferences stay untouched).
"""
import os

from PySide6.QtCore import QSettings

from uiwhispercpp.transcript import (
  FILE_SRT,
  FILE_TXT,
  PRECISION_MS,
  PRECISION_SECONDS,
  TIMESTAMPS_OFF,
  TIMESTAMPS_RANGE,
  TIMESTAMPS_START,
  OutputOptions,
)
from uiwhispercpp.version import DEFAULT_UPDATE_REPO

_ORG = "Camphouse"
_APP = "UIWhisperCPP"


class Settings:
  def __init__(self) -> None:
    override = os.environ.get("UIWHISPERCPP_SETTINGS_FILE")
    if override:
      self._store = QSettings(override, QSettings.Format.IniFormat)
    else:
      self._store = QSettings(_ORG, _APP)

  def output_options(self) -> OutputOptions:
    """The saved output options; any unknown value falls back to its default."""
    default = OutputOptions()
    return OutputOptions(
      timestamps=self.choice(
        "output/timestamps", default.timestamps,
        (TIMESTAMPS_RANGE, TIMESTAMPS_START, TIMESTAMPS_OFF),
      ),
      precision=self.choice(
        "output/precision", default.precision, (PRECISION_MS, PRECISION_SECONDS)
      ),
      speaker_labels=self._bool("output/speaker_labels", default.speaker_labels),
      file_type=self.choice("output/file_type", default.file_type, (FILE_TXT, FILE_SRT)),
    )

  def choice(self, key: str, default: str, allowed: tuple[str, ...]) -> str:
    value = self._store.value(key, default)
    value = value[0] if isinstance(value, list) and value else value
    return value if isinstance(value, str) and value in allowed else default

  def integer(self, key: str, default: int, low: int, high: int) -> int:
    """An int setting clamped to [low, high]; junk values give `default`."""
    value = self._store.value(key, default)
    value = value[0] if isinstance(value, list) and value else value
    try:
      number = int(float(value))
    except (TypeError, ValueError, OverflowError):
      return default
    return max(low, min(high, number))

  def text(self, key: str, default: str = "") -> str:
    value = self._store.value(key, default)
    return value if isinstance(value, str) else default

  def flag(self, key: str, default: bool) -> bool:
    return self._bool(key, default)

  def reset_ui(self) -> None:
    """Forget window-level choices (used if they ever stop the app starting)."""
    for key in self._store.allKeys():
      if key.startswith("ui/"):
        self._store.remove(key)

  def set_output_options(self, options: OutputOptions) -> None:
    self._store.setValue("output/timestamps", options.timestamps)
    self._store.setValue("output/precision", options.precision)
    self._store.setValue("output/speaker_labels", options.speaker_labels)
    self._store.setValue("output/file_type", options.file_type)

  def update_repo(self) -> str:
    return self.text("updates/repo", DEFAULT_UPDATE_REPO).strip() or DEFAULT_UPDATE_REPO

  def set_update_repo(self, repo: str) -> None:
    self._store.setValue("updates/repo", repo.strip() or DEFAULT_UPDATE_REPO)

  def value(self, key: str, default: object = None) -> object:
    return self._store.value(key, default)

  def set_value(self, key: str, value: object) -> None:
    self._store.setValue(key, value)

  def _bool(self, key: str, default: bool) -> bool:
    value = self._store.value(key, default)
    value = value[0] if isinstance(value, list) and value else value
    if isinstance(value, str):
      return value.lower() in ("1", "true", "yes")
    return bool(value)
