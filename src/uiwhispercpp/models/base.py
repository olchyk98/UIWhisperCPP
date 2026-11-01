"""Backend-neutral model abstraction.

`Model` is the interface every transcription backend implements (Whisper,
Parakeet, ...). `ModelManager` (see manager.py) is the single entry point the
rest of the app talks to: it lists the available models and runs transcription,
hiding which backend actually does the work.

To add a backend from another package later, implement `Model` and register it
in `ModelManager`. Nothing else in the app needs to change.
"""
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Word:
  """One word with its own timing, in seconds. `text` is the bare word."""
  start: float
  end: float
  text: str


@dataclass(frozen=True)
class Segment:
  """One transcribed span of audio. Times are in seconds.

  `words` carries per-word timing when the backend can produce it (Parakeet
  does; whisper.cpp through pywhispercpp does not). Speaker diarization uses it
  to attribute a speaker to each word, so a single sentence that straddles a
  speaker change can be split correctly. When it is empty, diarization falls
  back to attributing the whole segment to one speaker.
  """
  start: float
  end: float
  text: str
  words: tuple[Word, ...] = ()


@dataclass(frozen=True)
class ModelOption:
  """A single user-selectable model, as shown in the model dropdown.

  `key` is the stable identifier the manager uses to route a request back to the
  backend that owns it; `label` is the human-readable description.
  `unavailable_reason` is set when this Mac cannot run the model at all (e.g.
  MLX on an Intel Mac); the UI shows it greyed out with that reason.
  """
  key: str
  label: str
  unavailable_reason: str | None = None
  # Compact name shown in the closed model menu ("Parakeet v3").
  short_label: str = ""


# Called once per segment, as transcription produces it.
OnSegment = Callable[[Segment], None]
# Called with a completion percentage in the range 0..100.
OnProgress = Callable[[int], None]
# Called with short human-readable status updates ("Installing Parakeet...").
OnStatus = Callable[[str], None]


class Model(ABC):
  """A transcription backend.

  A backend may expose several selectable variants (e.g. Whisper's sizes)
  through `options`, and knows how to transcribe audio with any of them.
  """

  @property
  @abstractmethod
  def options(self) -> list[ModelOption]:
    """The models this backend offers to the user."""

  @abstractmethod
  def transcribe(
    self,
    audio_path: str,
    *,
    model_key: str,
    language: str,
    on_segment: OnSegment,
    on_progress: OnProgress,
    on_status: OnStatus | None = None,
  ) -> list[Segment]:
    """Transcribe `audio_path` using the variant identified by `model_key`.

    `on_segment` is called as segments become available, and the full list is
    also returned. `language` is an ISO code (or "" for auto-detect); a backend
    that detects the language itself may ignore it.
    """

  @abstractmethod
  def unload(self) -> None:
    """Release any loaded weights so another model can take the memory."""
