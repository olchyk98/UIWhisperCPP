import sys

from uiwhispercpp.system_check import exit_if_macos_outdated


def _self_test() -> None:
  """`--self-test`: verify the bundled engines import, print a report, exit.

  Lets a packaged build be checked from the terminal without opening the UI:
    UIWhisperCPP.app/Contents/MacOS/UIWhisperCPP --self-test
  """
  from uiwhispercpp.models.parakeet import parakeet_unavailable_reason
  from uiwhispercpp.version import __version__

  print(f"UIWhisperCPP {__version__}")
  failures = 0
  for module in ("PySide6.QtWidgets", "pywhispercpp.model", "onnxruntime",
                 "mlx.core", "parakeet_mlx"):
    try:
      __import__(module)
      print(f"ok    {module}")
    except Exception as error:  # noqa: BLE001
      failures += 1
      print(f"FAIL  {module}: {error}")
  print(f"parakeet: {parakeet_unavailable_reason() or 'available'}")
  sys.exit(1 if failures else 0)


def main() -> None:
  # Speaker diarization runs in a spawned child process. In a frozen
  # (PyInstaller) build, the child re-launches this same executable, so
  # freeze_support() must run first to intercept it - otherwise the child
  # would start a second GUI instead of doing its work.
  import multiprocessing
  multiprocessing.freeze_support()

  # Before importing Qt: on a too-old macOS the bundled Qt can't even load,
  # so the user gets a clear native alert instead of a silent crash.
  exit_if_macos_outdated()

  if "--self-test" in sys.argv:
    _self_test()

  from uiwhispercpp.gui.gui import run_program
  run_program()


if __name__ == "__main__":
  main()
