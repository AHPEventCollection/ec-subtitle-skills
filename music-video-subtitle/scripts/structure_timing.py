"""Complete musical cycles and reviewable, piecewise timing transfer.

The structure table is authored from lyrics and independent audio by the agent.
Beat counts are hypotheses, never an automatic musical-structure verdict.
Only standard-library modules are needed in either standalone Skill.
"""
from __future__ import annotations

import array
import bisect
import csv
import math
import re
import subprocess
import sys
from pathlib import Path

FIELDS = ("cycle", "part", "role", "first", "last", "start", "end", "beats", "complete", "evidence")
EVENT_FIELDS = ("id", "start", "end", "source_text", "translation")


def seconds(value: str | float) -> float:
    pieces = str(value).strip().split(":")
    result = 0.0
    for piece in pieces:
        result = result * 60 + float(piece)
    if not math.isfinite(result) or result < 0:
        raise ValueError("结构时间必须是非负有限值")
    return result


def timestamp(value: float) -> str:
    millis = round(value * 1000)
    minutes, millis = divmod(millis, 60000)
    whole, fraction = divmod(millis, 1000)
    return f"{minutes:02d}:{whole:02d}.{fraction:03d}"


def read_tsv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def write_tsv(path: Path, rows: list[dict], fields: tuple | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def normalized_events(events: list[dict]) -> list[dict]:
    result = []
    for index, event in enumerate(events, 1):
        start, end = float(event["start"]), float(event["end"])
        text = str(event.get("source_text", ""))
        if not math.isfinite(start + end) or start < 0 or end <= start or not text.strip():
            raise ValueError(f"第{index}条字幕的正文或时间无效")
        result.append(dict(id=str(event.get("id", index)), start=round(start, 6), end=round(end, 6),
                           source_text=text, translation=str(event.get("translation", ""))))
    if not result or len({e["id"] for e in result}) != len(result):
        raise ValueError("字幕为空或事件ID重复")
    return result


def load_structure(path: Path, events: list[dict], duration: float) -> list[dict]:
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("媒体时长必须是正有限值")
    if not path.is_file():
        raise ValueError("缺少structure.tsv：主线程须先结合歌词和独立音频识别完整一番/二番，不能以短副歌代替")
    rows = read_tsv(path)
    if not rows or not set(FIELDS).issubset(rows[0]):
        raise ValueError("structure.tsv缺少结构行或必要字段")
    result, used, keys = [], set(), set()
    previous_time, previous_line = -1.0, 0
    for row in rows:
        item = {key: row[key].strip() for key in FIELDS}
        item.update(first=int(item["first"]), last=int(item["last"]),
                    start=seconds(item["start"]), end=seconds(item["end"]), beats=int(item["beats"]))
        key = (item["cycle"], item["part"])
        indices = set(range(item["first"], item["last"] + 1))
        if not item["cycle"] or not item["part"] or key in keys:
            raise ValueError("结构cycle/part必须非空且同轮内唯一")
        if item["role"] not in {"verse", "prechorus", "chorus", "bridge", "other"}:
            raise ValueError("结构role必须为verse/prechorus/chorus/bridge/other")
        if not indices or item["first"] < 1 or item["last"] > len(events) or used & indices:
            raise ValueError("结构歌词范围越界、倒序或重复")
        if any(events[i-1].get("role", "lyric") != "lyric" for i in indices):
            raise ValueError("讲话或即兴唱词必须单独复核，不能列入结构模板的常规歌词范围")
        if item["first"] <= previous_line or item["start"] < previous_time - 0.001:
            raise ValueError("结构必须按实际演唱时间与歌词顺序排列")
        if item["end"] <= item["start"] or item["end"] > duration + 0.001 or item["beats"] <= 0:
            raise ValueError("结构时长、拍数或媒体范围无效")
        if item["complete"] not in {"yes", "no"} or not item["evidence"] or item["evidence"].lower() in {"todo", "pending"}:
            raise ValueError("结构必须记录完整性及实际音频定位证据")
        keys.add(key)
        used.update(indices)
        previous_time, previous_line = item["end"], item["last"]
        result.append(item)
    return result


def choose_cycle(rows: list[dict], requested: str | None = None) -> list[dict]:
    cycles = list(dict.fromkeys(row["cycle"] for row in rows if row["role"] == "verse"))[:2]
    if requested and requested not in cycles:
        raise ValueError("代表段只能选择完整的一番或二番")
    for cycle in ([requested] if requested else cycles):
        parts = [row for row in rows if row["cycle"] == cycle]
        if not parts or parts[0]["role"] != "verse" or parts[-1]["role"] != "chorus":
            continue
        if any(row["complete"] != "yes" for row in parts):
            continue
        if any(right["first"] != left["last"] + 1 for left, right in zip(parts, parts[1:])):
            continue
        return parts
    raise ValueError("尚无完整主歌至副歌的一番/二番：先补全结构定位，不得因短片段识别准确而通过")


def cycle_window(parts: list[dict], duration: float) -> tuple[float, float]:
    # Musical completeness wins over the old 84/180-second limits and short pauses.
    return max(0.0, parts[0]["start"] - 3.0), min(duration, parts[-1]["end"] + 3.0)


def audio_onsets(audio: Path, ffmpeg: Path, offset: float = 0.0) -> list[float]:
    if audio.suffix.lower() not in {".wav", ".flac"} or not audio.is_file():
        raise ValueError("节奏定位必须使用已提取的独立WAV或FLAC")
    command = [str(ffmpeg), "-v", "error", "-nostdin", "-i", str(audio), "-ss", str(offset),
               "-vn", "-ac", "1", "-ar", "8000", "-f", "f32le", "pipe:1"]
    data = array.array("f", subprocess.run(command, capture_output=True, check=True).stdout)
    if sys.byteorder != "little":
        data.byteswap()
    energy = [math.sqrt(sum(x*x for x in data[i:i+80]) / 80) for i in range(0, len(data)-79, 80)]
    return [max(0.0, current - previous) for previous, current in zip([0.0] + energy, energy)]


def beat_anchors(part: dict, onsets: list[float]) -> tuple[list[float], int]:
    """Locate 4/8-beat phrase landmarks near a structural beat-count hypothesis."""
    start, end, beats = part["start"], part["end"], part["beats"]
    anchors, supported = [start], 0
    interval = (end - start) / beats
    for beat in range(4, beats, 4):
        expected = start + interval * beat
        radius = min(0.25, interval * 0.35)
        first = max(0, math.ceil((expected - radius) * 100))
        last = min(len(onsets), math.floor((expected + radius) * 100) + 1)
        window = onsets[first:last]
        found = expected
        if window and max(window) > max(1e-6, 2.5 * sum(window) / len(window)):
            found = (first + max(range(len(window)), key=window.__getitem__)) / 100
            supported += 1
        anchors.append(found)
    anchors.append(end)
    return anchors, supported


def rhythm_hypotheses(onsets: list[float], start: float, end: float) -> list[dict]:
    """Autocorrelation hypotheses, retaining half/double-tempo ambiguity for review."""
    # Retain the 10-ms envelope: 50-ms bins can erase a ~97-BPM peak while
    # retaining its half/double-tempo aliases on real band recordings.
    values = onsets[round(start*100):round(end*100)]
    if len(values) < 320 or max(values, default=0) <= 1e-6:
        return []
    # Smooth transient jitter without decimating or changing the 10-ms grid.
    values = [sum(values[max(0, i-1):i+2])/3 for i in range(len(values))]
    mean = sum(values) / len(values)
    values = [x-mean for x in values]
    scores = []
    for lag in range(30, min(151, len(values)//3)):
        left, right = values[:-lag], values[lag:]
        norm = math.sqrt(sum(x*x for x in left) * sum(x*x for x in right))
        scores.append((sum(a*b for a,b in zip(left,right))/norm if norm else 0.0, lag))
    candidates = []
    for index, (score, lag) in enumerate(scores):
        if score < 0.2:
            continue
        if index and score < scores[index-1][0]:
            continue
        if index+1 < len(scores) and score < scores[index+1][0]:
            continue
        period = lag * 0.01
        count = (end-start)/period
        candidates.append(dict(bpm=round(60/period, 3), estimated_beats=round(count, 3),
            groups_of_4=round(count/4, 3), groups_of_8=round(count/8, 3), correlation=round(score, 6)))
    return sorted(candidates, key=lambda row: row["correlation"], reverse=True)[:5]


def write_rhythm_hints(plan: Path, onsets: list[float], output: Path) -> Path:
    rows = read_tsv(plan)
    if not rows:
        raise ValueError("主线程先填写各部分的大致起止时间，再用节奏证据核对拍数与4拍/8拍分组")
    hints, lines = [], ["# 结构节奏线索", "", "候选保留半速/倍速歧义，不自动填写拍数或确认结构完整", ""]
    for row in rows:
        start, end = seconds(row["start"]), seconds(row["end"])
        if end <= start or end > len(onsets)/100 + 0.02:
            raise ValueError("节奏查询窗口超出独立音频")
        candidates = rhythm_hypotheses(onsets, start, end)
        lines.append(f"- {row['cycle']}/{row['part']}：{timestamp(start)}至{timestamp(end)}")
        if not candidates:
            lines.append("  节奏证据不足，继续回听定位，不推定固定拍数")
        for candidate in candidates:
            hints.append(dict(cycle=row["cycle"], part=row["part"], **candidate))
            lines.append(f"  候选{candidate['bpm']}BPM，约{candidate['estimated_beats']}拍，"
                         f"4拍组约{candidate['groups_of_4']}组，8拍组约{candidate['groups_of_8']}组")
    output.mkdir(parents=True, exist_ok=True)
    write_tsv(output / "rhythm-hints.tsv", hints,
              ("cycle", "part", "bpm", "estimated_beats", "groups_of_4", "groups_of_8", "correlation"))
    report = output / "rhythm-hints.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def interpolate(value: float, source: list[float], target: list[float]) -> float:
    if len(source) != len(target) or len(source) < 2:
        raise ValueError("结构锚点不能对应")
    if any(b <= a for a, b in zip(source, source[1:])) or any(b <= a for a, b in zip(target, target[1:])):
        raise ValueError("音频锚点必须严格递增")
    if value < source[0] - 0.001 or value > source[-1] + 0.001:
        raise ValueError("参考字幕越出已确认的结构边界，禁止外推")
    index = min(len(source) - 2, max(0, bisect.bisect_right(source, value) - 1))
    ratio = (value - source[index]) / (source[index+1] - source[index])
    return target[index] + ratio * (target[index+1] - target[index])


def transfer(events: list[dict], rows: list[dict], selected: list[dict], reviewed: list[dict],
             onsets: list[float]) -> tuple[list[dict], list[dict]]:
    base = normalized_events(events)
    first, last = selected[0]["first"], selected[-1]["last"]
    expected = base[first-1:last]
    if len(expected) != len(reviewed) or any((a["source_text"], a["translation"]) !=
            (b["source_text"], b["translation"]) for a, b in zip(expected, reviewed)):
        raise ValueError("代表段回灌必须保持正文、翻译、顺序和事件数，只调整时间")
    reviewed = normalized_events(reviewed)
    if any(b["start"] < a["end"]-0.001 for a,b in zip(reviewed,reviewed[1:])):
        raise ValueError("代表段自身有重叠，先校正参考时间，禁止传播")
    for part in selected:
        for event in reviewed[part["first"]-first:part["last"]-first+1]:
            if event["start"] < part["start"]-0.001 or event["end"] > part["end"]+0.001:
                raise ValueError("校准字幕越出对应结构边界，先核实结构定位，禁止外推")
    result = [dict(event) for event in base]
    usage = [dict(line=i, reference="", part="", status="local_review", reason="outside_reference_structure")
             for i in range(1, len(base)+1)]
    for index, event in enumerate(reviewed, first-1):
        result[index].update(start=float(event["start"]), end=float(event["end"]))
        usage[index].update(reference=index+1, status="reference", reason="representative_timing_input")
    by_part = {part["part"]: part for part in selected}
    selected_cycle = selected[0]["cycle"]
    for part in rows:
        if part["cycle"] == selected_cycle:
            continue
        indices = range(part["first"]-1, part["last"])
        ref = by_part.get(part["part"])
        reason = ""
        if ref is None or ref["role"] != part["role"]:
            reason = "structure_has_no_reference_part"
        elif part["complete"] != "yes" or part["beats"] != ref["beats"]:
            reason = "structure_or_beat_count_changed"
        elif part["last"] - part["first"] != ref["last"] - ref["first"]:
            reason = "different_segmentation_use_reference_part_for_local_review"
        for index in indices:
            usage[index].update(part=part["part"], reference=(f'{ref["first"]}-{ref["last"]}' if ref else ""))
            if reason:
                usage[index]["reason"] = reason
        if reason:
            continue
        source_anchors, source_support = beat_anchors(ref, onsets)
        target_anchors, target_support = beat_anchors(part, onsets)
        for source_index, target_index in zip(range(ref["first"]-1, ref["last"]), indices):
            source_event = result[source_index]
            # Keep within-line phrasing offsets; do not snap subtitle boundaries to beats.
            start = interpolate(source_event["start"], source_anchors, target_anchors)
            end = interpolate(source_event["end"], source_anchors, target_anchors)
            result[target_index].update(start=round(start, 6), end=round(end, 6))
            usage[target_index].update(reference=source_index+1, status="mapped", reason=(
                "audio_phrase_anchors_review_local_melody" if min(source_support, target_support) > 0
                else "structural_boundary_ratio_review_beat_hypothesis"))
    # Preserve the proposed timing and reference trace for local correction.
    # Both adapters reject remaining overlaps before any formal application.
    for index, event in enumerate(result):
        if event["end"] <= event["start"]:
            raise ValueError(f"结构推广在第{index+1}行产生非正时长，须先调整对应结构/断句")
        if index and event["start"] < result[index-1]["end"] - 0.001:
            for affected, reason in [(index-1, f"overlaps_next_line_{index+1}"),
                                     (index, f"overlaps_previous_line_{index}")]:
                if usage[affected]["status"] != "reference":
                    usage[affected]["status"] = "local_review"
                usage[affected]["reason"] += ";" + reason
    return result, usage


def write_srt(path: Path, events: list[dict]) -> None:
    def clock(value: float) -> str:
        millis = round(value * 1000)
        hours, millis = divmod(millis, 3600000)
        minutes, millis = divmod(millis, 60000)
        sec, millis = divmod(millis, 1000)
        return f"{hours:02d}:{minutes:02d}:{sec:02d},{millis:03d}"
    blocks = []
    for index, event in enumerate(events, 1):
        lines = [str(event.get("translation", "")), event["source_text"]]
        text = "\n".join(line for line in lines if line)
        blocks.append(f'{index}\n{clock(event["start"])} --> {clock(event["end"])}\n{text}')
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8-sig")


def read_srt_against(path: Path, expected: list[dict], origin: float = 0.0) -> list[dict]:
    blocks = re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").strip())
    if len(blocks) != len(expected):
        raise ValueError("回灌字幕事件数变化，须单独处理断句，不能直接传播")
    result = []
    for block, event in zip(blocks, expected):
        lines = block.splitlines()
        if len(lines) < 3 or " --> " not in lines[1]:
            raise ValueError("回灌字幕不是有效SRT")
        text = "\n".join(value for value in (event["translation"], event["source_text"]) if value)
        if "\n".join(lines[2:]) != text:
            raise ValueError("回灌字幕正文、翻译或顺序已变化，拒绝用作只改时间的参考")
        a, b = lines[1].replace(",", ".").split(" --> ")
        result.append({**event, "start": seconds(a) + origin, "end": seconds(b) + origin})
    return normalized_events(result)


def _plan_rows(rows: list[dict]) -> list[dict]:
    return [{key: str(row[key]) for key in FIELDS} for row in rows]


def current_pack(events: list[dict], rows: list[dict], output: Path) -> dict:
    if normalized_events(read_tsv(output / "source-events.tsv")) != normalized_events(events):
        raise ValueError("初稿字幕已变化，旧代表段和推广结果失效，请在新的审核目录重建")
    if read_tsv(output / "structure-snapshot.tsv") != _plan_rows(rows):
        raise ValueError("结构定位已变化，旧代表段和推广结果失效，请在新的审核目录重建")
    values = {row["key"]: row["value"] for row in read_tsv(output / "selection.tsv")}
    choose_cycle(rows, values["cycle"])
    return values


def prepare_review(events: list[dict], plan: Path, duration: float, output: Path,
                   *, cycle: str | None = None, zero_origin: bool = False) -> dict:
    rows = load_structure(plan, events, duration)
    events = normalized_events(events)
    selected = choose_cycle(rows, cycle)
    start, end = cycle_window(selected, duration)
    origin = start if zero_origin else 0.0
    expected = events[selected[0]["first"]-1:selected[-1]["last"]]
    if any(e["start"] < start - 0.001 or e["end"] > end + 0.001 for e in expected):
        raise ValueError("代表段初稿越出完整结构样片，先在该结构内修正初稿定位再生成，禁止裁掉歌词")
    output.mkdir(parents=True, exist_ok=True)
    reference = output / "reference.srt"
    if reference.exists():
        previous = current_pack(events, rows, output)
        if previous["cycle"] != selected[0]["cycle"] or float(previous["origin"]) != origin:
            raise ValueError("已有可编辑代表段，改变选段必须使用新的审核目录")
    else:
        write_tsv(output / "source-events.tsv", events, EVENT_FIELDS)
        write_tsv(output / "structure-snapshot.tsv", _plan_rows(rows), FIELDS)
        write_tsv(output / "selection.tsv", [dict(key=k, value=v) for k, v in
                  dict(cycle=selected[0]["cycle"], origin=origin, start=start, end=end).items()], ("key", "value"))
        write_srt(reference, [{**e, "start": e["start"]-origin, "end": e["end"]-origin} for e in expected])
    report = output / "review.md"
    report.write_text("\n".join([
        "# 完整代表段校准", "", f"- 代表轮次：{selected[0]['cycle']}",
        f"- 完整窗口：{timestamp(start)}至{timestamp(end)}",
        f"- 歌词范围：第{selected[0]['first']}至{selected[-1]['last']}行",
        "- 结构：" + "→".join(p["part"] for p in selected),
        "- 编辑reference.srt校准整轮时间，保留正文、翻译和顺序",
        "- 校准后必须生成整曲结构推广候选，并检查usage.tsv中的局部差异",
        "- 4拍/8拍仅用于寻找音频乐句锚点，不把每条字幕吸附到节拍", ""
    ]), encoding="utf-8")
    return dict(start=start, end=end, origin=origin, cycle=selected[0]["cycle"], reference=reference, report=report)


def proposed_review(events: list[dict], plan: Path, duration: float, output: Path,
                    onsets: list[float], *, write: bool = True) -> tuple[list[dict], list[dict]]:
    rows = load_structure(plan, events, duration)
    events = normalized_events(events)
    values = current_pack(events, rows, output)
    selected = choose_cycle(rows, values["cycle"])
    expected = events[selected[0]["first"]-1:selected[-1]["last"]]
    reviewed = read_srt_against(output / "reference.srt", expected, float(values["origin"]))
    result, usage = transfer(events, rows, selected, reviewed, onsets)
    if write:
        existing_candidate = output / "structure-candidate.srt"
        if existing_candidate.exists():
            old_result = normalized_events(read_tsv(output / "candidate-events.tsv"))
            edited = read_srt_against(existing_candidate, old_result)
            if any(abs(a[k]-b[k]) > 0.001 for a, b in zip(edited, old_result) for k in ("start", "end")):
                raise ValueError("结构候选已有人工作时间修订，拒绝覆盖；重新推广须使用新的审核目录")
        write_srt(output / "structure-candidate.srt", result)
        write_tsv(output / "usage.tsv", usage, ("line", "reference", "part", "status", "reason"))
        write_tsv(output / "candidate-events.tsv", result, EVENT_FIELDS)
        counts = {status: sum(row["status"] == status for row in usage) for status in ("reference", "mapped", "local_review")}
        conflicts = [f"- 第{i}与{i+1}行重叠{timestamp(a['end']-b['start'])}，须在整曲候选中局部修订"
                     for i,(a,b) in enumerate(zip(result,result[1:]),1) if b["start"] < a["end"]-0.001]
        (output / "transfer.md").write_text("\n".join([
            "# 整曲结构推广候选", "", f"- 代表段：{counts['reference']}行",
            f"- 已参考代表段映射：{counts['mapped']}行", f"- 需局部校正：{counts['local_review']}行",
            "- 每一行的参考位置与差异原因见usage.tsv，未省略未匹配部分",
            "- 已映射结果仍须检查不同歌词的发声和旋律细节",
            "- 在structure-candidate.srt中校正差异，明确确认整曲后才可进入正式字幕", "",
            *conflicts, ""
        ]), encoding="utf-8")
    else:
        if normalized_events(read_tsv(output / "candidate-events.tsv")) != normalized_events(result):
            raise ValueError("代表段、结构或音频参考已变化，请重新生成推广候选")
        expected_usage = [{k: str(v) for k, v in row.items()} for row in usage]
        if read_tsv(output / "usage.tsv") != expected_usage:
            raise ValueError("逐行结构参考记录已变化或缺失，请重新生成推广候选")
    return result, usage
