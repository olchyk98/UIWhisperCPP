"""Safe, observable model downloads.

Every model file the app fetches goes through `download`: it streams to a
`.part` file with a network timeout, checks the HTTP status and the final size,
reports progress, can be stopped, and only renames the file into place once it
is complete. A half-finished or HTML-error download therefore never ends up
under the real name (where it would later be loaded and crash the engine).
"""
from collections.abc import Callable
import os
import ssl
import threading
import urllib.error
import urllib.request

from uiwhispercpp import app_log
from uiwhispercpp.models.base import JobStopped, StopToken, UserFacingError

_log = app_log.get("downloads")
_CHUNK = 64 * 1024
_TIMEOUT = 30

# (bytes done, bytes total or 0 when unknown)
OnBytes = Callable[[int, int], None]


class DownloadError(UserFacingError):
  """A download failure with a short, user-facing message."""


def _ssl_context() -> ssl.SSLContext:
  try:
    import certifi
    return ssl.create_default_context(cafile=certifi.where())
  except ImportError:
    return ssl.create_default_context()


def format_size(num_bytes: int) -> str:
  if num_bytes >= 1_000_000_000:
    return f"{num_bytes / 1_000_000_000:.1f} GB"
  return f"{num_bytes / 1_000_000:.0f} MB"


def download(
  url: str,
  destination: str,
  *,
  what: str,
  on_bytes: OnBytes,
  stop: StopToken | None = None,
  min_size: int = 1_000_000,
) -> None:
  """Download `url` to `destination` atomically. `what` names it in errors.

  The transfer runs on a helper thread so Stop takes effect immediately, even
  while a server hasn't answered yet (where a socket read can block for the
  whole timeout). A stopped helper finishes or times out on its own, cleans up
  its partial file and never renames it into place.
  """
  if stop is None:
    _download(url, destination, what=what, on_bytes=on_bytes, stop=None, min_size=min_size)
    return
  outcome: dict[str, BaseException | None] = {}

  def run() -> None:
    try:
      _download(url, destination, what=what, on_bytes=on_bytes, stop=stop, min_size=min_size)
      outcome["error"] = None
    except BaseException as error:  # noqa: BLE001 - handed to the caller
      outcome["error"] = error

  helper = threading.Thread(target=run, name="download", daemon=True)
  helper.start()
  while helper.is_alive():
    helper.join(0.2)
    if stop.stopped and helper.is_alive():
      raise JobStopped()
  if outcome.get("error") is not None:
    raise outcome["error"]


def _download(
  url: str,
  destination: str,
  *,
  what: str,
  on_bytes: OnBytes,
  stop: StopToken | None,
  min_size: int,
) -> None:
  os.makedirs(os.path.dirname(destination), exist_ok=True)
  partial = destination + ".part"
  _log.info("Downloading %s: %s", what, url)
  request = urllib.request.Request(url, headers={"User-Agent": "UIWhisperCPP"})
  on_bytes(0, 0)
  finished = threading.Event()
  try:
    with urllib.request.urlopen(request, timeout=_TIMEOUT, context=_ssl_context()) as response:
      if stop is not None:
        _close_on_stop(response, stop, finished)
      total = int(response.headers.get("Content-Length") or 0)
      done = 0
      with open(partial, "wb") as out:
        while chunk := response.read(_CHUNK):
          if stop is not None:
            stop.check()
          out.write(chunk)
          done += len(chunk)
          on_bytes(done, total)
    if stop is not None:
      stop.check()
  except urllib.error.HTTPError as error:
    _cleanup(partial)
    raise DownloadError(
      f"Couldn't download the {what} (the server answered {error.code}). Try again later."
    ) from error
  except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError) as error:
    _cleanup(partial)
    if stop is not None:
      stop.check()  # a read cut short by Stop is a stop, not a network error
    raise DownloadError(
      f"Couldn't download the {what}. It needs an internet connection the first "
      "time only - check your connection and try again."
    ) from error
  except OSError as error:
    _cleanup(partial)
    if stop is not None:
      stop.check()
    raise DownloadError(
      f"Couldn't save the {what} to disk ({error.strerror or error}). "
      "Check that there is enough free space."
    ) from error
  except BaseException:
    _cleanup(partial)
    raise
  finally:
    finished.set()
  if (total and done != total) or done < min_size:
    _cleanup(partial)
    raise DownloadError(
      f"The {what} download was incomplete. Check your connection and try again."
    )
  os.replace(partial, destination)
  _log.info("Downloaded %s (%s) to %s", what, format_size(done), destination)


def _close_on_stop(response: object, stop: StopToken, finished: threading.Event) -> None:
  """Close the connection as soon as Stop is pressed, so a stalled read ends now."""
  def watch() -> None:
    while not finished.wait(0.3):
      if stop.stopped:
        try:
          response.close()
        except Exception:  # noqa: BLE001
          pass
        return

  threading.Thread(target=watch, name="download-stop", daemon=True).start()


def _cleanup(path: str) -> None:
  try:
    os.remove(path)
  except OSError:
    pass
