"""Transcription models.

`ModelManager` is the single entry point for the rest of the app; `Model` is the
interface new backends implement; `Segment` and `ModelOption` are the shared
data types they exchange.
"""
from uiwhispercpp.models.base import Model, ModelOption, Segment, Word
from uiwhispercpp.models.diarization import (
  Diarizer,
  SpeakerSegment,
  Turn,
  label_segments,
)
from uiwhispercpp.models.manager import ModelManager

__all__ = [
  "Diarizer",
  "Model",
  "ModelManager",
  "ModelOption",
  "Segment",
  "SpeakerSegment",
  "Turn",
  "Word",
  "label_segments",
]
