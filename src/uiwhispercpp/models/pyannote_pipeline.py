"""Pure-ONNX speaker diarization, the pyannote 3.1 pipeline.

A dependency-trimmed reimplementation of the pyannote-audio 3.1 diarization
pipeline running entirely on onnxruntime (no PyTorch). It pairs the pyannote
powerset *segmentation* model with a WeSpeaker *embedding* model and does
pyannote-style two-stage clustering. This is what fixes sherpa's clustering
collapsing distinct voices onto one speaker: the segmentation model emits
per-frame multi-speaker activity, and clustering groups embeddings of those
local speakers into global ones.

Both ONNX models come from the public, non-gated `onnx-community` repos, so no
HuggingFace account/token is ever needed.

Algorithm and constants adapted from `samson6460/pyannote-onnx-extended` (MIT)
and `pengzhendong/pyannote-onnx`; this implementation drops their `av` /
`huggingface_hub` / `pytorch` dependencies in favour of the audio decoding and
clustering we already ship (soxr + scipy).
"""
from itertools import permutations

import numpy as np

SAMPLE_RATE = 16000
_DURATION = 10.0  # segmentation model window, seconds
_STEP = 5.0       # 50% overlap between windows
_WINDOW = int(_DURATION * SAMPLE_RATE)
_STEP_SAMPLES = int(_STEP * SAMPLE_RATE)
# Hysteresis + smoothing thresholds (pyannote 3.1 defaults).
_ONSET = 0.5
_OFFSET = 0.5
_MIN_DURATION_ON = 0.5
_MIN_DURATION_OFF = 0.3


def _sample2frame(sample: int) -> int:
  """Map a sample index to the segmentation model's output frame index."""
  return (sample - 721) // 270


class PyannoteOnnxDiarizer:
  """Runs the pyannote-3.1 ONNX diarization pipeline on 16 kHz mono audio."""

  def __init__(self, segmentation_path: str, embedding_path: str) -> None:
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.inter_op_num_threads = 1
    options.intra_op_num_threads = 1
    providers = ["CPUExecutionProvider"]
    self._segmentation = ort.InferenceSession(
      segmentation_path, sess_options=options, providers=providers
    )
    self._embedding = ort.InferenceSession(
      embedding_path, sess_options=options, providers=providers
    )
    # Query input names rather than hardcoding (they differ between exports).
    self._seg_input = self._segmentation.get_inputs()[0].name
    self._emb_input = self._embedding.get_inputs()[0].name

  def diarize(self, waveform, *, num_speakers=None, on_progress=None):
    """Return `[(start, end, speaker), ...]` sorted by start time.

    `waveform` is a 1-D float32 array at 16 kHz. `num_speakers` pins the cluster
    count when known; leave it None to auto-detect.
    """
    segments = self._run_segmentation(waveform, on_progress)
    embeddings, valid = self._extract_embeddings(waveform, segments)
    if len(embeddings) == 0:
      return []
    labels = self._cluster(embeddings, valid, num_speakers)
    spans = [
      (segment[0], segment[1], int(label)) for segment, label in zip(valid, labels)
    ]
    return _merge_same_speaker(spans)

  # -- 1. segmentation -------------------------------------------------------

  @staticmethod
  def _reorder(overlap_prob, prob):
    """Align a window's local speakers to the previous window's, by permutation."""
    perms = np.array([np.array(perm).T for perm in permutations(prob.T)])
    sums = np.sum(perms[:, : overlap_prob.shape[0], :] - overlap_prob, axis=1)
    diffs = np.sum(np.abs(sums), axis=1)
    return perms[np.argmin(diffs)]

  def _run_segmentation(self, waveform, on_progress):
    waveform = np.ascontiguousarray(waveform, dtype=np.float32)
    pad = _WINDOW - (len(waveform) % _WINDOW)
    if 0 < pad < _WINDOW:
      waveform = np.pad(waveform, (0, pad))

    num_frames = _sample2frame(_WINDOW)
    seconds_per_frame = _DURATION / num_frames
    total_duration = len(waveform) / SAMPLE_RATE
    total_frames = int(total_duration / seconds_per_frame) + 100
    scores = np.zeros((total_frames, 3), dtype=np.float32)
    overlap_frames = _sample2frame(_WINDOW - _STEP_SAMPLES)

    starts = list(range(0, len(waveform) - _WINDOW + 1, _STEP_SAMPLES))
    for index, i_sample in enumerate(starts):
      chunk = waveform[i_sample : i_sample + _WINDOW][np.newaxis, np.newaxis, :]
      out = self._segmentation.run(None, {self._seg_input: chunk})[0][0]
      out = np.exp(out)  # log-probs -> probs over 7 powerset classes
      # Marginal per-speaker activity from the 3-speaker powerset.
      out[:, 1] += out[:, 4] + out[:, 5]
      out[:, 2] += out[:, 4] + out[:, 6]
      out[:, 3] += out[:, 5] + out[:, 6]
      speech_prob = out[:, 1:4]

      start_frame = int((i_sample / SAMPLE_RATE) / seconds_per_frame)
      end_frame = start_frame + len(speech_prob)
      if i_sample > 0:
        prior = scores[start_frame:end_frame][:overlap_frames]
        speech_prob = self._reorder(prior, speech_prob)
        mid = start_frame + overlap_frames
        scores[start_frame:mid] = (prior + speech_prob[:overlap_frames]) / 2
        scores[mid:end_frame] = speech_prob[overlap_frames:]
      else:
        scores[start_frame:end_frame] = speech_prob

      if on_progress is not None and starts:
        on_progress(int((index + 1) / len(starts) * 100))

    return self._scores_to_segments(scores, seconds_per_frame, total_duration)

  @staticmethod
  def _scores_to_segments(scores, seconds_per_frame, total_duration):
    active = [False, False, False]
    starts = [0.0, 0.0, 0.0]
    per_speaker = [[], [], []]
    for frame, row in enumerate(scores):
      t = frame * seconds_per_frame
      if t > total_duration:
        break
      for s in range(3):
        if not active[s]:
          if row[s] > _ONSET:
            active[s] = True
            starts[s] = t
        elif row[s] < _OFFSET:
          active[s] = False
          if t - starts[s] >= _MIN_DURATION_ON:
            per_speaker[s].append((starts[s], t))
    for s in range(3):
      if active[s] and total_duration - starts[s] >= _MIN_DURATION_ON:
        per_speaker[s].append((starts[s], total_duration))

    segments = []
    for spans in per_speaker:
      segments.extend(_merge_close(spans, _MIN_DURATION_OFF))
    return segments

  # -- 2. embeddings ---------------------------------------------------------

  def _extract_embeddings(self, waveform, segments):
    import librosa

    embeddings, valid = [], []
    for start, end in segments:
      chunk = waveform[int(start * SAMPLE_RATE) : int(end * SAMPLE_RATE)]
      if len(chunk) < 400:
        continue
      mel = librosa.feature.melspectrogram(
        y=np.ascontiguousarray(chunk, dtype=np.float32), sr=SAMPLE_RATE,
        n_fft=400, hop_length=160, n_mels=80, window="hamming", center=False,
      )
      features = np.log(mel + 1e-6).T
      features = features - np.mean(features, axis=0)
      features = features[np.newaxis, :, :].astype(np.float32)
      emb = self._embedding.run(None, {self._emb_input: features})[0][0]
      norm = np.linalg.norm(emb)
      if norm > 1e-6:
        emb = emb / norm
      embeddings.append(emb)
      valid.append((start, end))
    return np.array(embeddings), valid

  # -- 3. clustering ---------------------------------------------------------

  @staticmethod
  def _cluster(embeddings, segments, num_speakers):
    if len(embeddings) == 1:
      return [0]
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import cdist

    total = sum(end - start for start, end in segments)
    min_long = float(np.clip(total / 60, 2, 5))
    long_idx = [i for i, (s, e) in enumerate(segments) if (e - s) >= min_long]
    short_idx = [i for i, (s, e) in enumerate(segments) if (e - s) < min_long]

    if len(long_idx) < 2 or (num_speakers is not None and num_speakers <= 1):
      return [0] * len(embeddings)

    long_emb = embeddings[long_idx]
    if num_speakers is not None:
      tree = linkage(long_emb, method="ward", metric="euclidean")
      long_labels = fcluster(tree, t=num_speakers, criterion="maxclust") - 1
    else:
      threshold = max((350 - total) / 350, 0.73)
      tree = linkage(long_emb, method="single", metric="euclidean")
      long_labels = fcluster(tree, t=threshold, criterion="distance") - 1

    labels = np.zeros(len(embeddings), dtype=int)
    labels[long_idx] = long_labels
    if short_idx:
      uniq = np.unique(long_labels)
      centroids = np.array([long_emb[long_labels == u].mean(axis=0) for u in uniq])
      nearest = np.argmin(cdist(embeddings[short_idx], centroids), axis=1)
      labels[short_idx] = [uniq[n] for n in nearest]
    return labels


def _merge_close(spans, collar):
  """Sort spans and merge any whose gap is <= `collar`."""
  spans = sorted(spans)
  merged = []
  for start, end in spans:
    if merged and start - merged[-1][1] <= collar:
      merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    else:
      merged.append((start, end))
  return merged


def _merge_same_speaker(spans):
  """Sort by start; merge consecutive same-speaker spans that touch/overlap."""
  spans = sorted(spans, key=lambda s: (s[0], s[1]))
  merged = []
  for start, end, speaker in spans:
    if merged and merged[-1][2] == speaker and start <= merged[-1][1]:
      prev = merged[-1]
      merged[-1] = (prev[0], max(prev[1], end), speaker)
    else:
      merged.append((start, end, speaker))
  return merged
