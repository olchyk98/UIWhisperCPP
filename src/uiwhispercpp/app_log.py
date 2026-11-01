"""The app's debug log: everything the UI abstracts away, in one place.

`setup()` (called once at startup) does three things:
- configures Python logging to write to ~/Library/Logs/UIWhisperCPP/app.log
  (rotated, so it never grows without bound);
- captures the raw process output: file descriptors 1 and 2 are redirected
  through a pipe, so prints, Python warnings, native library chatter
  (whisper.cpp, MLX, onnxruntime, ffmpeg errors) and the speaker-separation
  child process all land in the log, while still reaching the terminal;
- keeps the most recent lines in memory for the in-app Debug Log window,
  which subscribes with `add_listener`.

Use `log = app_log.get(__name__)` and the standard logging methods anywhere.
"""
from collections import deque
from collections.abc import Callable
import logging
import logging.handlers
import os
import platform
import sys
import threading

LOG_DIR = os.path.expanduser("~/Library/Logs/UIWhisperCPP")
LOG_FILE = os.path.join(LOG_DIR, "app.log")
_MAX_LINES = 5000

_lines: deque[str] = deque(maxlen=_MAX_LINES)
_listeners: list[Callable[[str], None]] = []
_lock = threading.Lock()
_configured = False
_raw = logging.getLogger("raw")


def get(name: str) -> logging.Logger:
  return logging.getLogger(name)


def recent_lines() -> list[str]:
  with _lock:
    return list(_lines)


def add_listener(listener: Callable[[str], None]) -> None:
  """`listener` is called (from any thread) with every new formatted line."""
  with _lock:
    _listeners.append(listener)


def remove_listener(listener: Callable[[str], None]) -> None:
  with _lock:
    if listener in _listeners:
      _listeners.remove(listener)


class _MemoryHandler(logging.Handler):
  def emit(self, record: logging.LogRecord) -> None:
    try:
      line = self.format(record)
    except Exception:  # noqa: BLE001 - logging must never raise
      return
    with _lock:
      _lines.append(line)
      listeners = list(_listeners)
    for listener in listeners:
      try:
        listener(line)
      except Exception:  # noqa: BLE001
        pass


def _capture_fd(fd: int, original: int) -> None:
  """Route `fd` through a pipe: tee to the original target and into the log."""
  read_end, write_end = os.pipe()
  os.dup2(write_end, fd)
  os.close(write_end)

  def pump() -> None:
    buffer = b""
    while True:
      try:
        chunk = os.read(read_end, 4096)
      except OSError:
        return
      if not chunk:
        return
      try:
        os.write(original, chunk)
      except OSError:
        pass  # no terminal attached (Finder launch): the log still gets it
      buffer += chunk
      *complete, buffer = buffer.split(b"\n")
      for raw in complete:
        text = raw.decode("utf-8", errors="replace").rstrip("\r")
        if text.strip():
          _raw.info(text)

  threading.Thread(target=pump, name=f"log-fd{fd}", daemon=True).start()


def setup() -> None:
  """Configure logging and capture raw output. Safe to call more than once."""
  global _configured
  if _configured:
    return
  _configured = True

  formatter = logging.Formatter(
    "%(asctime)s.%(msecs)03d %(levelname)-5s %(name)s: %(message)s", "%H:%M:%S"
  )
  root = logging.getLogger()
  # Third-party libraries at INFO (httpx still logs each model download
  # request); the app's own loggers at DEBUG for progress milestones.
  root.setLevel(logging.INFO)
  for name in ("jobs", "app", "parakeet", "updater", "raw", "crash"):
    logging.getLogger(name).setLevel(logging.DEBUG)
  for noisy in ("httpcore", "urllib3", "filelock", "fsspec", "numba"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
  memory = _MemoryHandler()
  memory.setFormatter(formatter)
  root.addHandler(memory)
  try:
    os.makedirs(LOG_DIR, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
      LOG_FILE, maxBytes=2_000_000, backupCount=2, encoding="utf-8"
    )
    file_handler.setFormatter(
      logging.Formatter("%(asctime)s %(levelname)-5s %(name)s: %(message)s")
    )
    root.addHandler(file_handler)
  except OSError:
    pass  # an unwritable log folder must not stop the app
  # Raw lines are already printed to the terminal by the fd tee; keep them
  # out of any console handler to avoid echo loops.
  _raw.propagate = True
  logging.captureWarnings(True)

  # Capture native output too. Python's own sys.stdout/err keep writing to
  # fds 1/2, which now feed the pipe, so prints are captured as well.
  for fd in (1, 2):
    try:
      original = os.dup(fd)
      _capture_fd(fd, original)
    except OSError:
      pass
  try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
  except (AttributeError, ValueError):
    pass

  def log_unhandled(kind, value, traceback) -> None:
    logging.getLogger("crash").critical("Unhandled exception", exc_info=(kind, value, traceback))

  sys.excepthook = log_unhandled
  threading.excepthook = lambda args: logging.getLogger("crash").critical(
    "Unhandled exception in thread %s", args.thread.name if args.thread else "?",
    exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
  )

  from uiwhispercpp.version import __version__
  logging.getLogger("app").info(
    "UIWhisperCPP %s starting - macOS %s, %s, Python %s, %s",
    __version__,
    platform.mac_ver()[0] or "?",
    platform.machine(),
    platform.python_version(),
    "packaged app" if getattr(sys, "frozen", False) else "from source",
  )
