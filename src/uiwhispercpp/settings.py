"""Persistent user preferences (stored with QSettings in the user's defaults).

Set UIWHISPERCPP_SETTINGS_FILE=/path/to/file.ini to use a throwaway INI file
instead (for tests, so real preferences stay untouched).
"""
import os

from PySide6.QtCore import QSettings

from uiwhispercpp.transcript import OutputOptions
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
    default = OutputOptions()
    return OutputOptions(
      timestamps=str(self._store.value("output/timestamps", default.timestamps)),
      precision=str(self._store.value("output/precision", default.precision)),
      speaker_labels=self._bool("output/speaker_labels", default.speaker_labels),
      file_type=str(self._store.value("output/file_type", default.file_type)),
    )

  def set_output_options(self, options: OutputOptions) -> None:
    self._store.setValue("output/timestamps", options.timestamps)
    self._store.setValue("output/precision", options.precision)
    self._store.setValue("output/speaker_labels", options.speaker_labels)
    self._store.setValue("output/file_type", options.file_type)

  def update_repo(self) -> str:
    return str(self._store.value("updates/repo", DEFAULT_UPDATE_REPO)) or DEFAULT_UPDATE_REPO

  def set_update_repo(self, repo: str) -> None:
    self._store.setValue("updates/repo", repo.strip() or DEFAULT_UPDATE_REPO)

  def value(self, key: str, default: object = None) -> object:
    return self._store.value(key, default)

  def set_value(self, key: str, value: object) -> None:
    self._store.setValue(key, value)

  def _bool(self, key: str, default: bool) -> bool:
    value = self._store.value(key, default)
    if isinstance(value, str):
      return value.lower() in ("1", "true", "yes")
    return bool(value)
