"""Read-only publication preparation; never changes an edited copy or opens a browser."""
from __future__ import annotations

import argparse
import html
import re
import sys
from dataclasses import dataclass
from pathlib import Path

PLATFORMS = ("微博", "小红书", "B站", "视频号")
FIELD = re.compile(r"^(标题|正文|话题|标签|转载链接|上一篇笔记ID)：(.*)$")
URL = re.compile(r"https?://[^\s<>）]+")
BV = re.compile(r"\bBV[0-9A-Za-z]{10}\b")


@dataclass
class Post:
    title: str
    body: str
    tags: str = ""
    source: str = ""
    previous_note_id: str = ""


def parse_copy(text: str) -> dict[str, Post]:
    chunks = re.split(r"(?m)^##\s+(微博|小红书|B站|视频号)\s*$", text)
    posts: dict[str, Post] = {}
    for platform, chunk in zip(chunks[1::2], chunks[2::2]):
        if platform in posts:
            raise ValueError(f"平台章节重复：{platform}")
        fields: dict[str, list[str]] = {}
        current = ""
        for line in chunk.splitlines():
            match = FIELD.match(line)
            if match:
                current = match[1]
                if current in fields and any(part.strip() for part in fields[current]):
                    raise ValueError(f"{platform}字段重复：{current}")
                fields[current] = [match[2]]
            elif current == "标题" and not line.strip():
                # User copy may put the body directly below the one-line title.
                current = "正文"
                fields.setdefault(current, [])
            elif current:
                fields[current].append(line)
        value = {key: "\n".join(lines).strip() for key, lines in fields.items()}
        posts[platform] = Post(
            title=value.get("标题", ""), body=value.get("正文", ""),
            tags=value.get("标签", value.get("话题", "")),
            source=value.get("转载链接", ""),
            previous_note_id=value.get("上一篇笔记ID", ""),
        )
    return posts


def resolve_copy(mv_dir: Path, explicit: Path | None = None) -> Path:
    if explicit is not None:
        if not explicit.is_file():
            raise FileNotFoundError(f"指定文案不存在：{explicit}")
        return explicit
    videos = list((mv_dir / "output").glob(f"{mv_dir.name}.hardsub.v*.mp4"))
    versions = [int(m[1]) for p in videos
                if (m := re.fullmatch(re.escape(mv_dir.name) + r"\.hardsub\.v(\d+)\.mp4", p.name))]
    if versions:
        formal = mv_dir / "output" / f"{mv_dir.name}.publish-copy.v{max(versions):02d}.md"
        if not formal.is_file():
            raise FileNotFoundError(f"当前成品缺少同版本正式文案：{formal}")
        return formal
    draft = mv_dir / "work" / "publish-copy.md"
    if not draft.is_file():
        raise FileNotFoundError(f"缺少文案：{draft}")
    return draft


def check_posts(posts: dict[str, Post], platforms: list[str], require_previous: bool) -> list[str]:
    issues: list[str] = []
    for platform in platforms:
        post = posts.get(platform)
        if post is None or not post.title or not post.body:
            issues.append(f"{platform}：缺少章节、标题或正文")
            continue
        urls = URL.findall(post.body)
        if platform == "小红书" and urls:
            issues.append("小红书：正文不得粘贴URL，上一篇应使用站内引用")
        if platform == "B站" and any(
            not re.match(r"https?://(?:www\.)?bilibili\.com/", u) for u in urls
        ):
            issues.append("B站：简介存在外站URL，应移到专用转载来源字段")
        if require_previous and platform == "B站":
            refs = re.findall(r"(?:上一篇|上一首)：([^\n]*)", post.body)
            if not any(BV.search(ref) for ref in refs):
                issues.append("B站：缺少已核验的上一篇BV引用")
        if require_previous and platform == "小红书":
            if not re.search(r"上一篇：\S", post.body):
                issues.append("小红书：缺少上一篇标题")
            if not re.fullmatch(r"[0-9a-f]{24}", post.previous_note_id):
                issues.append("小红书：缺少已核验的上一篇笔记ID，不能冒充已完成站内引用")
    return issues


def check_title_preference(mv_dir: Path, posts: dict[str, Post]) -> list[str]:
    """An explicit original song title is authoritative for this workspace."""
    preference = mv_dir / "work" / "publish-title.txt"
    if not preference.is_file():
        return []
    song = preference.read_text(encoding="utf-8-sig").strip()
    if not song or "\n" in song:
        return ["标题约定：publish-title.txt必须只含一行已核实的原文歌名"]
    issues = []
    for platform, post in posts.items():
        if song not in post.title:
            issues.append(f"{platform}：标题未保留指定原文歌名《{song}》")
        if platform == "小红书" and len(post.title) > 20:
            issues.append("小红书：标题超过20字，应缩短前缀，保留原文歌名")
    return issues


def preflight(mv_dir: Path, explicit: Path | None = None, require_previous: bool = False,
              *, issues_out: list[str] | None = None) -> str:
    copy = resolve_copy(mv_dir, explicit)
    text = copy.read_text(encoding="utf-8-sig")
    posts = parse_copy(text)
    scope = mv_dir / "work" / "publish-platforms.txt"
    platforms = scope.read_text(encoding="utf-8-sig").split() if scope.is_file() else list(posts)
    if not platforms or len(set(platforms)) != len(platforms) or any(p not in PLATFORMS for p in platforms):
        raise ValueError("发布平台声明为空、重复或含未知平台")
    issues = check_posts(posts, platforms, require_previous)
    issues.extend(check_title_preference(mv_dir, posts))
    lines = ["# 发布准备检查", "", f"文案真相源：{copy}", "原文件只读，未同步或改写旧草稿", ""]
    version = re.search(r"\.publish-copy\.v(\d+)\.md$", copy.name)
    if version:
        stem = f"{mv_dir.name}.hardsub.v{int(version[1]):02d}.mp4"
        video = mv_dir / "output" / stem
        covers = [p for p in (mv_dir / "output").glob(
            f"{mv_dir.name}.cover.v{int(version[1]):02d}.*"
        ) if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}]
        if not video.is_file() or video.stat().st_size == 0:
            issues.append("素材：同版本正片缺少或为空")
        else:
            lines.append(f"正片：{video}（{video.stat().st_size}字节）")
        if len(covers) != 1 or covers[0].stat().st_size == 0:
            issues.append("素材：同版本封面缺少、为空或不唯一")
        else:
            lines.append(f"封面：{covers[0]}")
        lines.append("")
    else:
        issues.append("素材：当前使用前期草稿，尚未确认同版本发布包")
    for platform in platforms:
        post = posts.get(platform)
        if post is None:
            continue
        lines += [f"## {platform}", "", f"标题：{post.title}",
                  f"正文：{len(post.body)}字，保留人工格式", f"标签：{html.unescape(post.tags) or '沿用正文内话题'}"]
        if platform == "B站":
            source = post.source
            source_path = mv_dir / "source" / "source.md"
            if not source and source_path.is_file():
                source = next(iter(URL.findall(source_path.read_text(encoding="utf-8-sig"))), "")
            lines.append(f"专用转载来源：{source or '尚缺少，不能放进正文代替'}")
        if platform == "小红书" and post.previous_note_id:
            lines.append(f"上一篇笔记ID：{post.previous_note_id}，仍须在页面核验站内引用卡片")
        lines.append("")
    lines += ["## 检查结论", ""]
    lines += [f"- {issue}" for issue in issues] or ["本地文案检查通过，不代表网页待提交已完成"]
    lines += ["- 浏览器上传、封面、字段持久性和站内引用必须独立回读", "- 未打开浏览器、未上传、未提交"]
    if issues_out is not None:
        issues_out.extend(issues)
    return "\n".join(lines) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mv-dir", type=Path, required=True)
    parser.add_argument("--copy", type=Path)
    parser.add_argument("--require-previous", action="store_true")
    args = parser.parse_args()
    issues: list[str] = []
    print(preflight(args.mv_dir, args.copy, args.require_previous, issues_out=issues), end="")
    return 2 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
