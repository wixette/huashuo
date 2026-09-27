"""Audio helpers: WAV files, silence trimming, loudness and true-peak gain (POST-1/2/4)."""

from __future__ import annotations

import os
import wave
from pathlib import Path

import numpy as np

TARGET_LUFS = -18.0
MAX_TRUE_PEAK_DB = -1.5
MAX_GAIN_DB = 12.0           # never boost a unit more than this, whatever it measures
TRIM_BELOW_PEAK_DB = 40.0    # a 20 ms frame this far below the peak counts as silence
TRIM_MARGIN = 0.03           # keep a little air so word onsets and tails are not clipped
FADE_IN, FADE_OUT = 0.005, 0.015   # seconds; the model sometimes stops mid-sound, which clicks


def write_wav(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    """16-bit mono WAV, written to a temp file and renamed so it is never half-written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    pcm = (np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(tmp), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())
    os.replace(tmp, path)


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as handle:
        if handle.getnchannels() != 1 or handle.getsampwidth() != 2:
            raise ValueError(f"{path}: expected 16-bit mono WAV")
        frames = handle.readframes(handle.getnframes())
        return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32767.0, handle.getframerate()


def to_pcm16(audio: np.ndarray) -> bytes:
    return (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


def trim_bounds(audio: np.ndarray, sample_rate: int) -> tuple[int, int]:
    """Start and end sample of the audible part, with a small margin (POST-4)."""
    hop = max(1, int(sample_rate * 0.02))
    frames = len(audio) // hop
    if frames == 0:
        return 0, len(audio)
    rms = np.sqrt((audio[: frames * hop].reshape(frames, hop) ** 2).mean(axis=1) + 1e-12)
    db = 20 * np.log10(rms)
    loud = np.flatnonzero(db > db.max() - TRIM_BELOW_PEAK_DB)
    if len(loud) == 0:
        return 0, len(audio)
    margin = int(TRIM_MARGIN * sample_rate)
    return max(0, loud[0] * hop - margin), min(len(audio), (loud[-1] + 1) * hop + margin)


def fade(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    """Short linear fades at both ends, so a unit that starts or stops abruptly does not click."""
    audio = audio.copy()
    for n, sl in ((min(len(audio), int(FADE_IN * sample_rate)), slice(None)),
                  (min(len(audio), int(FADE_OUT * sample_rate)), slice(None, None, -1))):
        if n:
            audio[sl][:n] *= np.linspace(0.0, 1.0, n, endpoint=False, dtype=audio.dtype)
    return audio


def loudness(audio: np.ndarray, sample_rate: int) -> float | None:
    """Integrated loudness in LUFS (ITU-R BS.1770), or None if too short to measure."""
    import pyloudnorm

    if len(audio) < int(0.4 * sample_rate):   # below one gating block
        return None
    value = pyloudnorm.Meter(sample_rate).integrated_loudness(audio.astype(np.float64))
    return float(value) if np.isfinite(value) else None


def true_peak_db(audio: np.ndarray) -> float:
    """Peak after 4x oversampling, which catches inter-sample peaks a plain max misses."""
    from scipy.signal import resample_poly

    if len(audio) == 0:
        return -120.0
    peak = float(np.max(np.abs(resample_poly(audio, 4, 1))))
    return 20 * np.log10(max(peak, 1e-9))


def gain_db(measured_lufs: float | None, fallback_lufs: float | None,
            target: float = TARGET_LUFS) -> float:
    """Gain that brings a unit to the target loudness; peaks are the limiter's job.

    Units too short to measure use the book's median loudness instead.
    """
    measured = measured_lufs if measured_lufs is not None else fallback_lufs
    return float(np.clip(target - (measured if measured is not None else target),
                         -MAX_GAIN_DB, MAX_GAIN_DB))


# The limiter's ceiling sits below the true-peak limit: AAC decoding overshoots peaks by
# up to ~1 dB, and the ceiling is applied to samples, not to the oversampled signal.
LIMITER_CEILING_DB = MAX_TRUE_PEAK_DB - 1.5
_LOOKAHEAD = 0.005   # seconds: gain starts falling this long before a peak
_RELEASE = 0.050     # seconds: and recovers over this long after it


def limit(audio: np.ndarray, sample_rate: int, ceiling_db: float = LIMITER_CEILING_DB) -> np.ndarray:
    """Look-ahead peak limiter (POST-2). Only the rare spikes above the ceiling are
    touched, so speech brought to the target loudness keeps its dynamics."""
    from scipy.ndimage import maximum_filter1d, minimum_filter1d, uniform_filter1d

    ceiling = 10 ** (ceiling_db / 20)
    peaks = maximum_filter1d(np.abs(audio), size=max(1, int(_LOOKAHEAD * sample_rate) * 2 + 1))
    if peaks.max(initial=0.0) <= ceiling:
        return audio
    wanted = np.minimum(1.0, ceiling / np.maximum(peaks, 1e-9))
    # Minimum then moving average of the same width: smooth, and never above `wanted`
    # at the peak itself.
    width = max(1, int(_RELEASE * sample_rate))
    gain = uniform_filter1d(minimum_filter1d(wanted, size=width), size=width)
    return (audio * np.minimum(gain, wanted)).astype(np.float32)
