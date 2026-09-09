from __future__ import annotations

import argparse
import csv
import hashlib
import re
import subprocess
import unicodedata
from pathlib import Path

from common import (
    ensure_workspace,
    find_source_video,
    probe_media,
    resolve_binary,
    write_text,
)
from lyrics_source import is_metadata, parse_lrc
from subtitle_io import Cue, load_cues, render_srt, validate_cues


VALID_MODES = {"whisper", "sofa", "combined", "compare"}
SOFA_MODES = {"sofa", "combined", "compare"}
ROLE_VALUES = {"song", "speech", "unknown"}
TIME_FIELD_NAMES = {"time", "start", "end", "timestamp", "lrc_start", "lrc_end"}
SUSPICIOUS_TEXT = re.compile(r"[A-Za-z0-9]")
RUBY_LIKE_TEXT = re.compile(r"[一-龥々〆ヵヶ]+[ /／]+[ァ-ヶー]+")
SOFA_SILENCE_LABELS = {"AP", "SP"}
COMMON_READINGS_FILE = (
    Path(__file__).resolve().parents[1] / "references" / "common-readings.tsv"
)


def _load_common_readings() -> dict[str, str]:
    with COMMON_READINGS_FILE.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    exact = {
        row["surface"].strip(): row["reading"].strip()
        for row in rows
        if row.get("rule", "").strip() == "exact"
    }
    if not exact:
        raise ValueError(f"常用读音覆盖文件为空：{COMMON_READINGS_FILE}")
    return exact


COMMON_READING_REPLACEMENTS = _load_common_readings()
PRONOUN_KIMI = re.compile(
    r"(^|[\s、。！？はがをにともへで])君(?=(?:自身|たち|達|ら)?(?:は|が|を|に|の|も|と|へ|で|$))"
)

KANA = {
    "あ": ("a",),
    "い": ("i",),
    "う": ("u",),
    "え": ("e",),
    "お": ("o",),
    "か": ("k", "a"),
    "き": ("k", "i"),
    "く": ("k", "u"),
    "け": ("k", "e"),
    "こ": ("k", "o"),
    "さ": ("s", "a"),
    "し": ("sh", "i"),
    "す": ("s", "u"),
    "せ": ("s", "e"),
    "そ": ("s", "o"),
    "た": ("t", "a"),
    "ち": ("ch", "i"),
    "つ": ("ts", "u"),
    "て": ("t", "e"),
    "と": ("t", "o"),
    "な": ("n", "a"),
    "に": ("n", "i"),
    "ぬ": ("n", "u"),
    "ね": ("n", "e"),
    "の": ("n", "o"),
    "は": ("h", "a"),
    "ひ": ("h", "i"),
    "ふ": ("f", "u"),
    "へ": ("h", "e"),
    "ほ": ("h", "o"),
    "ま": ("m", "a"),
    "み": ("m", "i"),
    "む": ("m", "u"),
    "め": ("m", "e"),
    "も": ("m", "o"),
    "や": ("y", "a"),
    "ゆ": ("y", "u"),
    "よ": ("y", "o"),
    "ら": ("r", "a"),
    "り": ("r", "i"),
    "る": ("r", "u"),
    "れ": ("r", "e"),
    "ろ": ("r", "o"),
    "わ": ("w", "a"),
    "ゐ": ("i",),
    "ゑ": ("e",),
    "を": ("o",),
    "が": ("g", "a"),
    "ぎ": ("g", "i"),
    "ぐ": ("g", "u"),
    "げ": ("g", "e"),
    "ご": ("g", "o"),
    "ざ": ("z", "a"),
    "じ": ("j", "i"),
    "ず": ("z", "u"),
    "ぜ": ("z", "e"),
    "ぞ": ("z", "o"),
    "だ": ("d", "a"),
    "ぢ": ("j", "i"),
    "づ": ("z", "u"),
    "で": ("d", "e"),
    "ど": ("d", "o"),
    "ば": ("b", "a"),
    "び": ("b", "i"),
    "ぶ": ("b", "u"),
    "べ": ("b", "e"),
    "ぼ": ("b", "o"),
    "ぱ": ("p", "a"),
    "ぴ": ("p", "i"),
    "ぷ": ("p", "u"),
    "ぺ": ("p", "e"),
    "ぽ": ("p", "o"),
    "ゔ": ("v", "u"),
    "ん": ("N",),
    "っ": ("cl",),
    "ぁ": ("a",),
    "ぃ": ("i",),
    "ぅ": ("u",),
    "ぇ": ("e",),
    "ぉ": ("o",),
}

YOON = {
    "きゃ": ("ky", "a"),
    "きゅ": ("ky", "u"),
    "きょ": ("ky", "o"),
    "しゃ": ("sh", "a"),
    "しゅ": ("sh", "u"),
    "しょ": ("sh", "o"),
    "ちゃ": ("ch", "a"),
    "ちゅ": ("ch", "u"),
    "ちょ": ("ch", "o"),
    "にゃ": ("ny", "a"),
    "にゅ": ("ny", "u"),
    "にょ": ("ny", "o"),
    "ひゃ": ("hy", "a"),
    "ひゅ": ("hy", "u"),
    "ひょ": ("hy", "o"),
    "みゃ": ("my", "a"),
    "みゅ": ("my", "u"),
    "みょ": ("my", "o"),
    "りゃ": ("ry", "a"),
    "りゅ": ("ry", "u"),
    "りょ": ("ry", "o"),
    "ぎゃ": ("gy", "a"),
    "ぎゅ": ("gy", "u"),
    "ぎょ": ("gy", "o"),
    "じゃ": ("j", "a"),
    "じゅ": ("j", "u"),
    "じょ": ("j", "o"),
    "びゃ": ("by", "a"),
    "びゅ": ("by", "u"),
    "びょ": ("by", "o"),
    "ぴゃ": ("py", "a"),
    "ぴゅ": ("py", "u"),
    "ぴょ": ("py", "o"),
    "ふぁ": ("f", "a"),
    "ふぃ": ("f", "i"),
    "ふぇ": ("f", "e"),
    "ふぉ": ("f", "o"),
    "てぃ": ("ty", "i"),
    "でぃ": ("dy", "i"),
    "つぁ": ("ts", "a"),
    "つぃ": ("ts", "i"),
    "つぇ": ("ts", "e"),
    "つぉ": ("ts", "o"),
    "うぃ": ("w", "i"),
    "うぇ": ("w", "e"),
    "うぉ": ("w", "o"),
    "ゔぁ": ("v", "a"),
    "ゔぃ": ("v", "i"),
    "ゔぇ": ("v", "e"),
    "ゔぉ": ("v", "o"),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _lyric_text(path: Path) -> list[str]:
    return [
        line.text.strip()
        for line in parse_lrc(path.read_text(encoding="utf-8-sig"))
        if line.text.strip() and not is_metadata(line.text)
    ]


def _roles(path: Path, count: int) -> list[str]:
    roles = ["song"] * count
    if not path.is_file():
        return roles
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    for row in rows:
        line = int(row["line"])
        role = row["role"].strip().casefold()
        if not 1 <= line <= count:
            raise ValueError(f"roles.tsv行号越界：{line}")
        if role not in ROLE_VALUES:
            raise ValueError(f"roles.tsv角色非法：{role}")
        roles[line - 1] = role
    return roles


def _write_text_if_changed(
    path: Path, content: str, *, encoding: str = "utf-8"
) -> None:
    if path.is_file() and path.read_text(encoding=encoding) == content:
        return
    path.write_text(content, encoding=encoding, newline="\n")


def _reading_source(text: str) -> str:
    source = text
    for surface in sorted(COMMON_READING_REPLACEMENTS, key=len, reverse=True):
        source = source.replace(surface, COMMON_READING_REPLACEMENTS[surface])
    return PRONOUN_KIMI.sub(lambda match: match.group(1) + "きみ", source)


def _common_reading_hits(text: str) -> list[str]:
    hits: list[str] = []
    for surface in sorted(COMMON_READING_REPLACEMENTS, key=len, reverse=True):
        if surface in text and not any(surface in selected for selected in hits):
            hits.append(surface)
    if PRONOUN_KIMI.search(text):
        hits.append("君（代名词上下文）")
    return hits


def _pronunciation_overrides(
    path: Path, lyrics: list[str]
) -> dict[int, dict[str, str]]:
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    overrides: dict[int, dict[str, str]] = {}
    for row in rows:
        try:
            line = int(row.get("line", ""))
        except (TypeError, ValueError) as error:
            raise ValueError("pronunciation-overrides.tsv存在非法line") from error
        if not 1 <= line <= len(lyrics):
            raise ValueError(f"pronunciation-overrides.tsv行号越界：{line}")
        japanese = (row.get("japanese") or "").strip()
        if japanese != lyrics[line - 1]:
            raise ValueError(
                f"pronunciation-overrides.tsv第{line}行正文与当前歌词不一致"
            )
        reading = (row.get("reading") or row.get("confirmed_reading") or "").strip()
        if not reading:
            raise ValueError(f"pronunciation-overrides.tsv第{line}行缺少reading")
        if line in overrides:
            raise ValueError(f"pronunciation-overrides.tsv第{line}行重复")
        overrides[line] = {
            "japanese": japanese,
            "reading": reading,
            "reason": (
                row.get("reason") or row.get("evidence") or "逐曲稀疏覆盖"
            ).strip(),
        }
    return overrides


def _reading(text: str) -> str:
    try:
        from pykakasi import kakasi
    except ImportError as error:
        raise RuntimeError("缺少pykakasi，无法生成SOFA读音和音素") from error
    return "".join(token["hira"] for token in kakasi().convert(_reading_source(text)))


def _phones(reading: str) -> list[str]:
    phones: list[str] = []
    index = 0
    while index < len(reading):
        pair = reading[index : index + 2]
        char = reading[index]
        if pair in YOON:
            phones.extend(YOON[pair])
            index += 2
            continue
        if char == "ー":
            vowel = next(
                (
                    item
                    for item in reversed(phones)
                    if item in {"a", "i", "u", "e", "o"}
                ),
                None,
            )
            if vowel is None:
                raise ValueError(f"长音符前没有元音：{reading}")
            phones.append(vowel)
            index += 1
            continue
        if char in KANA:
            phones.extend(KANA[char])
            index += 1
            continue
        if char.isspace() or unicodedata.category(char).startswith(("P", "S")):
            index += 1
            continue
        raise ValueError(f"SOFA读音含未支持字符{char!r}：{reading}")
    return phones


def prepare_alignment(mv_dir: Path) -> Path:
    mv_dir = ensure_workspace(mv_dir)
    japanese_path = mv_dir / "lyrics" / "original.lrc"
    chinese_path = mv_dir / "lyrics" / "chinese.lrc"
    if not japanese_path.is_file() or not chinese_path.is_file():
        raise FileNotFoundError("缺少lyrics/original.lrc或lyrics/chinese.lrc")
    japanese = _lyric_text(japanese_path)
    chinese = _lyric_text(chinese_path)
    if not japanese or len(japanese) != len(chinese):
        raise ValueError(
            f"日中歌词行数必须相同且非空：日文{len(japanese)}行，中文{len(chinese)}行"
        )
    roles = _roles(mv_dir / "lyrics" / "roles.tsv", len(japanese))
    overrides = _pronunciation_overrides(
        mv_dir / "lyrics" / "pronunciation-overrides.tsv", japanese
    )
    rows: list[dict[str, str]] = []
    all_song_phones: list[str] = []
    suspicious: list[tuple[int, str, str]] = []
    applied: list[dict[str, str]] = []
    for line_number, (ja, zh, role) in enumerate(
        zip(japanese, chinese, roles), start=1
    ):
        reading = ""
        phones: list[str] = []
        if role == "song":
            common_hits = _common_reading_hits(ja)
            override = overrides.get(line_number)
            reading = override["reading"] if override else _reading(ja)
            phones = _phones(reading)
            if not phones:
                raise ValueError(f"第{line_number}行没有可用SOFA音素")
            if all_song_phones:
                all_song_phones.append("SP")
            all_song_phones.extend(phones)
            if override:
                applied.append(
                    {
                        "line": str(line_number),
                        "japanese": ja,
                        "source": "lyrics/pronunciation-overrides.tsv",
                        "reading": reading,
                        "reason": override["reason"],
                    }
                )
            elif common_hits:
                applied.append(
                    {
                        "line": str(line_number),
                        "japanese": ja,
                        "source": "common-readings",
                        "reading": reading,
                        "reason": "、".join(common_hits),
                    }
                )
        if line_number not in overrides:
            residual = ja
            for surface in _common_reading_hits(ja):
                if surface in COMMON_READING_REPLACEMENTS:
                    residual = residual.replace(surface, "")
            reasons: list[str] = []
            if SUSPICIOUS_TEXT.search(residual):
                reasons.append("含未覆盖英文或数字")
            if RUBY_LIKE_TEXT.search(ja):
                reasons.append("疑似汉字与片假名读音并记")
            if reasons:
                suspicious.append((line_number, ja, "、".join(reasons)))
        rows.append(
            {
                "line": str(line_number),
                "role": role,
                "japanese": ja,
                "chinese": zh,
                "reading": reading,
                "phones": " ".join(phones),
            }
        )

    review_dir = mv_dir / "review" / "alignment"
    review_dir.mkdir(parents=True, exist_ok=True)
    text_only = review_dir / "text-only.tsv"
    with text_only.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    _write_text_if_changed(review_dir / "song.lab", " ".join(all_song_phones) + "\n")
    pronunciation = review_dir / "pronunciation-review.tsv"
    with pronunciation.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(("line", "japanese", "reason"))
        writer.writerows(suspicious)
    with (review_dir / "pronunciation-applied.tsv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("line", "japanese", "source", "reading", "reason"),
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerows(applied)

    master = mv_dir / "subtitle" / "master.srt"
    guard_value = _sha256(master) if master.is_file() else "ABSENT"
    write_text(review_dir / "master-guard.sha256", guard_value + "\n")
    fields = set(rows[0])
    forbidden_absent = fields.isdisjoint(TIME_FIELD_NAMES)
    audit = [
        "# 无外部时间轴输入审计",
        "",
        "- 合同　LRC只提取正文顺序，时间标签不进入Whisper、SOFA或组合逻辑",
        f"- 歌词行数　{len(rows)}",
        f"- 演唱行数　{sum(row['role'] == 'song' for row in rows)}",
        f"- 口语行数　{sum(row['role'] == 'speech' for row in rows)}",
        f"- 待读音人工确认　{len(suspicious)}",
        f"- 已应用常用词或稀疏覆盖　{len(applied)}",
        f"- 输出字段　{','.join(rows[0])}",
        f"- forbidden_fields_absent　{str(forbidden_absent).lower()}",
        "- 候选目录　review/alignment/candidates",
        "- 人工主字幕　subtitle/master.srt只读保护",
        "",
    ]
    audit_path = review_dir / "input-audit.md"
    write_text(audit_path, "\n".join(audit))
    if not forbidden_absent:
        raise ValueError("无时间字段审计失败")
    return audit_path


def build_sofa_candidate(mv_dir: Path, htk_path: Path | None = None) -> Path:
    mv_dir = ensure_workspace(mv_dir)
    review_dir = mv_dir / "review" / "alignment"
    text_only = review_dir / "text-only.tsv"
    if not text_only.is_file():
        raise FileNotFoundError("尚未运行alignment-prepare")
    htk_path = htk_path or (
        mv_dir / "work" / "sofa-input" / "htk" / "words" / "vocals.lab"
    )
    if not htk_path.is_file():
        raise FileNotFoundError(f"缺少SOFA HTK输出：{htk_path}")

    with text_only.open("r", encoding="utf-8-sig", newline="") as handle:
        lyric_rows = list(csv.DictReader(handle, delimiter="\t"))
    aligned: list[tuple[float, float, str]] = []
    silence_counts = {label: 0 for label in sorted(SOFA_SILENCE_LABELS)}
    for line_number, raw in enumerate(
        htk_path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        parts = raw.split()
        if len(parts) != 3:
            raise ValueError(f"SOFA HTK第{line_number}行格式非法")
        start, end, phone = parts
        if phone in SOFA_SILENCE_LABELS:
            silence_counts[phone] += 1
            continue
        aligned.append((int(start) / 10_000_000, int(end) / 10_000_000, phone))

    expected_phones = [phone for row in lyric_rows for phone in row["phones"].split()]
    actual_phones = [phone for _start, _end, phone in aligned]
    if actual_phones != expected_phones:
        mismatch = next(
            (
                index
                for index, pair in enumerate(
                    zip(actual_phones, expected_phones), start=1
                )
                if pair[0] != pair[1]
            ),
            None,
        )
        raise ValueError(
            "SOFA音素与无时间输入不一致 "
            f"actual={len(actual_phones)} expected={len(expected_phones)} "
            f"first_mismatch={mismatch}"
        )

    cues: list[Cue] = []
    reports: list[dict[str, str]] = []
    cursor = 0
    for row in lyric_rows:
        if row["role"] != "song":
            raise ValueError("SOFA-only候选不能静默处理speech或unknown行")
        line_phones = row["phones"].split()
        group = aligned[cursor : cursor + len(line_phones)]
        cursor += len(line_phones)
        if len(group) != len(line_phones):
            raise ValueError(f"第{row['line']}行SOFA音素数量不足")
        start = group[0][0]
        end = group[-1][1]
        duration = end - start
        flags: list[str] = []
        if duration > 12:
            flags.append("duration_gt_12s")
        if duration < 0.3:
            flags.append("duration_lt_0.3s")
        cues.append(Cue(start, end, row["chinese"], row["japanese"]))
        reports.append(
            {
                "line": row["line"],
                "start": f"{start:.3f}",
                "end": f"{end:.3f}",
                "duration": f"{duration:.3f}",
                "japanese": row["japanese"],
                "chinese": row["chinese"],
                "review_flags": ",".join(flags),
            }
        )
    validate_cues(cues)

    candidate_dir = review_dir / "candidates"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    candidate = candidate_dir / "sofa-only.srt"
    _write_text_if_changed(candidate, render_srt(cues), encoding="utf-8-sig")
    reports_path = candidate_dir / "sofa-line-alignment.tsv"
    with reports_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(reports[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(reports)
    write_text(
        candidate_dir / "sofa-conversion.md",
        "\n".join(
            [
                "# SOFA候选转换",
                "",
                f"- 正文音素　{len(actual_phones)}",
                f"- AP静音　{silence_counts['AP']}",
                f"- SP静音　{silence_counts['SP']}",
                "- AP与SP　仅保留审计，不计入歌词正文音素",
                "- 正文音素顺序　逐项完全一致",
                "",
            ]
        ),
    )
    return candidate


def _probe_duration(source: Path, ffprobe_value: str | None = None) -> float:
    ffprobe = resolve_binary("ffprobe", ffprobe_value)
    completed = subprocess.run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        return float(completed.stdout.strip())
    except ValueError as error:
        raise ValueError(f"无法读取媒体时长：{source}") from error


def preflight_alignment(
    mv_dir: Path, mode: str = "sofa", ffprobe_value: str | None = None
) -> Path:
    mode = mode.casefold()
    if mode not in VALID_MODES:
        raise ValueError(f"对齐模式非法：{mode}")
    mv_dir = ensure_workspace(mv_dir)
    review_dir = mv_dir / "review" / "alignment"
    text_only = review_dir / "text-only.tsv"
    audit = review_dir / "input-audit.md"
    if not text_only.is_file() or not audit.is_file():
        raise FileNotFoundError("尚未运行alignment-prepare")
    if "forbidden_fields_absent　true" not in audit.read_text(encoding="utf-8-sig"):
        raise ValueError("无时间字段审计未通过")

    evidence = [
        "# 机器对齐前置检查",
        "",
        f"- 模式　{mode}",
        "- 外部歌词时间轴　未进入模型输入",
        "- 候选与人工主字幕　隔离",
    ]
    if mode in SOFA_MODES:
        source = find_source_video(mv_dir)
        vocals = mv_dir / "work" / "vocals.wav"
        if not vocals.is_file():
            raise FileNotFoundError(
                "SOFA或组合模式要求work/vocals.wav，人声分离失败时禁止退回原始混音"
            )
        if vocals.resolve() == source.resolve():
            raise ValueError("分离人声不能与源视频指向同一文件")
        source_info = probe_media(source, ffprobe_value)
        vocal_duration = _probe_duration(vocals, ffprobe_value)
        difference = abs(source_info.duration - vocal_duration)
        if difference > 0.25:
            raise ValueError(f"分离人声与源片时长差{difference:.3f}秒，拒绝继续")
        evidence.extend(
            [
                "- SOFA音频　work/vocals.wav",
                f"- 分离人声时长　{vocal_duration:.3f}秒",
                f"- 与源片时长差　{difference:.3f}秒",
                "- 原始混音降级　禁止",
            ]
        )
    if mode == "compare":
        evidence.extend(
            [
                "- Whisper候选　必须独立生成",
                "- SOFA候选　必须独立生成",
                "- Combined候选　只作实验诊断，不得冒充人工终稿",
            ]
        )
    report = review_dir / f"preflight-{mode}.md"
    write_text(report, "\n".join(evidence) + "\n")
    return report


def _expected_candidates(mode: str) -> tuple[str, ...]:
    if mode == "whisper":
        return ("whisper-only.srt",)
    if mode == "sofa":
        return ("sofa-only.srt",)
    return ("whisper-only.srt", "sofa-only.srt", "combined.srt")


def validate_alignment_candidates(mv_dir: Path, mode: str = "sofa") -> Path:
    mode = mode.casefold()
    if mode not in VALID_MODES:
        raise ValueError(f"对齐模式非法：{mode}")
    mv_dir = ensure_workspace(mv_dir)
    review_dir = mv_dir / "review" / "alignment"
    preflight = review_dir / f"preflight-{mode}.md"
    if not preflight.is_file():
        raise FileNotFoundError(f"尚未通过{mode}模式前置检查")
    candidates = review_dir / "candidates"
    expected = _expected_candidates(mode)
    cue_counts: dict[str, int] = {}
    for name in expected:
        path = candidates / name
        if not path.is_file():
            raise FileNotFoundError(f"缺少独立候选：{path}")
        cue_counts[name] = len(load_cues(path))
    if mode == "compare":
        comparison = candidates / "three-way-comparison.tsv"
        if not comparison.is_file():
            raise FileNotFoundError("三路对比缺少three-way-comparison.tsv")
        header = comparison.read_text(encoding="utf-8-sig").splitlines()[0].split("\t")
        required = {"whisper_start", "sofa_start", "combined_start", "review_flags"}
        if not required.issubset(header):
            raise ValueError("三路对比表缺少模式独立字段或人工复核标记")

    pronunciation = review_dir / "pronunciation-review.tsv"
    if pronunciation.is_file():
        with pronunciation.open("r", encoding="utf-8-sig", newline="") as handle:
            if any(True for _row in csv.DictReader(handle, delimiter="\t")):
                raise ValueError("pronunciation-review.tsv仍有未确认读音，拒绝继续")

    guard = review_dir / "master-guard.sha256"
    if not guard.is_file():
        raise FileNotFoundError("缺少人工主字幕保护基线")
    expected_guard = guard.read_text(encoding="utf-8-sig").strip()
    master = mv_dir / "subtitle" / "master.srt"
    if expected_guard != "ABSENT":
        if not master.is_file() or _sha256(master) != expected_guard:
            raise ValueError("subtitle/master.srt在机器候选阶段发生变化，拒绝继续")

    lines = [
        "# 机器候选结构检查",
        "",
        f"- 模式　{mode}",
        "- 候选目录　review/alignment/candidates",
        "- 人工主字幕保护　通过",
        "- 重叠、负时长与空事件　通过",
    ]
    lines.extend(f"- {name}　{cue_counts[name]}条" for name in expected)
    if mode in {"combined", "compare"}:
        lines.append("- Combined定位　仅供实验诊断，不是默认流程或终稿")
    lines.extend(["- 人工复对　必需", ""])
    report = review_dir / f"candidate-check-{mode}.md"
    write_text(report, "\n".join(lines))
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MV机器对齐输入与候选硬门禁")
    subparsers = parser.add_subparsers(dest="action", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--mv-dir", required=True, type=Path)
    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--mv-dir", required=True, type=Path)
    preflight.add_argument("--mode", default="sofa", choices=sorted(VALID_MODES))
    preflight.add_argument("--ffprobe")
    validate = subparsers.add_parser("validate")
    validate.add_argument("--mv-dir", required=True, type=Path)
    validate.add_argument("--mode", default="sofa", choices=sorted(VALID_MODES))
    sofa_candidate = subparsers.add_parser("sofa-candidate")
    sofa_candidate.add_argument("--mv-dir", required=True, type=Path)
    sofa_candidate.add_argument("--htk", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.action == "prepare":
            result = prepare_alignment(args.mv_dir)
        elif args.action == "preflight":
            result = preflight_alignment(args.mv_dir, args.mode, args.ffprobe)
        elif args.action == "validate":
            result = validate_alignment_candidates(args.mv_dir, args.mode)
        else:
            result = build_sofa_candidate(args.mv_dir, args.htk)
    except Exception as error:
        print(f"error={error}")
        return 2
    print(f"result={result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
