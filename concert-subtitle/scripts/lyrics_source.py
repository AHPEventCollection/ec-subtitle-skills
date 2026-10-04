from __future__ import annotations

import argparse
import json
import os
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from common import VERSION, write_json, write_text


DEFAULT_COOKIE_FILE = Path.home() / ".codex" / "secrets" / "netease.cookie.txt"
USER_AGENT = f"concert-subtitle/{VERSION}"


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    slug = re.sub(
        r"[^\w\u3040-\u30ff\u3400-\u9fff.-]+",
        "_",
        normalized,
        flags=re.UNICODE,
    )
    return slug.strip("_") or "untitled"


def line_count(value: str | None) -> int:
    return len([line for line in (value or "").splitlines() if line.strip()])


def has_lrc_timestamps(value: str | None) -> bool:
    return bool(re.search(r"\[\d{2}:\d{2}(?:\.\d{1,3})?\]", value or ""))


def score_text_match(
    title: str,
    artist: str,
    item_title: str,
    item_artist: str,
) -> int:
    title_l = title.casefold()
    artist_l = artist.casefold()
    item_title_l = item_title.casefold()
    item_artist_l = item_artist.casefold()
    score = 0
    if title_l and title_l == item_title_l:
        score += 8
    elif title_l and title_l in item_title_l:
        score += 4
    if artist_l and artist_l in item_artist_l:
        score += 4
    return score


def lrclib_search(title: str, artist: str) -> list[dict[str, Any]]:
    params = {"track_name": title}
    if artist:
        params["artist_name"] = artist
    url = "https://lrclib.net/api/search?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=25) as response:
        data = json.loads(response.read().decode("utf-8"))
    return data if isinstance(data, list) else []


def choose_lrclib_hit(
    title: str,
    artist: str,
    hits: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not hits:
        return None
    return max(
        hits,
        key=lambda item: (
            score_text_match(
                title,
                artist,
                item.get("trackName", ""),
                item.get("artistName", ""),
            ),
            bool(item.get("syncedLyrics")),
            line_count(item.get("plainLyrics")),
            not bool(item.get("instrumental")),
        ),
    )


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
    configured_file = os.environ.get("NETEASE_COOKIE_FILE")
    source = cookie_file or (Path(configured_file) if configured_file else None)
    if source is None and DEFAULT_COOKIE_FILE.is_file():
        source = DEFAULT_COOKIE_FILE
    if source and source.is_file():
        return _normalize_cookie(source.read_text(encoding="utf-8-sig"))
    return None


def netease_request(url: str, cookie: str | None) -> dict[str, Any]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36"
        ),
        "Referer": "https://music.163.com/",
    }
    if cookie:
        headers["Cookie"] = cookie
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=25) as response:
        return json.loads(response.read().decode("utf-8"))


def netease_search(
    title: str,
    artist: str,
    cookie: str | None,
) -> list[dict[str, Any]]:
    keyword = f"{title} {artist}".strip()
    url = "https://music.163.com/api/search/get?" + urllib.parse.urlencode(
        {"s": keyword, "type": 1, "limit": 10}
    )
    data = netease_request(url, cookie)
    songs = data.get("result", {}).get("songs", [])
    return songs if isinstance(songs, list) else []


def netease_artists_text(song: dict[str, Any]) -> str:
    artists = song.get("artists") or song.get("ar") or []
    return "|".join(
        item.get("name", "") for item in artists if isinstance(item, dict)
    )


def netease_song_score(title: str, artist: str, song: dict[str, Any]) -> int:
    return score_text_match(
        title,
        artist,
        song.get("name", ""),
        netease_artists_text(song),
    )


def rank_netease_songs(
    title: str,
    artist: str,
    songs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return sorted(
        songs,
        key=lambda song: (
            netease_song_score(title, artist, song),
            song.get("popularity") or song.get("score") or 0,
        ),
        reverse=True,
    )


def choose_netease_song(
    title: str,
    artist: str,
    songs: list[dict[str, Any]],
) -> dict[str, Any] | None:
    ranked = rank_netease_songs(title, artist, songs)
    return ranked[0] if ranked else None


def netease_lyrics(song_id: int, cookie: str | None) -> dict[str, Any]:
    url = "https://music.163.com/api/song/lyric?" + urllib.parse.urlencode(
        {"id": song_id, "lv": 1, "kv": 1, "tv": -1}
    )
    return netease_request(url, cookie)


def choose_lyric_file(
    synced: str | None,
    plain: str | None,
) -> tuple[str, str] | None:
    if synced and has_lrc_timestamps(synced):
        return "lrc", synced.strip()
    if plain and plain.strip():
        return "txt", plain.strip()
    if synced and synced.strip():
        text = re.sub(r"\[\d{2}:\d{2}(?:\.\d{1,3})?\]", "", synced).strip()
        return "txt", text
    return None


def save_single_lyrics_file(
    out_dir: Path,
    *,
    title: str,
    artist: str,
    source: str,
    synced: str | None,
    plain: str | None,
    metadata: dict[str, Any],
    translation_synced: str | None = None,
    translation_plain: str | None = None,
    translation_source: str | None = None,
) -> dict[str, Any]:
    chosen = choose_lyric_file(synced, plain)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = slugify(title)
    result = {
        "title": title,
        "artist": artist,
        "source": source,
        "status": "not_found",
        "file": None,
        "file_type": None,
        "translation_status": "not_found",
        "translation_file": None,
        "translation_file_type": None,
        "translation_source": None,
        "metadata": metadata,
    }
    if chosen is None:
        return result

    file_type, text = chosen
    lyric_path = out_dir / f"{stem}.{file_type}"
    write_text(lyric_path, text + "\n")
    result.update(
        {
            "status": "found",
            "file": str(lyric_path.resolve()),
            "file_type": file_type,
            "line_count": line_count(text),
        }
    )
    translated = choose_lyric_file(translation_synced, translation_plain)
    if translated is not None:
        translation_file_type, translation_text = translated
        translation_path = out_dir / f"{stem}.zh.{translation_file_type}"
        write_text(translation_path, translation_text + "\n")
        result.update(
            {
                "translation_status": "found",
                "translation_file": str(translation_path.resolve()),
                "translation_file_type": translation_file_type,
                "translation_source": translation_source or source,
                "translation_line_count": line_count(translation_text),
            }
        )
    return result


def fetch_one(
    title: str,
    artist: str,
    out_dir: Path,
    *,
    cookie_file: Path | None = None,
) -> dict[str, Any]:
    query_log: list[dict[str, Any]] = []
    cookie = read_netease_cookie(cookie_file)
    netease_fallback: tuple[dict[str, Any], dict[str, Any]] | None = None
    try:
        songs = netease_search(title, artist, cookie)
        query_log.append(
            {
                "source": "netease_search",
                "hits": len(songs),
                "cookie": bool(cookie),
            }
        )
        ranked = rank_netease_songs(title, artist, songs)
        best_score = netease_song_score(title, artist, ranked[0]) if ranked else 0
        for song in ranked[:5]:
            match_score = netease_song_score(title, artist, song)
            if match_score < max(1, best_score - 2) or song.get("id") is None:
                continue
            lyric_data = netease_lyrics(int(song["id"]), cookie)
            synced = (lyric_data.get("lrc") or {}).get("lyric")
            translated = (lyric_data.get("tlyric") or {}).get("lyric")
            if synced and has_lrc_timestamps(synced):
                if translated and translated.strip():
                    query_log.append(
                        {
                            "source": "netease",
                            "song_id": song.get("id"),
                            "bilingual": True,
                        }
                    )
                    return save_single_lyrics_file(
                        out_dir,
                        title=title,
                        artist=artist,
                        source="netease",
                        synced=synced,
                        plain=synced,
                        translation_synced=translated,
                        translation_plain=translated,
                        translation_source="netease",
                        metadata={
                            "hit": {
                                "id": song.get("id"),
                                "name": song.get("name"),
                                "artists": netease_artists_text(song),
                            },
                            "query_log": query_log,
                        },
                    )
                if netease_fallback is None:
                    netease_fallback = (song, lyric_data)
                query_log.append(
                    {
                        "source": "netease",
                        "song_id": song.get("id"),
                        "bilingual": False,
                    }
                )
            else:
                query_log.append(
                    {
                        "source": "netease",
                        "song_id": song.get("id"),
                        "fallback": "no_synced_lrc",
                    }
                )
    except Exception as exc:
        query_log.append(
            {
                "source": "netease",
                "error": str(exc),
                "cookie": bool(cookie),
            }
        )

    if netease_fallback is not None:
        song, lyric_data = netease_fallback
        synced = (lyric_data.get("lrc") or {}).get("lyric")
        return save_single_lyrics_file(
            out_dir,
            title=title,
            artist=artist,
            source="netease",
            synced=synced,
            plain=synced,
            metadata={
                "hit": {
                    "id": song.get("id"),
                    "name": song.get("name"),
                    "artists": netease_artists_text(song),
                },
                "query_log": query_log,
            },
        )

    try:
        hits = lrclib_search(title, artist)
        query_log.append({"source": "lrclib", "hits": len(hits)})
        hit = choose_lrclib_hit(title, artist, hits)
        if hit:
            return save_single_lyrics_file(
                out_dir,
                title=title,
                artist=artist,
                source="lrclib",
                synced=hit.get("syncedLyrics"),
                plain=hit.get("plainLyrics"),
                metadata={
                    "hit": {
                        key: hit.get(key)
                        for key in (
                            "id",
                            "trackName",
                            "artistName",
                            "albumName",
                            "duration",
                        )
                    },
                    "query_log": query_log,
                },
            )
    except Exception as exc:
        query_log.append({"source": "lrclib", "error": str(exc)})

    return save_single_lyrics_file(
        out_dir,
        title=title,
        artist=artist,
        source="none",
        synced=None,
        plain=None,
        metadata={"query_log": query_log},
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "优先获取网易原文LRC与tlyric，原译分离保存；"
            "网易无可用原文时回退LRCLIB"
        )
    )
    parser.add_argument("--title", required=True)
    parser.add_argument("--artist", default="")
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--netease-cookie-file", type=Path)
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = fetch_one(
        args.title,
        args.artist,
        args.out_dir,
        cookie_file=args.netease_cookie_file,
    )
    write_json(args.out_dir / f"{slugify(args.title)}.lookup.json", result)
    state = result["file_type"] if result["status"] == "found" else "missing"
    bilingual = (
        "bilingual"
        if result.get("translation_status") == "found"
        else "original-only"
    )
    print(
        f"{args.title}: {state}, {bilingual} via {result['source']} -> "
        f"{result.get('file') or args.out_dir}"
    )
    time.sleep(0.05)
    return 0 if result["status"] == "found" else 1


if __name__ == "__main__":
    raise SystemExit(main())
