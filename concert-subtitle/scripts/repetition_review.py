from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any

from build_review_clips import render_review_video
from common import (
    VERSION,
    archive_section_results,
    canonical_hash,
    console_version,
    file_sha256,
    load_events,
    load_lyrics,
    load_manifest,
    manifest_section_contract,
    package_input_hash,
    portable_path,
    read_json,
    resolve_binary,
    resolve_review_media,
    resolve_stored_path,
    section_directory,
    update_status,
    utc_now,
    write_json,
    write_text,
)
from merge_concert_subtitles import render_ass
from subtitle_core import (
    RepetitionPolicy,
    RhythmPolicy,
    SubtitleEvent,
    build_rhythm_evidence,
    decode_audio_pcm,
    detect_repetition_group,
    event_risk_codes,
    fallback_sample,
    load_subtitle_events,
    match_timing_only_reference,
    normalize_text,
    representative_cycle,
    timing_metrics,
)


ARTIFACT_DIR_NAME = f"repetition-rhythm-v{VERSION}"
GROUPS_SCHEMA = "concert.repetition-groups.v1"
RHYTHM_SCHEMA = "concert.rhythm-map.v1"
IMPORT_SCHEMA = "concert.repetition-import.v1"
PROPOSAL_SCHEMA = "concert.repetition-proposal.v1"
DECISION_SCHEMA = "concert.repetition-decision.v1"
APPLICATION_SCHEMA = "concert.repetition-application.v1"
BENCHMARK_SCHEMA = "concert.repetition-benchmark.v1"


def _bind_artifact(payload: dict[str, Any]) -> dict[str, Any]:
    bound = {key: value for key, value in payload.items() if key != "artifact_hash"}
    bound["artifact_hash"] = canonical_hash(bound)
    return bound


def _verify_artifact(payload: dict[str, Any], path: Path) -> None:
    claimed = payload.get("artifact_hash")
    actual = canonical_hash(
        {key: value for key, value in payload.items() if key != "artifact_hash"}
    )
    if not claimed or claimed != actual:
        raise ValueError(f"证据哈希不一致：{path}")


def _write_artifact(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    bound = _bind_artifact(payload)
    write_json(path, bound)
    return bound


def _load_artifact(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"找不到证据：{path}")
    payload = read_json(path)
    _verify_artifact(payload, path)
    return payload


def _artifact_dir(concert_dir: Path, section_id: str) -> Path:
    return (
        concert_dir.resolve()
        / "run"
        / "evidence"
        / ARTIFACT_DIR_NAME
        / section_id
    )


def _review_dir(concert_dir: Path, section_id: str) -> Path:
    return concert_dir.resolve() / "run" / "review" / section_id / "repetition"


def _section_context(concert_dir: Path, section_id: str) -> dict[str, Any]:
    concert_dir = concert_dir.resolve()
    manifest = load_manifest(concert_dir)
    section_index = next(
        (
            index
            for index, section in enumerate(manifest["sections"])
            if section["id"] == section_id
        ),
        None,
    )
    if section_index is None:
        raise ValueError(f"Manifest没有分段：{section_id}")
    section = manifest["sections"][section_index]
    if section["type"] != "song":
        raise ValueError(f"重复传播只允许歌曲分段：{section_id}")
    section_dir = section_directory(concert_dir, section_id)
    package_path = section_dir / "input.json"
    events_path = section_dir / "events.json"
    if not package_path.is_file() or not events_path.is_file():
        raise FileNotFoundError(f"歌曲工作包不完整：{section_dir}")
    package = read_json(package_path)
    if package.get("input_hash") != package_input_hash(package):
        raise ValueError(f"工作包输入哈希失效：{package_path}")
    events_payload = read_json(events_path)
    events = load_events(events_path)
    audio_path = resolve_stored_path(package.get("audio_path"), section_dir)
    if audio_path is None or not audio_path.is_file():
        raise FileNotFoundError(f"分段独立音频不存在：{audio_path}")
    lyrics_path = resolve_stored_path(package.get("lyrics_file"), section_dir)
    if lyrics_path is None or not lyrics_path.is_file():
        raise FileNotFoundError(f"已知歌词不存在：{lyrics_path}")
    source = resolve_stored_path(
        str(manifest.get("review_media") or manifest.get("source_media", "")),
        concert_dir,
    )
    if source is None:
        raise FileNotFoundError("Manifest缺少审查媒体")
    section_duration = float(section["end"]) - float(section["start"])
    return {
        "concert_dir": concert_dir,
        "manifest": manifest,
        "section_index": section_index,
        "section": section,
        "section_dir": section_dir,
        "package": package,
        "events_path": events_path,
        "events_payload": events_payload,
        "events": events,
        "audio_path": audio_path,
        "lyrics_path": lyrics_path,
        "source": source,
        "section_duration": section_duration,
    }


def _bindings(context: dict[str, Any]) -> dict[str, Any]:
    package = context["package"]
    return {
        "manifest_section_hash": canonical_hash(
            manifest_section_contract(
                context["manifest"],
                context["section_index"],
            )
        ),
        "package_input_hash": package["input_hash"],
        "events_hash": canonical_hash(context["events"]),
        "audio_sha256": file_sha256(context["audio_path"]),
        "lyrics_sha256": file_sha256(context["lyrics_path"]),
    }


def _assert_current_bindings(
    context: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    current = _bindings(context)
    mismatches = [
        key for key, value in expected.items() if current.get(key) != value
    ]
    if mismatches:
        raise ValueError(f"当前输入与证据不一致：{','.join(mismatches)}")


def _shift_events(
    events: list[dict[str, Any]],
    start_index: int,
    end_index: int,
    origin: float,
) -> list[dict[str, Any]]:
    return [
        {
            **event,
            "start": round(float(event["start"]) - origin, 6),
            "end": round(float(event["end"]) - origin, 6),
        }
        for event in events[start_index:end_index]
    ]


def prepare_section(
    concert_dir: Path,
    section_id: str,
    *,
    artifact_dir: Path | None = None,
    review_dir: Path | None = None,
    ffmpeg_value: str | None = None,
    render_video: bool = True,
) -> dict[str, Any]:
    context = _section_context(concert_dir, section_id)
    artifact_dir = (artifact_dir or _artifact_dir(concert_dir, section_id)).resolve()
    review_dir = (review_dir or _review_dir(concert_dir, section_id)).resolve()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    review_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = resolve_binary("ffmpeg", ffmpeg_value)
    lyrics = load_lyrics(
        context["lyrics_path"],
        initial_metadata=[
            context["section"].get("title", ""),
            context["section"].get("artist", ""),
        ],
    )
    policy = RepetitionPolicy()
    group = detect_repetition_group(
        context["events"],
        policy,
        known_lyrics=lyrics,
    )
    rhythm_data: dict[str, Any]
    if group is not None:
        pcm, sample_rate = decode_audio_pcm(
            context["audio_path"],
            ffmpeg=ffmpeg,
        )
        audio_offset = (
            float(context["section"]["start"])
            - float(context["package"].get("audio_context_origin", context["section"]["start"]))
        )
        rhythm_data = build_rhythm_evidence(
            context["events"],
            group,
            pcm,
            sample_rate,
            context["section_duration"],
            RhythmPolicy(sample_rate=sample_rate),
            audio_time_offset=audio_offset,
        )
        representative_index = int(rhythm_data["representative_occurrence_index"])
        sample = representative_cycle(
            context["events"],
            group,
            representative_index,
            context["section_duration"],
            policy,
        )
        high_confidence_targets = [
            mapping
            for mapping in rhythm_data["mappings"]
            if mapping["occurrence_index"] != representative_index
            and mapping["high_confidence"]
        ]
        propagation_enabled = bool(
            group["stable"]
            and sample["complete_anchor"]
            and high_confidence_targets
        )
        propagation_reason = (
            "high_confidence_same_song_repetition"
            if propagation_enabled
            else "no_high_confidence_target_occurrence"
        )
    else:
        representative_index = None
        sample = fallback_sample(
            context["events"],
            context["section_duration"],
            policy,
        )
        rhythm_data = {
            "representative_occurrence_index": None,
            "candidate_quality": [],
            "mappings": [],
            "policy": {
                "min_correlation": 0.75,
                "max_duration_drift": 0.03,
                "max_boundary_adjustment": 0.75,
            },
        }
        propagation_enabled = False
        propagation_reason = "no_safe_repetition_group"

    sample_events = _shift_events(
        context["events"],
        int(sample["sample_start_event_index"]),
        int(sample["sample_end_event_index"]),
        float(sample["clip_start"]),
    )
    ass_path = review_dir / "representative.ass"
    write_text(
        ass_path,
        render_ass(sample_events, f"{section_id}-representative"),
        encoding="utf-8-sig",
        newline="\n",
    )
    mp4_path = review_dir / "representative.mp4"
    if render_video:
        source = resolve_review_media(
            context["manifest"],
            context["concert_dir"],
        )
        absolute_start = float(context["section"]["start"]) + float(sample["clip_start"])
        absolute_end = float(context["section"]["start"]) + float(sample["clip_end"])
        render_review_video(
            source=source,
            output_path=mp4_path,
            start=absolute_start,
            end=absolute_end,
            ffmpeg=ffmpeg,
        )

    bindings = _bindings(context)
    groups_payload = _write_artifact(
        artifact_dir / "repetition_groups.json",
        {
            "schema": GROUPS_SCHEMA,
            "tool_version": VERSION,
            "created_at": utc_now(),
            "section_id": section_id,
            "bindings": bindings,
            "normalization": "NFKC+casefold+strip_space_punctuation_symbols",
            "known_lyric_line_count": len(lyrics),
            "propagation_enabled": propagation_enabled,
            "propagation_reason": propagation_reason,
            "groups": [group] if group is not None else [],
            "representative": {
                **sample,
                "occurrence_index": representative_index,
                "ass_path": portable_path(ass_path, context["concert_dir"]),
                "ass_sha256": file_sha256(ass_path),
                "mp4_path": (
                    portable_path(mp4_path, context["concert_dir"])
                    if render_video
                    else None
                ),
                "mp4_sha256": file_sha256(mp4_path) if render_video else None,
                "single_visible_axis": True,
            },
        },
    )
    rhythm_payload = _write_artifact(
        artifact_dir / "rhythm_map.json",
        {
            "schema": RHYTHM_SCHEMA,
            "tool_version": VERSION,
            "created_at": utc_now(),
            "section_id": section_id,
            "bindings": bindings,
            "repetition_groups_hash": groups_payload["artifact_hash"],
            "evidence_role": "same_song_mapping_drift_and_confidence_only",
            "does_not_determine_lyrics": True,
            **rhythm_data,
        },
    )
    return {
        "section_id": section_id,
        "propagation_enabled": propagation_enabled,
        "representative_event_count": len(sample_events),
        "representative_mp4": str(mp4_path) if render_video else None,
        "repetition_groups": str(artifact_dir / "repetition_groups.json"),
        "rhythm_map": str(artifact_dir / "rhythm_map.json"),
        "rhythm_hash": rhythm_payload["artifact_hash"],
    }


def import_reference(
    concert_dir: Path,
    section_id: str,
    reference_path: Path,
    *,
    full_concert: bool = False,
    artifact_dir: Path | None = None,
) -> dict[str, Any]:
    context = _section_context(concert_dir, section_id)
    artifact_dir = (artifact_dir or _artifact_dir(concert_dir, section_id)).resolve()
    groups_path = artifact_dir / "repetition_groups.json"
    groups = _load_artifact(groups_path)
    _assert_current_bindings(context, groups["bindings"])
    representative = groups["representative"]
    expected_ids = list(representative["sample_event_ids"])
    event_by_id = {str(event.get("id", "")): event for event in context["events"]}
    if any(event_id not in event_by_id for event_id in expected_ids):
        raise ValueError("代表样片事件与当前正式事件不一致")
    expected = [event_by_id[event_id] for event_id in expected_ids]
    reference_path = reference_path.resolve()
    reference = load_subtitle_events(reference_path, "last")
    expected_absolute_start = (
        float(context["section"]["start"]) + float(expected[0]["start"])
        if full_concert
        else None
    )
    matched = match_timing_only_reference(
        expected,
        reference,
        full_concert=full_concert,
        expected_absolute_start=expected_absolute_start,
    )
    timings = []
    outliers = list(matched["outliers"])
    if matched["accepted"]:
        for expected_event, imported in zip(expected, matched["matched_events"]):
            if full_concert:
                imported_start = float(imported["start"]) - float(context["section"]["start"])
                imported_end = float(imported["end"]) - float(context["section"]["start"])
            else:
                imported_start = float(imported["start"]) + float(representative["clip_start"])
                imported_end = float(imported["end"]) + float(representative["clip_start"])
            if (
                imported_start < 0
                or imported_end <= imported_start
                or imported_end > context["section_duration"]
            ):
                outliers.append(
                    {
                        "code": "imported_time_outside_section",
                        "event_id": expected_event.get("id"),
                    }
                )
            timings.append(
                {
                    "event_id": str(expected_event.get("id", "")),
                    "normalized_text": normalize_text(
                        str(expected_event.get("source_text", ""))
                    ),
                    "original_start": float(expected_event["start"]),
                    "original_end": float(expected_event["end"]),
                    "imported_start": round(imported_start, 6),
                    "imported_end": round(imported_end, 6),
                    "start_adjustment": round(
                        imported_start - float(expected_event["start"]),
                        6,
                    ),
                    "end_adjustment": round(
                        imported_end - float(expected_event["end"]),
                        6,
                    ),
                }
            )
    accepted = bool(matched["accepted"] and not outliers)
    payload = _write_artifact(
        artifact_dir / "import.json",
        {
            "schema": IMPORT_SCHEMA,
            "tool_version": VERSION,
            "created_at": utc_now(),
            "section_id": section_id,
            "bindings": groups["bindings"],
            "repetition_groups_hash": groups["artifact_hash"],
            "reference_path": str(reference_path),
            "reference_sha256": file_sha256(reference_path),
            "reference_read_only": True,
            "reference_mode": "full_concert" if full_concert else "sample_relative",
            "accepted": accepted,
            "text_order_count_preserved": accepted,
            "timings": timings if accepted else [],
            "outliers": outliers,
        },
    )
    return payload


def _new_edge(
    original: float,
    proposed: float,
    source: str,
    reasons: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "original": round(original, 6),
        "proposed": round(proposed, 6),
        "adjustment": round(proposed - original, 6),
        "source": source,
        "outlier_reasons": sorted(set(reasons or [])),
    }


def _edge_is_eligible(edge: dict[str, Any]) -> bool:
    return not edge["outlier_reasons"] and not math.isclose(
        float(edge["original"]),
        float(edge["proposed"]),
        abs_tol=1e-6,
    )


def _validate_change_collisions(
    events: list[dict[str, Any]],
    changes: list[dict[str, Any]],
) -> None:
    change_by_id = {change["event_id"]: change for change in changes}

    def times(event: dict[str, Any]) -> tuple[float, float]:
        change = change_by_id.get(str(event.get("id", "")))
        start = float(event["start"])
        end = float(event["end"])
        if change:
            if _edge_is_eligible(change["start"]):
                start = float(change["start"]["proposed"])
            if _edge_is_eligible(change["end"]):
                end = float(change["end"]["proposed"])
        return start, end

    for event in events:
        change = change_by_id.get(str(event.get("id", "")))
        if change is None:
            continue
        start, end = times(event)
        if end <= start:
            if _edge_is_eligible(change["start"]):
                change["start"]["outlier_reasons"].append("non_positive_duration")
            if _edge_is_eligible(change["end"]):
                change["end"]["outlier_reasons"].append("non_positive_duration")
    for previous, current in zip(events, events[1:]):
        previous_start, previous_end = times(previous)
        current_start, current_end = times(current)
        del previous_start, current_end
        if previous_end <= current_start:
            continue
        previous_change = change_by_id.get(str(previous.get("id", "")))
        current_change = change_by_id.get(str(current.get("id", "")))
        if previous_change and _edge_is_eligible(previous_change["end"]):
            previous_change["end"]["outlier_reasons"].append("would_overlap_next")
        if current_change and _edge_is_eligible(current_change["start"]):
            current_change["start"]["outlier_reasons"].append("would_overlap_previous")
    for change in changes:
        for boundary in ("start", "end"):
            change[boundary]["outlier_reasons"] = sorted(
                set(change[boundary]["outlier_reasons"])
            )


def _render_outlier_assets(
    context: dict[str, Any],
    groups: dict[str, Any],
    occurrence_indices: set[int],
    review_dir: Path,
    ffmpeg: Path,
) -> list[dict[str, Any]]:
    if not occurrence_indices:
        return []
    group = groups["groups"][0]
    source = resolve_review_media(context["manifest"], context["concert_dir"])
    assets = []
    for occurrence_index in sorted(occurrence_indices):
        occurrence = group["occurrences"][occurrence_index]
        first = int(occurrence["start_event_index"])
        last = int(occurrence["end_event_index"])
        start = max(0.0, float(occurrence["start"]) - 3.0)
        end = min(context["section_duration"], float(occurrence["end"]) + 3.0)
        shifted = _shift_events(context["events"], first, last, start)
        ass_path = review_dir / "outliers" / f"occurrence-{occurrence_index + 1:02d}.ass"
        mp4_path = review_dir / "outliers" / f"occurrence-{occurrence_index + 1:02d}.mp4"
        write_text(
            ass_path,
            render_ass(shifted, f"{context['section']['id']}-outlier-{occurrence_index + 1}"),
            encoding="utf-8-sig",
            newline="\n",
        )
        render_review_video(
            source=source,
            output_path=mp4_path,
            start=float(context["section"]["start"]) + start,
            end=float(context["section"]["start"]) + end,
            ffmpeg=ffmpeg,
        )
        assets.append(
            {
                "occurrence_index": occurrence_index,
                "mp4_path": portable_path(mp4_path, context["concert_dir"]),
                "mp4_sha256": file_sha256(mp4_path),
            }
        )
    return assets


def propose_section(
    concert_dir: Path,
    section_id: str,
    *,
    artifact_dir: Path | None = None,
    review_dir: Path | None = None,
    ffmpeg_value: str | None = None,
    render_outliers: bool = True,
) -> dict[str, Any]:
    context = _section_context(concert_dir, section_id)
    artifact_dir = (artifact_dir or _artifact_dir(concert_dir, section_id)).resolve()
    review_dir = (review_dir or _review_dir(concert_dir, section_id)).resolve()
    groups = _load_artifact(artifact_dir / "repetition_groups.json")
    rhythm = _load_artifact(artifact_dir / "rhythm_map.json")
    imported = _load_artifact(artifact_dir / "import.json")
    _assert_current_bindings(context, groups["bindings"])
    if rhythm.get("repetition_groups_hash") != groups["artifact_hash"]:
        raise ValueError("节奏图没有绑定当前重复分组")
    if imported.get("repetition_groups_hash") != groups["artifact_hash"]:
        raise ValueError("回灌结果没有绑定当前重复分组")
    if not imported.get("accepted"):
        raise ValueError("回灌结果被拒绝，不能生成传播提案")

    events = context["events"]
    event_by_id = {str(event.get("id", "")): event for event in events}
    imported_by_id = {item["event_id"]: item for item in imported["timings"]}
    changes: list[dict[str, Any]] = []
    change_by_id: dict[str, dict[str, Any]] = {}
    for item in imported["timings"]:
        event = event_by_id[item["event_id"]]
        change = {
            "event_id": item["event_id"],
            "normalized_text": normalize_text(str(event.get("source_text", ""))),
            "mode": "representative_direct",
            "occurrence_index": groups["representative"].get("occurrence_index"),
            "start": _new_edge(
                float(event["start"]),
                float(item["imported_start"]),
                "human_reviewed_representative",
            ),
            "end": _new_edge(
                float(event["end"]),
                float(item["imported_end"]),
                "human_reviewed_representative",
            ),
        }
        changes.append(change)
        change_by_id[change["event_id"]] = change

    review_reduction_ids: set[str] = set()
    boundary_outliers: list[dict[str, Any]] = []
    if groups["groups"]:
        group = groups["groups"][0]
        representative_index = int(rhythm["representative_occurrence_index"])
        representative_occurrence = group["occurrences"][representative_index]
        representative_ids = list(representative_occurrence["event_ids"])
        for mapping in rhythm["mappings"]:
            occurrence_index = int(mapping["occurrence_index"])
            if occurrence_index == representative_index:
                continue
            target_occurrence = group["occurrences"][occurrence_index]
            target_ids = list(target_occurrence["event_ids"])
            shared_reasons = list(mapping.get("outlier_reasons", []))
            for representative_id, target_id in zip(representative_ids, target_ids):
                representative_event = event_by_id[representative_id]
                target_event = event_by_id[target_id]
                reasons = list(shared_reasons)
                if normalize_text(str(representative_event.get("source_text", ""))) != normalize_text(
                    str(target_event.get("source_text", ""))
                ):
                    reasons.append("text_mismatch")
                reasons.extend(event_risk_codes(representative_event))
                reasons.extend(event_risk_codes(target_event))
                imported_timing = imported_by_id.get(representative_id)
                if imported_timing is None:
                    reasons.append("representative_boundary_not_imported")
                    start_delta = 0.0
                    end_delta = 0.0
                else:
                    start_delta = float(imported_timing["start_adjustment"])
                    end_delta = float(imported_timing["end_adjustment"])
                ratio = float(mapping["duration_ratio"])
                proposed_start = float(target_event["start"]) + start_delta * ratio
                proposed_end = float(target_event["end"]) + end_delta * ratio
                start_reasons = list(reasons)
                end_reasons = list(reasons)
                if abs(proposed_start - float(target_event["start"])) > 0.75:
                    start_reasons.append("start_adjustment_exceeds_0_75_seconds")
                if abs(proposed_end - float(target_event["end"])) > 0.75:
                    end_reasons.append("end_adjustment_exceeds_0_75_seconds")
                change = {
                    "event_id": target_id,
                    "normalized_text": normalize_text(str(target_event.get("source_text", ""))),
                    "mode": "same_song_propagation",
                    "occurrence_index": occurrence_index,
                    "reference_event_id": representative_id,
                    "correlation": mapping["correlation"],
                    "duration_drift_ratio": mapping["duration_drift_ratio"],
                    "start": _new_edge(
                        float(target_event["start"]),
                        proposed_start,
                        "representative_start_delta_scaled_by_duration",
                        start_reasons,
                    ),
                    "end": _new_edge(
                        float(target_event["end"]),
                        proposed_end,
                        "representative_end_delta_scaled_by_duration",
                        end_reasons,
                    ),
                }
                changes.append(change)
                change_by_id[target_id] = change
                if not start_reasons and not end_reasons:
                    review_reduction_ids.add(target_id)

    _validate_change_collisions(events, changes)
    review_reduction_ids = {
        event_id
        for event_id in review_reduction_ids
        if all(
            not change_by_id[event_id][boundary]["outlier_reasons"]
            for boundary in ("start", "end")
        )
    }
    for change in changes:
        for boundary in ("start", "end"):
            for reason in change[boundary]["outlier_reasons"]:
                boundary_outliers.append(
                    {
                        "event_id": change["event_id"],
                        "occurrence_index": change.get("occurrence_index"),
                        "boundary": boundary,
                        "code": reason,
                    }
                )
        change["eligible_boundaries"] = [
            boundary
            for boundary in ("start", "end")
            if _edge_is_eligible(change[boundary])
        ]
        change["outlier"] = not bool(change["eligible_boundaries"])

    occurrence_indices = {
        int(outlier["occurrence_index"])
        for outlier in boundary_outliers
        if outlier.get("occurrence_index") is not None
    }
    outlier_assets: list[dict[str, Any]] = []
    if render_outliers and occurrence_indices and groups["groups"]:
        if not context["source"].is_file():
            raise FileNotFoundError(f"源视频不存在：{context['source']}")
        ffmpeg = resolve_binary("ffmpeg", ffmpeg_value)
        outlier_assets = _render_outlier_assets(
            context,
            groups,
            occurrence_indices,
            review_dir,
            ffmpeg,
        )
    payload = _write_artifact(
        artifact_dir / "proposal.json",
        {
            "schema": PROPOSAL_SCHEMA,
            "tool_version": VERSION,
            "created_at": utc_now(),
            "section_id": section_id,
            "bindings": groups["bindings"],
            "repetition_groups_hash": groups["artifact_hash"],
            "rhythm_map_hash": rhythm["artifact_hash"],
            "import_hash": imported["artifact_hash"],
            "applied": False,
            "boundary_independent": True,
            "changes": changes,
            "outliers": boundary_outliers,
            "outlier_assets": outlier_assets,
            "representative_asset": groups["representative"],
            "estimated_review_reduction_event_count": len(review_reduction_ids),
            "review_reduction_event_ids": sorted(review_reduction_ids),
        },
    )
    return payload


def decide_proposal(
    concert_dir: Path,
    section_id: str,
    decision: str,
    proposal_sha256: str,
    *,
    representative_reviewed: bool,
    all_outliers_reviewed: bool,
    reviewer: str,
    notes: str,
    artifact_dir: Path | None = None,
    require_assets: bool = True,
) -> dict[str, Any]:
    if decision not in {"approve", "reject"}:
        raise ValueError("裁决只能是approve或reject")
    artifact_dir = (artifact_dir or _artifact_dir(concert_dir, section_id)).resolve()
    proposal_path = artifact_dir / "proposal.json"
    proposal = _load_artifact(proposal_path)
    actual_sha256 = file_sha256(proposal_path)
    if proposal_sha256.casefold() != actual_sha256.casefold():
        raise ValueError("给定提案SHA256与当前提案文件不一致")
    if proposal.get("applied") is not False:
        raise ValueError("只允许裁决尚未应用的提案")
    if decision == "approve":
        if not representative_reviewed or not all_outliers_reviewed:
            raise ValueError("批准必须确认代表样片和全部离群样片已看")
        if require_assets:
            concert_dir = concert_dir.resolve()
            representative_path = resolve_stored_path(
                proposal["representative_asset"].get("mp4_path"),
                concert_dir,
            )
            if representative_path is None or not representative_path.is_file():
                raise FileNotFoundError("代表样片不存在，不能批准")
            for asset in proposal.get("outlier_assets", []):
                path = resolve_stored_path(asset.get("mp4_path"), concert_dir)
                if path is None or not path.is_file():
                    raise FileNotFoundError("离群样片不完整，不能批准")
    return _write_artifact(
        artifact_dir / "decision.json",
        {
            "schema": DECISION_SCHEMA,
            "tool_version": VERSION,
            "created_at": utc_now(),
            "section_id": section_id,
            "decision": decision,
            "proposal_file_sha256": actual_sha256,
            "proposal_artifact_hash": proposal["artifact_hash"],
            "representative_reviewed": representative_reviewed,
            "all_outliers_reviewed": all_outliers_reviewed,
            "reviewer": reviewer,
            "notes": notes,
        },
    )


def _apply_changes_in_memory(
    events: list[dict[str, Any]],
    proposal: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    change_by_id = {change["event_id"]: change for change in proposal["changes"]}
    updated: list[dict[str, Any]] = []
    changed_ids: list[str] = []
    for event in events:
        event_id = str(event.get("id", ""))
        change = change_by_id.get(event_id)
        current = dict(event)
        if change is not None:
            if normalize_text(str(event.get("source_text", ""))) != change["normalized_text"]:
                raise ValueError(f"事件正文已变化：{event_id}")
            changed = False
            if _edge_is_eligible(change["start"]):
                current["start"] = float(change["start"]["proposed"])
                changed = True
            if _edge_is_eligible(change["end"]):
                current["end"] = float(change["end"]["proposed"])
                changed = True
            if changed:
                changed_ids.append(event_id)
        updated.append(current)
    for event in updated:
        if not math.isfinite(float(event["start"])) or not math.isfinite(float(event["end"])):
            raise ValueError("提案包含非有限时间")
        if float(event["start"]) < 0 or float(event["end"]) <= float(event["start"]):
            raise ValueError(f"提案产生非正时长：{event.get('id')}")
    for previous, current in zip(updated, updated[1:]):
        if float(previous["end"]) > float(current["start"]) + 0.001:
            raise ValueError(
                f"提案产生重叠：{previous.get('id')}->{current.get('id')}"
            )
    return updated, changed_ids


def apply_proposal(
    concert_dir: Path,
    section_id: str,
    *,
    artifact_dir: Path | None = None,
) -> dict[str, Any]:
    context = _section_context(concert_dir, section_id)
    artifact_dir = (artifact_dir or _artifact_dir(concert_dir, section_id)).resolve()
    proposal_path = artifact_dir / "proposal.json"
    decision_path = artifact_dir / "decision.json"
    proposal = _load_artifact(proposal_path)
    decision = _load_artifact(decision_path)
    proposal_file_hash = file_sha256(proposal_path)
    if decision.get("decision") != "approve":
        raise ValueError("提案未获批准")
    if decision.get("proposal_file_sha256") != proposal_file_hash:
        raise ValueError("提案文件哈希在批准后发生变化")
    if decision.get("proposal_artifact_hash") != proposal.get("artifact_hash"):
        raise ValueError("批准记录没有绑定当前提案")
    if proposal.get("applied") is not False:
        raise ValueError("提案必须保持applied:false并通过独立应用记录落地")
    _assert_current_bindings(context, proposal["bindings"])
    updated_events, changed_ids = _apply_changes_in_memory(
        context["events"],
        proposal,
    )
    if not changed_ids:
        raise ValueError("提案没有可安全应用的非离群时间修订")
    events_payload = context["events_payload"]
    if isinstance(events_payload, list):
        new_payload: Any = updated_events
    else:
        new_payload = {**events_payload, "events": updated_events}
    archived = archive_section_results(
        context["section_dir"],
        f"repetition-{proposal['artifact_hash'][:12]}",
    )
    write_json(context["events_path"], new_payload)
    status = update_status(
        context["section_dir"],
        "stale",
        "repetition_timing_applied_requires_gate_and_main_review",
        section_id=section_id,
        proposal_hash=proposal["artifact_hash"],
        decision_hash=decision["artifact_hash"],
        changed_event_count=len(changed_ids),
    )
    return _write_artifact(
        artifact_dir / "application.json",
        {
            "schema": APPLICATION_SCHEMA,
            "tool_version": VERSION,
            "created_at": utc_now(),
            "section_id": section_id,
            "applied": True,
            "proposal_file_sha256": proposal_file_hash,
            "proposal_artifact_hash": proposal["artifact_hash"],
            "decision_artifact_hash": decision["artifact_hash"],
            "changed_event_ids": changed_ids,
            "archived_previous_results": str(archived) if archived else None,
            "new_events_hash": canonical_hash(updated_events),
            "status": status["status"],
            "required_next_steps": [
                "validate_section",
                "main_thread_full_section_review",
                "new_approval",
            ],
        },
    )


def _proposal_events(
    events: list[dict[str, Any]],
    proposal: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if proposal is None:
        return [dict(event) for event in events]
    updated, _ = _apply_changes_in_memory(events, proposal)
    return updated


def _absolute_subtitle_events(
    events: list[dict[str, Any]],
    section_start: float,
    index_origin: int,
) -> list[SubtitleEvent]:
    return [
        SubtitleEvent(
            index=index_origin + index,
            start=float(event["start"]) + section_start,
            end=float(event["end"]) + section_start,
            text=str(event.get("source_text", "")),
        )
        for index, event in enumerate(events)
    ]


def _worsened_propagations(
    machine: list[SubtitleEvent],
    proposed: list[SubtitleEvent],
    reference: list[SubtitleEvent],
    propagated_indices: set[int],
) -> int:
    machine_report = timing_metrics(machine, reference)
    proposed_report = timing_metrics(proposed, reference)
    machine_pairs = {
        index: pair
        for pair in machine_report["alignment"]["pairs"]
        for index in pair["hypothesis_indices"]
    }
    proposed_pairs = {
        index: pair
        for pair in proposed_report["alignment"]["pairs"]
        for index in pair["hypothesis_indices"]
    }
    failures = 0
    for index in propagated_indices:
        before = machine_pairs.get(index)
        after = proposed_pairs.get(index)
        if before is None or after is None:
            failures += 1
            continue
        if (
            abs(float(after["start_error"])) > abs(float(before["start_error"])) + 0.001
            or abs(float(after["end_error"])) > abs(float(before["end_error"])) + 0.001
        ):
            failures += 1
    return failures


def _formal_hash_snapshot(
    concert_dir: Path,
    songs: list[dict[str, Any]],
    reference_path: Path,
) -> dict[str, str]:
    paths = {
        concert_dir / "run" / "concert_manifest.json",
        reference_path,
        *(path for path in (concert_dir / "output").rglob("*") if path.is_file()),
    }
    for section in songs:
        section_dir = section_directory(concert_dir, section["id"])
        for name in (
            "input.json",
            "events.json",
            "report.json",
            "gate_report.json",
            "review_decision.json",
            "status.json",
        ):
            path = section_dir / name
            if path.is_file():
                paths.add(path)
    return {
        portable_path(path, concert_dir): file_sha256(path)
        for path in sorted(paths, key=lambda item: str(item).casefold())
    }


def benchmark_concert(
    concert_dir: Path,
    reference_path: Path,
    *,
    ffmpeg_value: str | None = None,
) -> dict[str, Any]:
    concert_dir = concert_dir.resolve()
    reference_path = reference_path.resolve()
    manifest = load_manifest(concert_dir)
    songs = [section for section in manifest["sections"] if section["type"] == "song"]
    if len(songs) != 17:
        raise ValueError(f"Silent Siren回归要求17首歌，当前{len(songs)}首")
    evidence_root = concert_dir / "run" / "evidence" / ARTIFACT_DIR_NAME
    evidence_root.mkdir(parents=True, exist_ok=True)
    reference_hash_before = file_sha256(reference_path)
    formal_hashes_before = _formal_hash_snapshot(
        concert_dir,
        songs,
        reference_path,
    )
    reference_all = load_subtitle_events(reference_path, "last")
    all_machine: list[SubtitleEvent] = []
    all_proposed: list[SubtitleEvent] = []
    song_reports = []
    index_origin = 0
    total_reduced = 0
    total_propagated = 0
    total_wrong = 0
    total_outliers = 0
    total_outlier_boundaries = 0
    total_proposed_deep_review = 0
    for section in songs:
        section_id = section["id"]
        section_root = evidence_root / section_id
        prepare = prepare_section(
            concert_dir,
            section_id,
            artifact_dir=section_root,
            review_dir=section_root,
            ffmpeg_value=ffmpeg_value,
            render_video=True,
        )
        imported = import_reference(
            concert_dir,
            section_id,
            reference_path,
            full_concert=True,
            artifact_dir=section_root,
        )
        proposal = None
        if imported["accepted"]:
            proposal = propose_section(
                concert_dir,
                section_id,
                artifact_dir=section_root,
                review_dir=section_root,
                ffmpeg_value=ffmpeg_value,
                render_outliers=False,
            )
        context = _section_context(concert_dir, section_id)
        proposed_events = _proposal_events(context["events"], proposal)
        machine_absolute = _absolute_subtitle_events(
            context["events"],
            float(section["start"]),
            index_origin,
        )
        proposed_absolute = _absolute_subtitle_events(
            proposed_events,
            float(section["start"]),
            index_origin,
        )
        section_reference = [
            event
            for event in reference_all
            if event.end > float(section["start"])
            and event.start < float(section["end"])
        ]
        machine_metrics = timing_metrics(machine_absolute, section_reference)
        proposal_metrics = timing_metrics(proposed_absolute, section_reference)
        reduction_ids = set(proposal.get("review_reduction_event_ids", [])) if proposal else set()
        id_to_index = {
            str(event.get("id", "")): index_origin + index
            for index, event in enumerate(context["events"])
        }
        propagated_indices = {
            id_to_index[event_id]
            for event_id in reduction_ids
            if event_id in id_to_index
        }
        wrong = _worsened_propagations(
            machine_absolute,
            proposed_absolute,
            section_reference,
            propagated_indices,
        )
        section_outliers = (
            proposal["outliers"] if proposal else imported.get("outliers", [])
        )
        outlier_event_ids = {
            str(item.get("event_id", item.get("code", "unknown")))
            for item in section_outliers
        }
        outlier_count = len(outlier_event_ids)
        section_event_count = len(context["events"])
        representative_event_count = prepare["representative_event_count"]
        if prepare["propagation_enabled"] and proposal is not None:
            proposed_deep_review = min(
                section_event_count,
                representative_event_count + outlier_count,
            )
        else:
            proposed_deep_review = section_event_count
        deep_review_reduction = section_event_count - proposed_deep_review
        total_reduced += deep_review_reduction
        total_proposed_deep_review += proposed_deep_review
        total_propagated += len(propagated_indices)
        total_wrong += wrong
        total_outliers += outlier_count
        total_outlier_boundaries += len(section_outliers)
        all_machine.extend(machine_absolute)
        all_proposed.extend(proposed_absolute)
        song_reports.append(
            {
                "section_id": section_id,
                "title": section.get("title", ""),
                "representative_mp4": prepare["representative_mp4"],
                "representative_event_count": representative_event_count,
                "propagation_enabled": prepare["propagation_enabled"],
                "import_accepted": imported["accepted"],
                "outlier_count": outlier_count,
                "baseline_deep_review_event_count": section_event_count,
                "proposed_deep_review_event_count": proposed_deep_review,
                "estimated_review_reduction_event_count": deep_review_reduction,
                "high_confidence_propagated_event_count": len(reduction_ids),
                "incorrect_propagation_count": wrong,
                "machine": machine_metrics,
                "proposal": proposal_metrics,
            }
        )
        index_origin += len(context["events"])

    overall_machine = timing_metrics(all_machine, reference_all)
    overall_proposal = timing_metrics(all_proposed, reference_all)
    formal_hashes_after = _formal_hash_snapshot(
        concert_dir,
        songs,
        reference_path,
    )
    reference_hash_after = file_sha256(reference_path)
    formal_unchanged = (
        formal_hashes_before == formal_hashes_after
        and reference_hash_before == reference_hash_after
    )
    total_song_events = len(all_machine)
    report = _write_artifact(
        evidence_root / "benchmark_report.json",
        {
            "schema": BENCHMARK_SCHEMA,
            "tool_version": VERSION,
            "created_at": utc_now(),
            "concert_id": manifest["concert_id"],
            "reference_path": str(reference_path),
            "reference_sha256": reference_hash_before,
            "song_count": len(songs),
            "representative_clip_count": sum(
                Path(item["representative_mp4"]).is_file() for item in song_reports
            ),
            "machine": overall_machine,
            "proposal": overall_proposal,
            "total_song_event_count": total_song_events,
            "representative_event_count": sum(
                item["representative_event_count"] for item in song_reports
            ),
            "outlier_count": total_outliers,
            "outlier_boundary_count": total_outlier_boundaries,
            "baseline_deep_review_event_count": total_song_events,
            "proposed_deep_review_event_count": total_proposed_deep_review,
            "estimated_review_reduction_event_count": total_reduced,
            "estimated_review_reduction_ratio": round(
                total_reduced / total_song_events,
                6,
            ) if total_song_events else 0.0,
            "propagated_event_count": total_propagated,
            "incorrect_propagation_count": total_wrong,
            "incorrect_propagation_rate": round(
                total_wrong / total_propagated,
                6,
            ) if total_propagated else 0.0,
            "formal_files_unchanged": formal_unchanged,
            "formal_hashes_before": formal_hashes_before,
            "formal_hashes_after": formal_hashes_after,
            "songs": song_reports,
            "completion_checks": {
                "seventeen_representative_clips": len(songs) == 17,
                "review_reduction_at_least_30_percent": (
                    total_reduced / total_song_events >= 0.30
                    if total_song_events
                    else False
                ),
                "start_p90_not_worse": (
                    overall_proposal["metrics"]["start"]["p90"]
                    <= overall_machine["metrics"]["start"]["p90"]
                ),
                "end_p90_not_worse": (
                    overall_proposal["metrics"]["end"]["p90"]
                    <= overall_machine["metrics"]["end"]["p90"]
                ),
                "incorrect_propagation_rate_zero": total_wrong == 0,
                "formal_files_unchanged": formal_unchanged,
            },
        },
    )
    return report


def _song_ids(concert_dir: Path, requested: list[str] | None) -> list[str]:
    manifest = load_manifest(concert_dir.resolve())
    available = [
        section["id"] for section in manifest["sections"] if section["type"] == "song"
    ]
    if requested:
        missing = sorted(set(requested) - set(available))
        if missing:
            raise ValueError(f"不是歌曲分段：{','.join(missing)}")
        return requested
    return available


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="准备代表样片并安全传播同曲重复段的时间修订"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="分析重复段并生成代表样片")
    prepare.add_argument("--concert-dir", required=True, type=Path)
    prepare.add_argument("--section", action="append", dest="sections")
    prepare.add_argument("--ffmpeg")

    importer = subparsers.add_parser("import", help="回灌人工ASS/SRT时间轴")
    importer.add_argument("--concert-dir", required=True, type=Path)
    importer.add_argument("--section", required=True)
    importer.add_argument("--reference", required=True, type=Path)
    importer.add_argument("--full-concert-reference", action="store_true")

    propose = subparsers.add_parser("propose", help="生成只读传播提案")
    propose.add_argument("--concert-dir", required=True, type=Path)
    propose.add_argument("--section", required=True)
    propose.add_argument("--ffmpeg")

    decide = subparsers.add_parser("decide", help="绑定提案哈希并记录裁决")
    decide.add_argument("--concert-dir", required=True, type=Path)
    decide.add_argument("--section", required=True)
    decide.add_argument("--decision", required=True, choices=("approve", "reject"))
    decide.add_argument("--proposal-sha256", required=True)
    decide.add_argument("--representative-reviewed", action="store_true")
    decide.add_argument("--all-outliers-reviewed", action="store_true")
    decide.add_argument("--reviewer", default="main")
    decide.add_argument("--notes", default="")

    apply = subparsers.add_parser("apply", help="应用已批准的非离群时间边界")
    apply.add_argument("--concert-dir", required=True, type=Path)
    apply.add_argument("--section", required=True)

    benchmark = subparsers.add_parser("benchmark", help="运行17首Silent Siren只读回归")
    benchmark.add_argument("--concert-dir", required=True, type=Path)
    benchmark.add_argument("--reference", required=True, type=Path)
    benchmark.add_argument("--ffmpeg")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    console_version("repetition_review")
    if args.command == "prepare":
        for section_id in _song_ids(args.concert_dir, args.sections):
            summary = prepare_section(
                args.concert_dir,
                section_id,
                ffmpeg_value=args.ffmpeg,
            )
            print(
                f"{section_id}: representative_events={summary['representative_event_count']} "
                f"propagation_enabled={str(summary['propagation_enabled']).lower()}"
            )
        return 0
    if args.command == "import":
        result = import_reference(
            args.concert_dir,
            args.section,
            args.reference,
            full_concert=args.full_concert_reference,
        )
        print(
            f"{args.section}: accepted={str(result['accepted']).lower()} "
            f"outliers={len(result['outliers'])}"
        )
        return 0 if result["accepted"] else 2
    if args.command == "propose":
        result = propose_section(
            args.concert_dir,
            args.section,
            ffmpeg_value=args.ffmpeg,
        )
        proposal_path = _artifact_dir(args.concert_dir, args.section) / "proposal.json"
        print(
            f"{args.section}: applied=false changes={len(result['changes'])} "
            f"outliers={len(result['outliers'])} sha256={file_sha256(proposal_path)}"
        )
        return 0
    if args.command == "decide":
        result = decide_proposal(
            args.concert_dir,
            args.section,
            args.decision,
            args.proposal_sha256,
            representative_reviewed=args.representative_reviewed,
            all_outliers_reviewed=args.all_outliers_reviewed,
            reviewer=args.reviewer,
            notes=args.notes,
        )
        print(f"{args.section}: decision={result['decision']}")
        return 0
    if args.command == "apply":
        result = apply_proposal(args.concert_dir, args.section)
        print(
            f"{args.section}: applied=true changed={len(result['changed_event_ids'])} "
            "status=stale"
        )
        return 0
    if args.command == "benchmark":
        result = benchmark_concert(
            args.concert_dir,
            args.reference,
            ffmpeg_value=args.ffmpeg,
        )
        print(
            f"songs={result['song_count']} clips={result['representative_clip_count']} "
            f"review_reduction={result['estimated_review_reduction_ratio']:.1%} "
            f"incorrect_propagation_rate={result['incorrect_propagation_rate']:.1%}"
        )
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
