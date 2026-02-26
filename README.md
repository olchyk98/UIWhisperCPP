# UIWhisperCPP

**A desktop native app for transcribing audio files. Runs locally, no cloud, no API keys.**



https://github.com/user-attachments/assets/7fa7d51e-edfd-4c85-ab9d-4758e72bafbe



---

UIWhisperCPP wraps [whisper.cpp](https://github.com/ggerganov/whisper.cpp) in a clean Qt interface - pick your files, hit go, get transcripts.
Everything runs on your machine. Your audio never leaves your computer.

## What it does

- **Transcribe .wav and .mp3 files** - select one or many, it'll work through them
- **Real-time progress** - watch the transcription happen with a progress bar and live log output
- **Timestamped output** - saves transcripts as `.txt` files with `[HH:MM:SS.mmm --> HH:MM:SS.mmm]` timestamps (SRT-style)
- **Runs completely offline** - powered by Whisper, no internet required after the initial model download

## Getting started

### Requirements

- Python 3.11+
- [FFmpeg](https://ffmpeg.org/) (needed by PyDub for audio conversion)
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

### Install

```bash
git clone https://github.com/olchyk98/UIWhisperCPP.git
cd UIWhisperCPP

# using uv (recommended)
uv sync

# or using pip
pip install -e .
```

### Run

```bash
# if installed with uv
uv run app
```

On first launch, the app will download a Whisper model. This takes a bit depending on your connection - after that, it's cached locally.

## How it works

Pretty straightforward pipeline:

1. You pick audio files through the file dialog
2. Audio gets resampled to 16 kHz (what Whisper model expects)
3. whisper.cpp processes the audio locally using the large-v3 model
4. Segments come back in real-time via callbacks - you see them in the log as they arrive
5. Final transcript gets saved as a `.txt` next to the original file

## Built with

- [whisper.cpp](https://github.com/ggerganov/whisper.cpp) - fast C++ inference for OpenAI's Whisper model
- [pywhispercpp](https://github.com/abdeladim-s/pywhispercpp) - Python bindings for whisper.cpp
- [PySide6](https://doc.qt.io/qtforpython-6/) - Qt for Python
- [PyDub](https://github.com/jiaaro/pydub) - audio manipulation
- [uv](https://docs.astral.sh/uv/) - Python package management

## License

[AGPL-3.0](LICENSE)
