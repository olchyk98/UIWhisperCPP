"""Speaker diarization via the pyannote-3.1 ONNX pipeline.

Diarization is a *separate* pass over the audio, independent of whichever
transcription backend (Parakeet/Whisper) produced the text. It answers "who
spoke when" as a list of `SpeakerSegment`s; `label_segments` then tags each
transcript line with the speaker who dominated its time span (using word timing
when available), keeping the transcript's own structure and just prefixing
`Speaker 0`, `Speaker 1`, ...

The diarizer itself is `PyannoteOnnxDiarizer` (see pyannote_pipeline.py): the
pyannote 3.1 pipeline running on plain onnxruntime — its segmentation model
emits per-frame multi-speaker activity that guides clustering, which separates
distinct voices far better than a flat embedding-clustering pass. Both ONNX
models come from the public, non-gated `onnx-community` repos, so no HuggingFace
account or token is ever needed.

onnxruntime holds the GIL during inference, so the pass runs in a child
*process* (its own GIL) and streams progress back over a queue; the parent's
calling thread blocks on that queue with the GIL released, keeping the GUI
responsive. See `Diarizer`.
"""
import multiprocessing
import os
import queue as _queue
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from uiwhispercpp.models.audio import decode_to_mono_wav
from uiwhispercpp.models.base import Segment

# Public, non-gated ONNX weights (no HuggingFace account/token needed):
# pyannote segmentation-3.0 and the WeSpeaker ResNet34 embedding, both as the
# `onnx-community` exports the pyannote-3.1 pipeline expects.
_SEGMENTATION_URL = (
  "https://huggingface.co/onnx-community/pyannote-segmentation-3.0/"
  "resolve/main/onnx/model.onnx"
)
_EMBEDDING_URL = (
  "https://huggingface.co/onnx-community/wespeaker-voxceleb-resnet34-LM/"
  "resolve/main/onnx/model.onnx"
)
_SEGMENTATION_MODEL = "pyannote-segmentation-3.0.onnx"
_EMBEDDING_MODEL = "wespeaker-voxceleb-resnet34-LM.onnx"

# Auto-detect the speaker count. The UI passes a positive number instead when
# the user knows how many people are in the recording, which is more reliable.
AUTO_SPEAKERS = -1

# Status messages (downloads, progress notes) for the UI log.
OnLog = Callable[[str], None]
# Diarization progress as a percentage in 0..100.
OnProgress = Callable[[int], None]


@dataclass(frozen=True)
class SpeakerSegment:
  """A span of audio attributed to one speaker. Times are in seconds."""
  start: float
  end: float
  speaker: int


@dataclass(frozen=True)
class Turn:
  """One transcript line tagged with a speaker. Times are in seconds."""
  start: float
  end: float
  speaker: int
  text: str


class Diarizer:
  """Runs the pyannote-ONNX diarization pipeline in a separate process.

  onnxruntime holds the GIL during inference, so running the pass in a thread
  would freeze the GUI. Instead each `diarize` call spawns a child process —
  which has its own GIL and so cannot block the GUI's main thread — and streams
  progress back over a queue. The calling thread blocks on that queue (releasing
  the GIL), leaving the UI responsive.

  The object is stateless and cheap to construct; the models are loaded in the
  child each call. That costs a second or two of startup, paid off by the GUI
  never freezing.
  """

  def diarize(
    self,
    audio_path: str,
    *,
    num_speakers: int = AUTO_SPEAKERS,
    on_progress: OnProgress | None = None,
    on_log: OnLog | None = None,
  ) -> list[SpeakerSegment]:
    """Return speaker-labelled spans for `audio_path`, sorted by start time.

    `num_speakers` caps the cluster count when known (the single biggest
    quality lever); clusters that are clearly the same voice are still merged,
    so a lone narrator pinned to 2 speakers comes back as one speaker. Leave it
    at `AUTO_SPEAKERS` to auto-detect. Models are downloaded here (so downloads
    are logged) on first use; diarization runs in a child process.
    """
    log = on_log or (lambda _message: None)
    segmentation_model, embedding_model = _ensure_models(log)

    # spawn, never fork: the parent already has Qt (and possibly Metal/MLX)
    # initialised, and forking that state on macOS crashes.
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    process = context.Process(
      target=_run_diarization_subprocess,
      args=(results, audio_path, segmentation_model, embedding_model, num_speakers),
      daemon=True,
    )
    process.start()
    try:
      while True:
        try:
          # Blocking get with the GIL released -> the GUI thread runs freely.
          kind, payload = results.get(timeout=1.0)
        except _queue.Empty:
          if not process.is_alive():
            raise RuntimeError(
              "Speaker diarization process exited without returning a result."
            )
          continue
        if kind == "progress":
          if on_progress is not None:
            on_progress(payload)
        elif kind == "result":
          return [
            SpeakerSegment(start, end, speaker) for start, end, speaker in payload
          ]
        elif kind == "error":
          raise RuntimeError(f"Speaker diarization failed:\n{payload}")
    finally:
      if process.is_alive():
        process.terminate()
      process.join(timeout=5)


def _run_diarization_subprocess(
  results,
  audio_path: str,
  segmentation_model: str,
  embedding_model: str,
  num_speakers: int,
) -> None:
  """Child-process entry point: run the pipeline and stream progress + result.

  Runs in its own spawned interpreter, so its GIL is independent of the GUI's.
  Everything put on `results` crosses the process boundary, so it must be
  picklable: ("progress", percent), then ("result", [(start, end, speaker), ...])
  on success, or ("error", traceback_text) on failure.
  """
  try:
    from uiwhispercpp.models.pyannote_pipeline import (
      SAMPLE_RATE,
      PyannoteOnnxDiarizer,
    )

    # Decode at native rate and resample with soxr; ffmpeg's own resampler
    # smears the spectrum enough to ruin the speaker embeddings.
    samples = _load_mono_resampled(audio_path, SAMPLE_RATE)
    diarizer = PyannoteOnnxDiarizer(segmentation_model, embedding_model)

    last_percent = -1

    def report(percent: int) -> None:
      nonlocal last_percent
      if percent != last_percent:
        last_percent = percent
        results.put(("progress", percent))

    speakers = None if num_speakers == AUTO_SPEAKERS else num_speakers
    spans = diarizer.diarize(samples, num_speakers=speakers, on_progress=report)
    results.put(("result", [(start, end, spk) for start, end, spk in spans]))
  except Exception:  # report any failure back to the parent
    import traceback
    results.put(("error", traceback.format_exc()))


def label_segments(
  segments: list[Segment], diarization: list[SpeakerSegment]
) -> list[Turn]:
  """Tag the transcript with speakers, splitting only where a speaker changes.

  Each transcript segment keeps its own text and timestamps; the diarization is
  used purely as a "who spoke at time T" index. A segment stays one line when a
  single speaker covers it. When the diarization shows a speaker change *inside*
  a segment — a long turn the transcriber never cut, with the other person
  interjecting — the segment is split at the word boundaries where the speaker
  changes, recovering the back-and-forth. Segments without word timing (Whisper)
  are always emitted whole, labelled by their dominant overlap.

  Lines are ordered by start time and clamped to run forward, so a transcriber
  that overlaps slightly at its chunk seams cannot produce backwards or
  overlapping line times.
  """
  labeled: list[Turn] = []
  previous_end: float | None = None
  for segment in sorted(segments, key=lambda s: (s.start, s.end)):
    for start, end, speaker, text in _label_segment(segment, diarization):
      text = text.strip()
      if not text:
        continue
      start = start if previous_end is None else max(start, previous_end)
      end = max(end, start)
      labeled.append(Turn(start=start, end=end, speaker=speaker, text=text))
      previous_end = end
  return labeled


def _label_segment(segment: Segment, diarization: list[SpeakerSegment]):
  """Split one transcript segment into `(start, end, speaker, text)` parts.

  With word timing, consecutive words sharing a speaker become one part, so a
  segment spanning a speaker change yields several parts; without word timing the
  whole segment is one part, labelled by its dominant time overlap.
  """
  if not segment.words:
    return [(segment.start, segment.end, _assign_speaker(segment, diarization), segment.text)]
  parts: list[list] = []  # [start, end, speaker, [words]]
  for word in segment.words:
    speaker = _assign_speaker(word, diarization)
    if parts and parts[-1][2] == speaker:
      parts[-1][1] = word.end
      parts[-1][3].append(word.text)
    else:
      parts.append([word.start, word.end, speaker, [word.text]])
  return [(p[0], p[1], p[2], " ".join(p[3])) for p in parts]


def _assign_speaker(unit, diarization: list[SpeakerSegment]) -> int:
  """The speaker whose diarized span overlaps `unit` (anything with start/end) most.

  Falls back to the nearest span when the unit lands in a gap between detected
  speech regions, and to speaker 0 only when there is no diarization at all.
  """
  if not diarization:
    return 0
  best_speaker = -1
  best_overlap = 0.0
  for span in diarization:
    overlap = min(unit.end, span.end) - max(unit.start, span.start)
    if overlap > best_overlap:
      best_overlap = overlap
      best_speaker = span.speaker
  if best_speaker != -1:
    return best_speaker

  midpoint = (unit.start + unit.end) / 2
  nearest_speaker = diarization[0].speaker
  nearest_distance = float("inf")
  for span in diarization:
    distance = max(span.start - midpoint, midpoint - span.end, 0.0)
    if distance < nearest_distance:
      nearest_distance = distance
      nearest_speaker = span.speaker
  return nearest_speaker


def _models_dir() -> str:
  directory = os.path.expanduser("~/.cache/uiwhispercpp/diarization")
  os.makedirs(directory, exist_ok=True)
  return directory


def _ensure_models(log: OnLog) -> tuple[str, str]:
  """Download the segmentation + embedding ONNX models on first use; return paths."""
  directory = _models_dir()
  segmentation_path = os.path.join(directory, _SEGMENTATION_MODEL)
  embedding_path = os.path.join(directory, _EMBEDDING_MODEL)

  if not os.path.exists(segmentation_path):
    _download(_SEGMENTATION_URL, segmentation_path, "speaker segmentation model", log)
  if not os.path.exists(embedding_path):
    _download(_EMBEDDING_URL, embedding_path, "speaker embedding model", log)

  return segmentation_path, embedding_path


def _download(url: str, destination: str, label: str, log: OnLog) -> None:
  """Download `url` to `destination` atomically (download once, then rename)."""
  log(f"Downloading {label} (one-time, ~once per machine)...")
  partial = destination + ".part"
  urllib.request.urlretrieve(url, partial)
  os.replace(partial, destination)
  log(f"Downloaded {label}.")


def _load_mono_resampled(audio_path: str, target_rate: int):
  """Load `audio_path` as a 1-D float32 mono array at `target_rate`.

  Decodes at the source's native rate (via ffmpeg, which only downmixes here)
  and resamples with soxr's high-quality converter. This matters: ffmpeg's
  default rate converter smears the spectrum enough to ruin the speaker
  embeddings, so the rate conversion is done with soxr instead.
  """
  try:
    import soundfile
  except ImportError as error:
    raise RuntimeError(
      "soundfile is not installed. Install it with `uv add soundfile`."
    ) from error
  wav_path = decode_to_mono_wav(audio_path)
  try:
    samples, sample_rate = soundfile.read(wav_path, dtype="float32", always_2d=False)
  finally:
    # The decoded WAV can be large (native-rate); drop it once read so the
    # temp dir doesn't accumulate one per file across runs.
    try:
      os.remove(wav_path)
    except OSError:
      pass
  if samples.ndim > 1:
    samples = samples.mean(axis=1)
  if sample_rate != target_rate:
    import soxr
    samples = soxr.resample(samples, sample_rate, target_rate)
  return samples
