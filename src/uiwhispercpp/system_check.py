"""Startup checks that must run before any Qt import.

The packaged app bundles libraries that need a minimum macOS version. On an
older Mac they fail to load with a cryptic import error (or the app silently
closes), which reads like a bug. This module detects that case using only the
standard library, so it still works when Qt itself cannot load, and tells the
user plainly what to do via a native dialog.
"""
import platform
import subprocess
import sys

from uiwhispercpp.version import MIN_MACOS


def macos_version() -> tuple[int, ...] | None:
  """The running macOS version as a tuple, or None when not on macOS."""
  if sys.platform != "darwin":
    return None
  raw = platform.mac_ver()[0]
  if not raw or raw.startswith("10.16"):
    # 10.16 is the compatibility version macOS reports to some programs
    # (SYSTEM_VERSION_COMPAT); ask the kernel for the real one.
    try:
      raw = subprocess.run(
        ["sysctl", "-n", "kern.osproductversion"], capture_output=True, text=True, timeout=5
      ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
      raw = ""
  try:
    version = tuple(int(part) for part in raw.split(".") if part)
  except ValueError:
    return None
  return version or None


def is_apple_silicon() -> bool:
  return sys.platform == "darwin" and platform.machine() == "arm64"


def _format(version: tuple[int, ...]) -> str:
  return ".".join(str(part) for part in version)


def outdated_macos_message() -> str | None:
  """A user-facing message when this Mac is too old for the app, else None."""
  current = macos_version()
  if current is None or current >= MIN_MACOS:
    return None
  return (
    f"UIWhisperCPP needs macOS {_format(MIN_MACOS)} or newer.\n\n"
    f"This Mac is running macOS {_format(current)}. The app's built-in "
    "components require a newer system, so it can't start - this is not a "
    "bug in the app.\n\n"
    "Update macOS in System Settings > General > Software Update, then open "
    "UIWhisperCPP again."
  )


def show_native_alert(title: str, message: str) -> None:
  """Show a blocking macOS alert without Qt; offers to open Software Update."""
  def quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'

  script = (
    f"set answer to display alert {quote(title)} message {quote(message)} "
    'as critical buttons {"Quit", "Open Software Update"} '
    'default button "Open Software Update"\n'
    'if button returned of answer is "Open Software Update" then\n'
    '  do shell script "open x-apple.systempreferences:'
    'com.apple.preferences.softwareupdate"\n'
    "end if"
  )
  try:
    subprocess.run(["osascript", "-e", script], check=False)
  except OSError:
    print(f"{title}\n\n{message}", file=sys.stderr)


def exit_if_macos_outdated() -> None:
  """Warn and exit when the Mac is older than the app supports."""
  message = outdated_macos_message()
  if message is None:
    return
  print(message, file=sys.stderr)
  show_native_alert("macOS update required", message)
  sys.exit(1)
