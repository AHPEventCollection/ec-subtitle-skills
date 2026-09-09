from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common import VERSION, read_required_text, write_text


DEFAULT_COOKIE_FILE = Path.home() / ".codex" / "secrets" / "netease.cookie.txt"
USER_AGENT = f"music-video-subtitle/{VERSION}"
LRC_LINE = re.compile(
    r"\[(?P<minute>\d{2,3}):(?P<second>\d{2}(?:\.\d{1,3})?)\](?P<text>.*)"
)
METADATA = re.compile(
    r"^(作词|作曲|编曲|制作人|翻译|混音(?:工程师|师)?|母带(?:工程师|处理)?|录音(?:工程师|师)?|出品|发行|吉他|贝斯|鼓|"
    r"詞|曲|編曲|プロデューサー|Lyrics|Composer|Arranger|Producer|Vocal)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TimedLine:
    timestamp: int
    text: str


@dataclass(frozen=True)
class Candidate:
    song: dict[str, Any]
    original: str | None
    translation: str | None
    match_score: int
    lyric_lines: int
    translated_lines: int
    coverage: float
    missing_timestamps: tuple[int, ...]


def parse_lrc(value: str | None) -> list[TimedLine]:
    lines: list[TimedLine] = []
    for raw_line in (value or "").splitlines():
        match = LRC_LINE.match(raw_line.strip())
        if not match:
            continue
        timestamp = round(
            (int(match.group("minute")) * 60 + float(match.group("second"))) * 1000
        )
        text = match.group("text").strip()
        if text:
            lines.append(TimedLine(timestamp, text))
    return lines


def is_metadata(text: str) -> bool:
    return bool(METADATA.match(text.strip()))


def is_ascii_chant(text: str) -> bool:
    stripped = re.sub(r"[\s♪♫~～!！?？.,，。'\"()（）]", "", text)
    return bool(stripped) and stripped.isascii() and len(stripped) <= 16


def lyric_lines(value: str | None) -> list[TimedLine]:
    return [line for line in parse_lrc(value) if not is_metadata(line.text)]


def _nearest_timestamp(
    target: int, timestamps: set[int], tolerance_ms: int = 350
) -> int | None:
    candidates = [value for value in timestamps if abs(value - target) <= tolerance_ms]
    return (
        min(candidates, key=lambda value: abs(value - target)) if candidates else None
    )


def translation_coverage(
    original: str | None,
    translation: str | None,
) -> tuple[float, tuple[int, ...], int, int]:
    originals = lyric_lines(original)
    translations = lyric_lines(translation)
    translated_timestamps = {line.timestamp for line in translations}
    missing = [
        line.timestamp
        for line in originals
        if not is_ascii_chant(line.text)
        and _nearest_timestamp(line.timestamp, translated_timestamps) is None
    ]
    required = len([line for line in originals if not is_ascii_chant(line.text)])
    covered = required - len(missing)
    coverage = covered / required if required else 0.0
    return coverage, tuple(missing), len(originals), len(translations)


def _normalize_cookie(raw: str | None) -> str | None:
    value = (raw or "").strip()
    if not value:
        return None
    if value.lower().startswith("cookie:"):
        value = value.split(":", 1)[1].strip()
    if "=" not in value and len(value) > 32:
        return f"MUSIC_U={value}; os=pc"
    return value


def read_netease_cookie(cookie_file: Path | None = None) -> str | None:
    cookie = os.environ.get("NETEASE_COOKIE") or os.environ.get("MUSIC_U")
    if cookie:
        return _normalize_cookie(cookie)
    configured = os.environ.get("NETEASE_COOKIE_FILE")
    source = cookie_file or (Path(configured) if configured else DEFAULT_COOKIE_FILE)
    if source.is_file():
        return _normalize_cookie(source.read_text(encoding="utf-8-sig"))
    return None


def netease_request(url: str, cookie: str | None) -> dict[str, Any]:
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"),
        "Referer": "https://music.163.com/",
    }
    if cookie:
        headers["Cookie"] = cookie
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=25) as response:
        return json.loads(response.read().decode("utf-8"))


def netease_search(title: str, artist: str, cookie: str | None) -> list[dict[str, Any]]:
    keyword = f"{title} {artist}".strip()
    url = "https://music.163.com/api/search/get?" + urllib.parse.urlencode(
        {"s": keyword, "type": 1, "limit": 10}
    )
    data = netease_request(url, cookie)
    songs = data.get("result", {}).get("songs", [])
    return songs if isinstance(songs, list) else []


def netease_lyrics(song_id: int, cookie: str | None) -> dict[str, Any]:
    url = "https://music.163.com/api/song/lyric?" + urllib.parse.urlencode(
        {"id": song_id, "lv": 1, "kv": 1, "tv": -1}
    )
    return netease_request(url, cookie)


def artists_text(song: dict[str, Any]) -> str:
    artists = song.get("artists") or song.get("ar") or []
    return "|".join(item.get("name", "") for item in artists if isinstance(item, dict))


def _match_score(title: str, artist: str, song: dict[str, Any]) -> int:
    query_title = title.casefold().strip()
    query_artist = artist.casefold().strip()
    hit_title = str(song.get("name", "")).casefold().strip()
    hit_artist = artists_text(song).casefold()
    score = 0
    if query_title == hit_title:
        score += 8
    elif query_title and (query_title in hit_title or hit_title in query_title):
        score += 4
    if query_artist and query_artist in hit_artist:
        score += 4
    return score


def inspect_candidates(
    title: str,
    artist: str,
    cookie: str | None,
) -> list[Candidate]:
    songs = sorted(
        netease_search(title, artist, cookie),
        key=lambda song: (
            _match_score(title, artist, song),
            song.get("popularity") or song.get("score") or 0,
        ),
        reverse=True,
    )[:5]
    candidates: list[Candidate] = []
    for song in songs:
        if song.get("id") is None:
            continue
        data = netease_lyrics(int(song["id"]), cookie)
        original = (data.get("lrc") or {}).get("lyric")
        translation = (data.get("tlyric") or {}).get("lyric")
        coverage, missing, originals, translations = translation_coverage(
            original, translation
        )
        candidates.append(
            Candidate(
                song=song,
                original=original,
                translation=translation,
                match_score=_match_score(title, artist, song),
                lyric_lines=originals,
                translated_lines=translations,
                coverage=coverage,
                missing_timestamps=missing,
            )
        )
    return candidates


def choose_candidate(candidates: list[Candidate]) -> Candidate | None:
    valid = [candidate for candidate in candidates if candidate.lyric_lines > 0]
    if not valid:
        return None
    best_match = max(candidate.match_score for candidate in valid)
    plausible = [
        candidate
        for candidate in valid
        if candidate.match_score >= max(1, best_match - 2)
    ]
    if not plausible:
        return None
    return max(
        plausible,
        key=lambda candidate: (
            candidate.coverage == 1.0,
            candidate.coverage,
            candidate.match_score,
            candidate.lyric_lines,
            candidate.song.get("popularity") or candidate.song.get("score") or 0,
        ),
    )


def _timestamp_text(timestamp: int) -> str:
    minutes, remainder = divmod(timestamp, 60000)
    seconds = remainder / 1000
    return f"{minutes:02d}:{seconds:05.2f}"


def write_fallback_search_plan(title: str, artist: str, out_dir: Path) -> Path:
    utaten_query = urllib.parse.quote(f'site:utaten.com/lyric/ "{title}" "{artist}"')
    tunecore_query = urllib.parse.quote(f'site:linkco.re "{title}" "{artist}" lyrics')
    path = out_dir / "fallback-search.md"
    write_text(
        path,
        "\n".join(
            [
                "# 歌词固定回退搜索",
                "",
                f"- 歌曲　{title}",
                f"- 艺人　{artist}",
                "- 触发原因　网易云前5个候选没有可用同步歌词",
                "- 回退站点　UtaTen、TuneCore Japan",
                "",
                "## 搜索顺序",
                "",
                "1. [UtaTen站内搜索](https://utaten.com/search)先用艺人名加完整歌名",
                f"2. [UtaTen精确站点搜索](https://www.google.com/search?q={utaten_query})仍无结果时改用完整歌名，再用可辨识首句",
                f"3. [TuneCore Japan精确站点搜索](https://www.google.com/search?q={tunecore_query})核对艺人页或发行页中的Lyrics",
                "",
                "## 采用门槛",
                "",
                "- 页面标题、艺人和歌曲名必须相互匹配",
                "- 必须能看到完整日文歌词正文，只有搜索摘要时不得采用",
                "- 保存采用URL和检索日期，来源不明的转载页不得作为正文真相源",
                "- 回退站点只补正文，不把网页时间或LRC时间轴送入SOFA",
                "- 中文缺失时单独翻译并人工复对，不用ASR改写歌词",
                "",
            ]
        ),
    )
    return path


def fetch_netease_bilingual(
    title: str,
    artist: str,
    out_dir: Path,
    *,
    cookie_file: Path | None = None,
) -> Candidate:
    cookie = read_netease_cookie(cookie_file)
    candidates = inspect_candidates(title, artist, cookie)
    chosen = choose_candidate(candidates)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = out_dir / "lookup.md"
    rows = [
        "|候选|艺人|匹配分|原文行|中文行|中文覆盖|",
        "|---|---|---:|---:|---:|---:|",
    ]
    for candidate in candidates:
        song_id = candidate.song.get("id")
        name = candidate.song.get("name", "")
        rows.append(
            f"|[{name}](https://music.163.com/song?id={song_id})|"
            f"{artists_text(candidate.song)}|{candidate.match_score}|"
            f"{candidate.lyric_lines}|{candidate.translated_lines}|"
            f"{candidate.coverage:.1%}|"
        )
    if chosen is None:
        write_text(
            report,
            "\n".join(
                [
                    "# 网易云歌词查询",
                    "",
                    f"- 歌曲　{title}",
                    f"- 艺人　{artist}",
                    "- 结果　没有找到可用同步歌词",
                    "",
                    *rows,
                    "",
                ]
            ),
        )
        fallback = write_fallback_search_plan(title, artist, out_dir)
        raise LookupError(
            f"网易云前5个候选没有可用同步歌词，按固定双站方案继续：{fallback}"
        )
    original = (chosen.original or "").strip()
    translation = (chosen.translation or "").strip()
    write_text(out_dir / "original.lrc", original + "\n")
    if translation:
        write_text(out_dir / "chinese.lrc", translation + "\n")
    missing = "、".join(_timestamp_text(value) for value in chosen.missing_timestamps)
    chosen_id = chosen.song.get("id")
    write_text(
        report,
        "\n".join(
            [
                "# 网易云歌词查询",
                "",
                f"- 歌曲　{title}",
                f"- 艺人　{artist}",
                f"- 采用　[{chosen.song.get('name', '')}](https://music.163.com/song?id={chosen_id})",
                f"- 网易云艺人　{artists_text(chosen.song)}",
                f"- 中文覆盖　{chosen.coverage:.1%}",
                f"- 缺译时间　{missing or '无'}",
                "",
                "## 前5个候选",
                "",
                *rows,
                "",
                "网易云歌词提供正文与初始时间。正式采用前仍需对照MV实际演唱，确认改词、重复、语气词和尾句",
                "",
            ]
        ),
    )
    return chosen


def fetch_for_workspace(
    mv_dir: Path,
    cookie_file: Path | None = None,
) -> Candidate:
    title = clean_workspace_title(
        read_required_text(mv_dir / "source" / "title.txt", "歌曲名")
    )
    artist = clean_workspace_artist(
        read_required_text(mv_dir / "source" / "artist.txt", "艺人名")
    )
    return fetch_netease_bilingual(
        title,
        artist,
        mv_dir / "lyrics",
        cookie_file=cookie_file,
    )


def clean_workspace_title(title: str) -> str:
    value = title.strip()
    quoted = re.fullmatch(
        r'.*?[「『](.+?)[」』]\s*(?:(?:Full\s*Ver\.?|Official(?:\s+Music)?\s*Video|Official\s*MV|Music\s*Video|MV)\s*)?',
        value, flags=re.IGNORECASE,
    )
    if quoted:
        return quoted.group(1).strip()
    if " - " in value:
        prefix, remainder = value.split(" - ", 1)
        if remainder and len(prefix) <= 40:
            value = remainder
    if " / " in value:
        prefix, remainder = value.split(" / ", 1)
        if remainder and len(prefix) <= 40:
            value = remainder
    value = re.split(r"\s+-?\s*from\s+", value, maxsplit=1, flags=re.IGNORECASE)[0]
    value = re.sub(
        r"\s*[\[(（【].*?(official|music\s*video|mv|live).*?[\])）】].*$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    return value.strip() or title.strip()


def clean_workspace_artist(artist: str) -> str:
    value = re.sub(
        r"\s*(?:公式(?:YouTube)?チャンネル|Official\s+(?:YouTube\s+)?Channel|\s+-\s+Topic)\s*$",
        "", artist.strip(), flags=re.IGNORECASE,
    )
    return value.strip() or artist.strip()
