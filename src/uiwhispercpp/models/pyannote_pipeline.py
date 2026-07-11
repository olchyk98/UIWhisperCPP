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
and `pengzhendong/pyannote-onnx`, with several fidelity fixes toward the real
pyannote 3.1 pipeline:

* Embedding features are Kaldi-style fbank (HTK mel scale, unnormalized
  triangular filters, 512-point FFT, pre-emphasis, per-frame DC removal) —
  exactly what WeSpeaker was trained on — instead of librosa's Slaney-scale
  mel spectrogram (validated against kaldi-native-fbank to float32 precision).
* Frames where another local speaker is active are excluded from embedding
  extraction (the reference pipeline's `embedding_exclude_overlap: true`).
* Long spans contribute one embedding per ~5 s sub-window instead of a single
  embedding per span, giving clustering more evidence per speaker.
* A pinned speaker count is treated as an upper bound: clusters whose
  centroids are so close they must be the same voice are merged rather than
  reported as separate speakers.
"""
from itertools import permutations

import numpy as np

SAMPLE_RATE = 16000
_DURATION = 10.0  # segmentation model window, seconds
_STEP = 5.0       # 50% overlap between windows
_WINDOW = int(_DURATION * SAMPLE_RATE)
_STEP_SAMPLES = int(_STEP * SAMPLE_RATE)
# Hysteresis + smoothing thresholds. Onset/offset are the pyannote 3.1
# defaults. The reference pipeline applies no minimum-on filtering at all
# (min_duration_off: 0.0); we keep a small minimum so lone noise blips don't
# become speakers, but low enough that a quick interjection ("yes", "okay")
# still gets its own span.
_ONSET = 0.5
_OFFSET = 0.5
_MIN_DURATION_ON = 0.25
_MIN_DURATION_OFF = 0.3

# Embedding extraction. Spans longer than _MAX_UNSPLIT contribute one
# embedding per ~_SUBWINDOW seconds (the reference pipeline works at <=10 s
# chunk granularity); more embeddings per speaker make clustering robust.
_SUBWINDOW = 5.0
_MAX_UNSPLIT = 10.0
# Overlap exclusion keeps only frames where no *other* local speaker is
# active, provided at least this many 10 ms feature frames survive.
_MIN_CLEAN_FRAMES = 25
# Two forced clusters whose centroids are closer than this cosine distance are
# the same voice split in half, not two speakers: same-voice forced splits
# measure ~0.03 here, while genuinely distinct speakers exceed ~0.5 (the
# reference pipeline merges below 0.70). Halfway margins on both sides.
_SAME_SPEAKER_DISTANCE = 0.25

# -- Kaldi fbank ---------------------------------------------------------------
# WeSpeaker models were trained on Kaldi fbank features:
# torchaudio.compliance.kaldi.fbank(waveform * 32768, num_mel_bins=80,
#   frame_length=25, frame_shift=10, round_to_power_of_two=True,
#   snip_edges=True, dither=0, sample_frequency=16000, window_type="hamming")
# i.e. per-frame DC removal, pre-emphasis 0.97, 400-sample Hamming window
# zero-padded to a 512-point FFT, HTK-scale mel triangles between 20 Hz and
# Nyquist with unit height, log with the float32-epsilon floor. CMN (mean
# subtraction over time) happens in `_embed`.

_FRAME_LEN = 400      # 25 ms
_FRAME_SHIFT = 160    # 10 ms
_PADDED_LEN = 512     # 400 rounded up to a power of two
_NUM_BINS = 80
_LOW_FREQ = 20.0
_HIGH_FREQ = 8000.0
_PREEMPHASIS = 0.97
_LOG_FLOOR = 1.1920928955078125e-07  # numeric_limits<float>::epsilon()

_mel_banks_cache = None


def _mel_banks():
  """Kaldi-style mel filterbank matrix, shape (80, 257)."""
  global _mel_banks_cache
  if _mel_banks_cache is not None:
    return _mel_banks_cache
  mel = lambda f: 1127.0 * np.log(1.0 + np.asarray(f, dtype=np.float64) / 700.0)
  fft_bin_width = SAMPLE_RATE / _PADDED_LEN
  mel_low, mel_high = mel(_LOW_FREQ), mel(_HIGH_FREQ)
  delta = (mel_high - mel_low) / (_NUM_BINS + 1)
  left = mel_low + np.arange(_NUM_BINS, dtype=np.float64)[:, None] * delta
  center = left + delta
  right = center + delta
  freqs = mel(fft_bin_width * np.arange(_PADDED_LEN // 2))[None, :]
  up = (freqs - left) / (center - left)
  down = (right - freqs) / (right - center)
  banks = np.maximum(0.0, np.minimum(up, down))
  # Kaldi computes triangles for the first 256 FFT bins and leaves the
  # Nyquist bin at zero.
  _mel_banks_cache = np.pad(banks, ((0, 0), (0, 1))).astype(np.float32)
  return _mel_banks_cache


def _kaldi_fbank(chunk):
  """Log-mel fbank of a float32 waveform in [-1, 1] at 16 kHz -> (frames, 80)."""
  x = np.asarray(chunk, dtype=np.float32) * 32768.0
  if len(x) < _FRAME_LEN:
    return np.empty((0, _NUM_BINS), dtype=np.float32)
  m = 1 + (len(x) - _FRAME_LEN) // _FRAME_SHIFT
  idx = np.arange(_FRAME_LEN)[None, :] + _FRAME_SHIFT * np.arange(m)[:, None]
  frames = x[idx].astype(np.float32)
  frames -= frames.mean(axis=1, keepdims=True)              # per-frame DC removal
  frames -= _PREEMPHASIS * np.concatenate(                  # pre-emphasis
    [frames[:, :1], frames[:, :-1]], axis=1
  )
  frames *= np.hamming(_FRAME_LEN).astype(np.float32)
  spectrum = np.abs(np.fft.rfft(frames, n=_PADDED_LEN)) ** 2
  mel = spectrum.astype(np.float32) @ _mel_banks().T
  return np.log(np.maximum(mel, _LOG_FLOOR)).astype(np.float32)


def _sample2frame(sample: int) -> int:
  """Map a sample index to the segmentation model's output frame index."""
  return (sample - 721) // 270


class PyannoteOnnxDiarizer:
  """Runs the pyannote-3.1 ONNX diarization pipeline on 16 kHz mono audio."""

  def __init__(self, segmentation_path: str, embedding_path: str) -> None:
    import os

    import onnxruntime as ort

    options = ort.SessionOptions()
    options.inter_op_num_threads = 1
    # This runs in its own child process, so a few threads speed up long files
    # without ever touching GUI responsiveness.
    options.intra_op_num_threads = max(1, min(4, (os.cpu_count() or 2) - 2))
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

    `waveform` is a 1-D float32 array at 16 kHz. `num_speakers` caps the
    cluster count when known (clusters that are clearly the same voice are
    still merged); leave it None to auto-detect.
    """
    spans, scores, seconds_per_frame = self._run_segmentation(waveform, on_progress)
    embeddings, durations, owners, kept = self._extract_embeddings(
      waveform, spans, scores, seconds_per_frame
    )
    if len(embeddings) == 0:
      return []
    point_labels = self._cluster(embeddings, durations, num_speakers)
    result = [
      (start, end, int(_majority_label(point_labels, durations, owners, index)))
      for index, (start, end, _slot) in enumerate(kept)
    ]
    return _merge_same_speaker(result)

  # -- 1. segmentation -------------------------------------------------------

  @staticmethod
  def _reorder(overlap_prob, prob):
    """Align a window's local speakers to the previous window's, by permutation.

    The cost is the frame-wise L1 distance over the overlapping region; summing
    signed differences first (as the reference ports do) lets opposite-signed
    errors cancel and can pick a mismatched permutation.
    """
    perms = np.array([np.array(perm).T for perm in permutations(prob.T)])
    diffs = np.sum(
      np.abs(perms[:, : overlap_prob.shape[0], :] - overlap_prob), axis=(1, 2)
    )
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

    spans = self._scores_to_spans(scores, seconds_per_frame, total_duration)
    return spans, scores, seconds_per_frame

  @staticmethod
  def _scores_to_spans(scores, seconds_per_frame, total_duration):
    """Binarize per-slot activity into `(start, end, slot)` spans.

    The slot identity is kept so embedding extraction can exclude frames where
    a *different* local speaker is talking over this one.
    """
    active = [False, False, False]
    starts = [0.0, 0.0, 0.0]
    per_slot = [[], [], []]
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
            per_slot[s].append((starts[s], t))
    for s in range(3):
      if active[s] and total_duration - starts[s] >= _MIN_DURATION_ON:
        per_slot[s].append((starts[s], total_duration))

    spans = []
    for s in range(3):
      for start, end in _merge_close(per_slot[s], _MIN_DURATION_OFF):
        spans.append((start, end, s))
    return sorted(spans)

  # -- 2. embeddings ---------------------------------------------------------

  def _extract_embeddings(self, waveform, spans, scores, seconds_per_frame):
    """One embedding per span sub-window.

    Returns `(embeddings, durations, owners, kept_spans)` where `owners[i]` is
    the index into `kept_spans` that embedding `i` belongs to.
    """
    embeddings, durations, owners, kept = [], [], [], []
    for start, end, slot in spans:
      pieces = _subwindows(start, end)
      span_index = len(kept)
      found = False
      for piece_start, piece_end in pieces:
        chunk = waveform[
          int(piece_start * SAMPLE_RATE) : int(piece_end * SAMPLE_RATE)
        ]
        if len(chunk) < _FRAME_LEN:
          continue
        features = _kaldi_fbank(chunk)
        features = self._exclude_overlap(
          features, piece_start, slot, scores, seconds_per_frame
        )
        embeddings.append(self._embed(features))
        durations.append(piece_end - piece_start)
        owners.append(span_index)
        found = True
      if found:
        kept.append((start, end, slot))
    return np.array(embeddings), np.array(durations), np.array(owners), kept

  @staticmethod
  def _exclude_overlap(features, start, slot, scores, seconds_per_frame):
    """Drop feature frames where another local speaker is also active.

    Mirrors the reference pipeline's `embedding_exclude_overlap: true`: the
    embedding should describe this speaker, not a mixture. Falls back to the
    full window when too little clean speech would remain.
    """
    centers = start + (np.arange(len(features)) * _FRAME_SHIFT + _FRAME_LEN / 2) / SAMPLE_RATE
    indices = np.minimum(
      (centers / seconds_per_frame).astype(int), len(scores) - 1
    )
    others = [s for s in range(3) if s != slot]
    clean = scores[indices][:, others].max(axis=1) <= 0.5
    if clean.sum() >= _MIN_CLEAN_FRAMES:
      return features[clean]
    return features

  def _embed(self, features):
    features = features - features.mean(axis=0)  # CMN, as in WeSpeaker
    emb = self._embedding.run(
      None, {self._emb_input: features[np.newaxis, :, :].astype(np.float32)}
    )[0][0]
    norm = np.linalg.norm(emb)
    if norm > 1e-6:
      emb = emb / norm
    return emb

  # -- 3. clustering ---------------------------------------------------------

  @staticmethod
  def _cluster(embeddings, durations, num_speakers):
    if len(embeddings) == 1:
      return [0]
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import cdist

    total = float(durations.sum())
    min_long = float(np.clip(total / 60, 2, 5))
    long_idx = np.where(durations >= min_long)[0]
    short_idx = np.where(durations < min_long)[0]

    if num_speakers is not None and num_speakers <= 1:
      return np.zeros(len(embeddings), dtype=int)
    if len(long_idx) < 2:
      if num_speakers is None:
        return np.zeros(len(embeddings), dtype=int)
      # A pinned count with too few long segments to anchor clustering:
      # cluster everything rather than silently reporting a single speaker.
      long_idx = np.arange(len(embeddings))
      short_idx = np.array([], dtype=int)

    long_emb = embeddings[long_idx]
    if num_speakers is not None:
      tree = linkage(long_emb, method="ward", metric="euclidean")
      long_labels = fcluster(tree, t=num_speakers, criterion="maxclust") - 1
      long_labels = _merge_same_voice(long_emb, long_labels)
    else:
      threshold = max((350 - total) / 350, 0.73)
      tree = linkage(long_emb, method="single", metric="euclidean")
      long_labels = fcluster(tree, t=threshold, criterion="distance") - 1

    labels = np.zeros(len(embeddings), dtype=int)
    labels[long_idx] = long_labels
    if len(short_idx):
      uniq = np.unique(long_labels)
      centroids = np.array([long_emb[long_labels == u].mean(axis=0) for u in uniq])
      nearest = np.argmin(cdist(embeddings[short_idx], centroids), axis=1)
      labels[short_idx] = [uniq[n] for n in nearest]
    return _compact_labels(labels)


def _compact_labels(labels):
  """Renumber labels to 0, 1, ... in order of first appearance.

  Same-voice merging can leave gaps (e.g. {0, 2}), which would surface as
  "Speaker 0" / "Speaker 2" in the transcript.
  """
  mapping = {}
  return np.array([mapping.setdefault(label, len(mapping)) for label in labels])


def _subwindows(start, end):
  """Split `[start, end)` into ~5 s pieces; spans up to 10 s stay whole."""
  duration = end - start
  if duration <= _MAX_UNSPLIT:
    return [(start, end)]
  count = max(1, int(duration // _SUBWINDOW))
  edges = np.linspace(start, end, count + 1)
  return list(zip(edges[:-1], edges[1:]))


def _merge_same_voice(embeddings, labels):
  """Merge forced clusters whose centroids are clearly the same voice.

  A pinned speaker count makes `fcluster` split *something* even when the
  audio has fewer distinct voices (a lone narrator pinned to 2 speakers gets
  an arbitrary half/half split). Same-voice splits sit at centroid cosine
  distance ~0.03; distinct speakers at 0.5+. Merging below
  `_SAME_SPEAKER_DISTANCE` turns the pinned count into an upper bound.
  """
  from scipy.spatial.distance import cosine

  labels = np.asarray(labels).copy()
  while True:
    uniq = np.unique(labels)
    if len(uniq) < 2:
      return labels
    centroids = {u: embeddings[labels == u].mean(axis=0) for u in uniq}
    best = None
    for i, a in enumerate(uniq):
      for b in uniq[i + 1 :]:
        distance = cosine(centroids[a], centroids[b])
        if distance < _SAME_SPEAKER_DISTANCE and (best is None or distance < best[0]):
          best = (distance, a, b)
    if best is None:
      return labels
    labels[labels == best[2]] = best[1]


def _majority_label(point_labels, durations, owners, span_index):
  """A span's speaker: the duration-weighted majority of its sub-windows."""
  votes = {}
  for label, duration, owner in zip(point_labels, durations, owners):
    if owner == span_index:
      votes[label] = votes.get(label, 0.0) + duration
  return max(votes, key=votes.get)


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
