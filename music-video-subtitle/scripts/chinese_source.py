from __future__ import annotations

from pathlib import Path

from common import ensure_workspace, write_text


SOURCE_FILE = "chinese-source.txt"
NETEASE_CREDIT = "中文歌词来源：网易云音乐"
MODEL_PREFIX = "中文歌词来源：翻译模型（"
HYBRID_PREFIX = "中文歌词来源：网易云音乐、翻译模型（"


def record_chinese_source(
    mv_dir: Path,
    kind: str,
    model: str | None = None,
) -> Path:
    mv_dir = ensure_workspace(mv_dir)
    if kind == "netease":
        if model:
            raise ValueError("网易云音乐来源不应填写翻译模型")
        credit = NETEASE_CREDIT
    elif kind == "translation-model":
        model_name = (model or "").strip()
        if not model_name or "\n" in model_name or "\r" in model_name:
            raise ValueError("翻译模型来源必须填写该次实际使用的模型名")
        credit = f"{MODEL_PREFIX}{model_name}）"
    elif kind == "hybrid":
        model_name = (model or "").strip()
        if not model_name or "\n" in model_name or "\r" in model_name:
            raise ValueError("混合来源必须填写该次实际使用的翻译模型名")
        credit = f"{HYBRID_PREFIX}{model_name}）"
    else:
        raise ValueError("中文歌词来源只能是netease、translation-model或hybrid")
    path = mv_dir / "subtitle" / SOURCE_FILE
    write_text(path, credit + "\n")
    return path


def read_chinese_source(mv_dir: Path) -> str:
    path = ensure_workspace(mv_dir) / "subtitle" / SOURCE_FILE
    if not path.is_file():
        raise FileNotFoundError(
            "缺少中文歌词来源，请先运行chinese-source或在官方字幕翻译时填写--translation-model"
        )
    credit = path.read_text(encoding="utf-8-sig").strip()
    if credit == NETEASE_CREDIT:
        return credit
    if credit.startswith(MODEL_PREFIX) and credit.endswith("）"):
        model = credit[len(MODEL_PREFIX) : -1].strip()
        if model:
            return credit
    if credit.startswith(HYBRID_PREFIX) and credit.endswith("）"):
        model = credit[len(HYBRID_PREFIX) : -1].strip()
        if model:
            return credit
    raise ValueError(f"中文歌词来源格式非法：{path}")
