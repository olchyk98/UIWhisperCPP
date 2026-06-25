# UIWhisperCPP

**A desktop native app for transcribing audio files. Runs locally, no cloud, no API keys.**



https://github.com/user-attachments/assets/7fa7d51e-edfd-4c85-ab9d-4758e72bafbe



---

UIWhisperCPP wraps local speech-to-text engines - [whisper.cpp](https://github.com/ggerganov/whisper.cpp) and [Parakeet](https://github.com/senstella/parakeet-mlx) - in a clean Qt interface - pick your files, hit go, get transcripts.
Everything runs on your machine. Your audio never leaves your computer.

## What it does

- **Transcribe .wav, .mp3 and .m4a files** - select one or many, it'll work through them
- **Choose your model** - Whisper sizes (base to large-v3) or Parakeet v3, a fast multilingual model for Apple Silicon
- **Separate speakers (optional)** - tick "Separate speakers" and each line is tagged `[Speaker 0]`, `[Speaker 1]`, … so a conversation reads as a back-and-forth. If you know how many people are talking, set the count for the cleanest result (defaults to 2; leave it on "Auto" if unsure). Runs as a local [pyannote](https://github.com/pyannote/pyannote-audio) ONNX pipeline - no HuggingFace account or API key
- **Real-time progress** - watch the transcription happen with a progress bar and live log output
- **Timestamped output** - saves transcripts as `.txt` files with `[HH:MM:SS.mmm --> HH:MM:SS.mmm]` timestamps (SRT-style), prefixed with `[Speaker N]` when speaker separation is on
- **Runs completely offline** - powered by Whisper or Parakeet, no internet required after the initial model download

## Getting started

### Requirements

- Python 3.11+
- An Apple Silicon Mac (only required for the Parakeet model; Whisper runs anywhere)
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

> FFmpeg is bundled via [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg), so no separate install is needed.

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

On first launch, the app will download a Whisper model. This takes a bit depending on your connection - after that, it's cached locally. Turning on speaker separation likewise downloads two small diarization models the first time you use it (from public sources - no HuggingFace login).

## How it works

Pretty straightforward pipeline:

1. You pick audio files through the file dialog
2. Audio gets resampled to 16 kHz (what the models expect)
3. The selected backend processes the audio locally - whisper.cpp for Whisper, MLX for Parakeet
4. Segments come back via callbacks - you see them in the log as they arrive
5. Final transcript gets saved as a `.txt` next to the original file

When **Separate speakers** is enabled, a second pass runs after transcription: a local pyannote speaker-diarization pipeline (pure [ONNX Runtime](https://onnxruntime.ai/), no PyTorch) works out who spoke when, and each transcript line is tagged with the matching speaker. It uses the transcriber's word-level timestamps, so a long turn that mixes two voices gets split at the right word rather than dumped under one label. This pass runs in a separate process to keep the UI responsive while it works.

A small abstraction layer (`uiwhispercpp.models`) sits behind all of this: each engine implements a common `Model` interface, and a single `ModelManager` lists the available models and runs transcription. Adding a model from another package later is just a new `Model` implementation registered with the manager - nothing else in the app changes.

## Built with

- [whisper.cpp](https://github.com/ggerganov/whisper.cpp) - fast C++ inference for OpenAI's Whisper model
- [pywhispercpp](https://github.com/abdeladim-s/pywhispercpp) - Python bindings for whisper.cpp
- [parakeet-mlx](https://github.com/senstella/parakeet-mlx) - NVIDIA Parakeet models on Apple Silicon via MLX
- [pyannote-audio](https://github.com/pyannote/pyannote-audio) models + [ONNX Runtime](https://onnxruntime.ai/) - offline speaker diarization, no PyTorch
- [soxr](https://github.com/dofuuz/python-soxr) - high-quality audio resampling
- [PySide6](https://doc.qt.io/qtforpython-6/) - Qt for Python
- [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg) - bundled FFmpeg for audio decoding
- [uv](https://docs.astral.sh/uv/) - Python package management

## License

[AGPL-3.0](LICENSE)
