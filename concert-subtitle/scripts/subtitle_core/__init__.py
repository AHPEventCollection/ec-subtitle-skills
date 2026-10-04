"""Project-neutral subtitle timing primitives used by concert adapters."""

from .boundary import (
    BoundaryPolicy,
    BoundaryProposal,
    TimedEvent,
    TimedWord,
    propose_boundaries,
)
from .evaluation import (
    AlignmentResult,
    EvalMetrics,
    SubtitleEvent,
    align_events,
    compute_metrics,
)
from .reference import (
    load_subtitle_events,
    match_timing_only_reference,
    timing_metrics,
)
from .repetition import (
    RepetitionPolicy,
    canonical_event_text,
    detect_repetition_group,
    event_has_live_variant,
    event_is_lyric,
    event_risk_codes,
    fallback_sample,
    representative_cycle,
)
from .rhythm import (
    RhythmPolicy,
    audio_stability,
    build_rhythm_evidence,
    decode_audio_pcm,
    normalized_cross_correlation,
    onset_energy_envelope,
    onset_pattern_correlation,
    onset_rhythm_signature,
)
from .text_match import normalize_text, phonetic_key, similarity
from .transcript import (
    TranscriptSegment,
    TranscriptWord,
    load_transcript,
    normalize_transcript_payload,
    write_transcript,
)

__all__ = [
    "AlignmentResult",
    "BoundaryPolicy",
    "BoundaryProposal",
    "EvalMetrics",
    "RepetitionPolicy",
    "RhythmPolicy",
    "SubtitleEvent",
    "TimedEvent",
    "TimedWord",
    "TranscriptSegment",
    "TranscriptWord",
    "align_events",
    "audio_stability",
    "build_rhythm_evidence",
    "canonical_event_text",
    "compute_metrics",
    "decode_audio_pcm",
    "detect_repetition_group",
    "event_has_live_variant",
    "event_is_lyric",
    "event_risk_codes",
    "fallback_sample",
    "load_subtitle_events",
    "load_transcript",
    "normalize_text",
    "normalize_transcript_payload",
    "phonetic_key",
    "propose_boundaries",
    "match_timing_only_reference",
    "normalized_cross_correlation",
    "onset_energy_envelope",
    "onset_pattern_correlation",
    "onset_rhythm_signature",
    "representative_cycle",
    "similarity",
    "timing_metrics",
    "write_transcript",
]
