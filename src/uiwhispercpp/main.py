from uiwhispercpp.gui.gui import run_program

# TODO: Split into gui (x)
# TODO: Block non wav/mp3 files (x)
# TODO: Provide display/logging/progress/error handling (x)
# TODO: Provide support for folders (x)
# TODO: Allow to select model size (x)
# TODO: Allow to specify language (x)
# TODO: py2mac/py2exe
# TODO: Prompt user before closing the app (when transcribing)
# TODO: Prompt before starting transcription (cancel file selection to be the easiest)
# TODO: Bug: Weird scrolling to the bottom when scrolled up
# TODO: Bug: When selecting folder, pressing cancel starts transcribing

def main() -> None:
    run_program()
