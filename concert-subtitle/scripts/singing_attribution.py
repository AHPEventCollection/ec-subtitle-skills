"""Evidence-backed singing annotations; no automatic identity or approval claims."""
from __future__ import annotations

import re
from typing import Any

from common import event_source_text

_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_CONFIRMATION_BASES = {"audio_review", "continuous_av_review", "user_correction"}


def validate_singing_events(
    events: list[dict[str, Any]], section_type: str | None = None,
) -> list[dict[str, Any]]:
    """Validate annotation/text alignment and palette, not the truth of listening."""
    failures: list[dict[str, Any]] = []
    palette: dict[str, str] = {}
    for index, event in enumerate(events):
        if "singing_parts" not in event:
            continue
        kind = section_type or event.get("section_type", "song")
        try:
            if kind != "song" or event.get("role", "lyric") == "speech":
                raise ValueError("演唱归属只用于歌曲歌词，不能代替MC说话人判断")
            parts = event["singing_parts"]
            if not isinstance(parts, list) or not parts:
                raise ValueError("singing_parts必须是非空数组")
            if any(not isinstance(part, dict) or not isinstance(part.get("text"), str)
                   or not part["text"] for part in parts):
                raise ValueError("每个演唱短句必须包含原文text")
            if "".join(part["text"] for part in parts) != event_source_text(event).strip():
                raise ValueError("演唱短句拼接后必须逐字等于原文，不得改词或丢标点")
            for part in parts:
                mode, status = part.get("mode"), part.get("status")
                if not isinstance(mode, str) or mode not in {"solo", "group", "unknown"}:
                    raise ValueError("mode只允许solo、group或unknown")
                if not isinstance(status, str) or status not in {"candidate", "confirmed"}:
                    raise ValueError("status只允许candidate或confirmed")
                singers = part.get("performer_ids", [])
                if not isinstance(singers, list) or any(not isinstance(s, str) or not s.strip() for s in singers):
                    raise ValueError("performer_ids必须为稳定身份ID数组")
                if len(set(singers)) != len(singers):
                    raise ValueError("performer_ids不能重复")
                if (mode == "solo" and len(singers) != 1
                        or mode == "group" and len(singers) == 1
                        or mode == "unknown" and singers):
                    raise ValueError("solo需一人，group可未知成员或至少两人，unknown不指定身份")
                if mode == "unknown" and status != "candidate":
                    raise ValueError("unknown不能标成confirmed")
                color = part.get("color")
                if color is not None and (mode != "solo" or not isinstance(color, str) or not _COLOR.fullmatch(color)):
                    raise ValueError("颜色仅用于solo，格式为#RRGGBB")
                evidence = part.get("evidence", [])
                if not isinstance(evidence, list) or any(
                    not isinstance(item, dict)
                    or not isinstance(item.get("basis"), str) or not item["basis"].strip()
                    or not isinstance(item.get("reference"), str) or not item["reference"].strip()
                    for item in evidence
                ):
                    raise ValueError("evidence需包含basis与可定位的reference")
                if status == "confirmed" and not any(item["basis"] in _CONFIRMATION_BASES for item in evidence):
                    raise ValueError("confirmed需真实回听、连续音画或用户纠正依据，镜头和结构不足以确认")
                if mode == "solo" and status == "confirmed" and color:
                    previous = palette.setdefault(singers[0], color.upper())
                    if previous != color.upper():
                        raise ValueError(f"同一演唱者颜色不一致：{singers[0]}")
        except ValueError as error:
            failures.append({"code": "invalid_singing_attribution", "event_index": index, "reason": str(error)})
    return failures


def singing_text_runs(event: dict[str, Any]) -> list[tuple[str, str]]:
    """Return original-text runs and RGB colors; candidates/groups stay white."""
    if "singing_parts" not in event:
        return [(event_source_text(event).strip(), "#FFFFFF")]
    failures = validate_singing_events([event])
    if failures:
        raise ValueError(failures[0]["reason"])
    return [
        (part["text"], (part.get("color") or "#FFFFFF")
         if part["mode"] == "solo" and part["status"] == "confirmed" else "#FFFFFF")
        for part in event["singing_parts"]
    ]


def ass_color(rgb: str) -> str:
    return "00" + rgb[5:7].upper() + rgb[3:5].upper() + rgb[1:3].upper()
