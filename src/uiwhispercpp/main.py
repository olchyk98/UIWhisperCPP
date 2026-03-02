from uiwhispercpp.gui import run_program

# TODO: Split into gui (x)
# TODO: Block non wav/mp3 files (x)
# TODO: Provide display/logging/progress/error handling (x)
# TODO: Provide support for folders (x)
# TODO: Allow to select model size
# TODO: Allow to specify language
# TODO: See how verbose downloading models is (if model does not exist, we should pipe some kind of output to the GUI to notify user that we are currently downloading the model)

def main() -> None:
    run_program()
