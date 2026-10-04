from __future__ import annotations

import math
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .repetition import event_risk_codes


@dataclass(frozen=True)
class RhythmPolicy:
    sample_rate: int = 16_000
    frame_samples: int = 400
    hop_samples: int = 160
    max_lag_seconds: float = 0.75
    min_correlation: float = 0.75
    max_duration_drift: float = 0.03
    max_boundary_adjustment: float = 0.75


def decode_audio_pcm(
    path: Path,
    *,
    ffmpeg: Path | None = None,
    sample_rate: int = 16_000,
) -> tuple[np.ndarray, int]:
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"音频不存在：{resolved}")
    if resolved.suffix.casefold() == ".wav":
        with wave.open(str(resolved), "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
            frames = handle.readframes(handle.getnframes())
        if width != 2:
            raise ValueError("WAV测试输入必须是16-bit PCM")
        pcm = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
        if channels > 1:
            pcm = pcm.reshape(-1, channels).mean(axis=1)
        if rate != sample_rate:
            positions = np.linspace(0.0, len(pcm) - 1, round(len(pcm) * sample_rate / rate))
            pcm = np.interp(positions, np.arange(len(pcm)), pcm).astype(np.float32)
        return pcm, sample_rate
    if ffmpeg is None:
        raise ValueError("解码FLAC需要显式提供项目ffmpeg路径")
    command = [
        str(ffmpeg.resolve()),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(resolved),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "f32le",
        "pipe:1",
    ]
    completed = subprocess.run(command, check=True, capture_output=True)
    pcm = np.frombuffer(completed.stdout, dtype="<f4").copy()
    if pcm.size == 0:
        raise ValueError(f"音频解码结果为空：{resolved}")
    return pcm, sample_rate


def onset_energy_envelope(
    pcm: np.ndarray,
    *,
    frame_samples: int = 400,
    hop_samples: int = 160,
) -> np.ndarray:
    signal = np.asarray(pcm, dtype=np.float64)
    if signal.size < frame_samples:
        return np.zeros(0, dtype=np.float64)
    squared = signal * signal
    cumulative = np.concatenate(([0.0], np.cumsum(squared)))
    energy = (
        cumulative[frame_samples:] - cumulative[:-frame_samples]
    )[::hop_samples] / frame_samples
    rms = np.sqrt(np.maximum(energy, 0.0))
    onset = np.maximum(0.0, np.diff(rms, prepend=rms[0]))
    if onset.size >= 3:
        onset = np.convolve(onset, np.ones(3) / 3.0, mode="same")
    peak = float(np.max(onset)) if onset.size else 0.0
    return onset / peak if peak > 0 else onset


def normalized_cross_correlation(
    reference: np.ndarray,
    target: np.ndarray,
    *,
    max_lag_frames: int,
) -> tuple[float, int]:
    left = np.asarray(reference, dtype=np.float64)
    right = np.asarray(target, dtype=np.float64)
    if left.size < 4 or right.size < 4:
        return 0.0, 0
    if right.size != left.size:
        positions = np.linspace(0.0, right.size - 1, left.size)
        right = np.interp(positions, np.arange(right.size), right)
    best_correlation = -1.0
    best_lag = 0
    maximum = min(max_lag_frames, max(0, left.size - 4))
    for lag in range(-maximum, maximum + 1):
        if lag < 0:
            current_left = left[-lag:]
            current_right = right[: right.size + lag]
        elif lag > 0:
            current_left = left[: left.size - lag]
            current_right = right[lag:]
        else:
            current_left = left
            current_right = right
        if current_left.size < 4:
            continue
        current_left = current_left - current_left.mean()
        current_right = current_right - current_right.mean()
        denominator = float(
            np.linalg.norm(current_left) * np.linalg.norm(current_right)
        )
        correlation = (
            float(np.dot(current_left, current_right) / denominator)
            if denominator > 1e-12
            else 0.0
        )
        if correlation > best_correlation:
            best_correlation = correlation
            best_lag = lag
    return max(0.0, min(1.0, best_correlation)), best_lag


def onset_rhythm_signature(
    envelope: np.ndarray,
    *,
    maximum_lag_frames: int = 400,
) -> np.ndarray:
    signal = np.asarray(envelope, dtype=np.float64)
    if signal.size < 8:
        return np.zeros(0, dtype=np.float64)
    centered = signal - signal.mean()
    transform_size = 1 << (2 * signal.size - 1).bit_length()
    spectrum = np.fft.rfft(centered, n=transform_size)
    autocorrelation = np.fft.irfft(
        spectrum * np.conjugate(spectrum),
        n=transform_size,
    )[: min(signal.size, maximum_lag_frames)]
    scale = abs(float(autocorrelation[0]))
    if scale <= 1e-12:
        return np.zeros(0, dtype=np.float64)
    normalized = autocorrelation / scale
    window = min(51, normalized.size if normalized.size % 2 else normalized.size - 1)
    if window >= 3:
        trend = np.convolve(normalized, np.ones(window) / window, mode="same")
        normalized = normalized - trend
    return normalized[5:] if normalized.size > 10 else normalized


def onset_pattern_correlation(
    reference: np.ndarray,
    target: np.ndarray,
) -> float:
    left = onset_rhythm_signature(reference)
    right = onset_rhythm_signature(target)
    correlation, _ = normalized_cross_correlation(
        left,
        right,
        max_lag_frames=min(20, max(0, min(left.size, right.size) - 4)),
    )
    return correlation


def audio_stability(pcm: np.ndarray) -> dict[str, float]:
    signal = np.asarray(pcm, dtype=np.float64)
    if signal.size == 0:
        return {
            "score": 0.0,
            "rms": 0.0,
            "silent_ratio": 1.0,
            "clipping_ratio": 0.0,
        }
    rms = float(np.sqrt(np.mean(signal * signal)))
    silent_ratio = float(np.mean(np.abs(signal) < 0.002))
    clipping_ratio = float(np.mean(np.abs(signal) >= 0.999))
    amplitude_score = min(1.0, rms / 0.08)
    score = amplitude_score * (1.0 - silent_ratio) * (1.0 - clipping_ratio)
    return {
        "score": round(max(0.0, min(1.0, score)), 6),
        "rms": round(rms, 8),
        "silent_ratio": round(silent_ratio, 6),
        "clipping_ratio": round(clipping_ratio, 6),
    }


def _slice_pcm(
    pcm: np.ndarray,
    sample_rate: int,
    start: float,
    end: float,
) -> np.ndarray:
    first = max(0, int(round(start * sample_rate)))
    last = min(pcm.size, int(round(end * sample_rate)))
    return pcm[first:last]


def _asr_coverage(events: list[dict[str, Any]]) -> float:
    if not events:
        return 0.0
    covered = 0
    for event in events:
        evidence = " ".join(str(value).casefold() for value in event.get("evidence", []))
        if any(needle in evidence for needle in ("asr", "word", "vocal")):
            covered += 1
    return covered / len(events)


def build_rhythm_evidence(
    events: list[dict[str, Any]],
    group: dict[str, Any],
    pcm: np.ndarray,
    sample_rate: int,
    section_duration: float,
    policy: RhythmPolicy | None = None,
    audio_time_offset: float = 0.0,
) -> dict[str, Any]:
    policy = policy or RhythmPolicy(sample_rate=sample_rate)
    occurrence_audio: list[np.ndarray] = []
    occurrence_envelopes: list[np.ndarray] = []
    qualities: list[dict[str, Any]] = []
    for occurrence in group["occurrences"]:
        audio = _slice_pcm(
            pcm,
            sample_rate,
            float(occurrence["start"]) + audio_time_offset,
            float(occurrence["end"]) + audio_time_offset,
        )
        envelope = onset_energy_envelope(
            audio,
            frame_samples=policy.frame_samples,
            hop_samples=policy.hop_samples,
        )
        occurrence_audio.append(audio)
        occurrence_envelopes.append(envelope)
        start_index = int(occurrence["start_event_index"])
        end_index = int(occurrence["end_event_index"])
        selected_events = events[start_index:end_index]
        risks = sorted(
            {
                code
                for event in selected_events
                for code in event_risk_codes(event)
            }
        )
        boundary_distance = min(
            float(occurrence["start"]),
            max(0.0, section_duration - float(occurrence["end"])),
        )
        qualities.append(
            {
                "asr_coverage": round(_asr_coverage(selected_events), 6),
                "audio_stability": audio_stability(audio),
                "risk_codes": risks,
                "boundary_distance_score": round(
                    min(1.0, boundary_distance / max(1.0, section_duration / 2.0)),
                    6,
                ),
            }
        )

    max_lag_frames = max(
        0,
        round(policy.max_lag_seconds * sample_rate / policy.hop_samples),
    )
    pairwise = [[1.0 for _ in occurrence_envelopes] for _ in occurrence_envelopes]
    direct_pairwise = [
        [1.0 for _ in occurrence_envelopes] for _ in occurrence_envelopes
    ]
    for left_index, left in enumerate(occurrence_envelopes):
        for right_index in range(left_index + 1, len(occurrence_envelopes)):
            direct_correlation, _ = normalized_cross_correlation(
                left,
                occurrence_envelopes[right_index],
                max_lag_frames=max_lag_frames,
            )
            pattern_correlation = onset_pattern_correlation(
                left,
                occurrence_envelopes[right_index],
            )
            pairwise[left_index][right_index] = pattern_correlation
            pairwise[right_index][left_index] = pattern_correlation
            direct_pairwise[left_index][right_index] = direct_correlation
            direct_pairwise[right_index][left_index] = direct_correlation

    for index, quality in enumerate(qualities):
        peers = [
            value
            for peer_index, value in enumerate(pairwise[index])
            if peer_index != index
        ]
        direct_peers = [
            value
            for peer_index, value in enumerate(direct_pairwise[index])
            if peer_index != index
        ]
        mean_correlation = sum(peers) / len(peers) if peers else 0.0
        mean_direct_correlation = (
            sum(direct_peers) / len(direct_peers) if direct_peers else 0.0
        )
        clean = 1.0 if not quality["risk_codes"] else 0.0
        score = (
            0.35 * float(quality["asr_coverage"])
            + 0.30 * mean_correlation
            + 0.20 * float(quality["audio_stability"]["score"])
            + 0.10 * clean
            + 0.05 * float(quality["boundary_distance_score"])
        )
        quality["mean_peer_correlation"] = round(mean_correlation, 6)
        quality["mean_direct_envelope_correlation"] = round(
            mean_direct_correlation,
            6,
        )
        quality["representative_score"] = round(score, 6)
    representative_index = max(
        range(len(qualities)),
        key=lambda index: (qualities[index]["representative_score"], -index),
    )
    reference = occurrence_envelopes[representative_index]
    reference_duration = float(group["occurrences"][representative_index]["duration"])
    mappings = []
    for index, occurrence in enumerate(group["occurrences"]):
        if index == representative_index:
            direct_correlation, lag_frames = 1.0, 0
            correlation = 1.0
        else:
            direct_correlation, lag_frames = normalized_cross_correlation(
                reference,
                occurrence_envelopes[index],
                max_lag_frames=max_lag_frames,
            )
            correlation = onset_pattern_correlation(
                reference,
                occurrence_envelopes[index],
            )
        duration = float(occurrence["duration"])
        ratio = duration / reference_duration if reference_duration > 0 else math.inf
        drift = abs(ratio - 1.0)
        risk_codes = list(qualities[index]["risk_codes"])
        high_confidence = (
            correlation >= policy.min_correlation
            and drift <= policy.max_duration_drift
            and not risk_codes
        )
        mappings.append(
            {
                "occurrence_index": index,
                "correlation": round(correlation, 6),
                "direct_envelope_correlation": round(direct_correlation, 6),
                "lag_seconds": round(
                    lag_frames * policy.hop_samples / sample_rate,
                    6,
                ),
                "duration_ratio": round(ratio, 6),
                "duration_drift_ratio": round(drift, 6),
                "high_confidence": high_confidence,
                "outlier_reasons": [
                    *risk_codes,
                    *(
                        ["low_audio_correlation"]
                        if correlation < policy.min_correlation
                        else []
                    ),
                    *(
                        ["duration_drift_exceeds_3_percent"]
                        if drift > policy.max_duration_drift
                        else []
                    ),
                ],
            }
        )
    return {
        "representative_occurrence_index": representative_index,
        "candidate_quality": qualities,
        "mappings": mappings,
        "policy": {
            "min_correlation": policy.min_correlation,
            "max_duration_drift": policy.max_duration_drift,
            "max_boundary_adjustment": policy.max_boundary_adjustment,
            "max_lag_seconds": policy.max_lag_seconds,
            "sample_rate": sample_rate,
            "frame_samples": policy.frame_samples,
            "hop_samples": policy.hop_samples,
            "audio_time_offset": audio_time_offset,
            "correlation_method": "onset_rhythm_signature_cross_correlation",
            "direct_envelope_correlation_recorded": True,
        },
    }
