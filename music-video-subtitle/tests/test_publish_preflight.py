from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from publish_preflight import Post, check_posts, check_title_preference, parse_copy, preflight, resolve_copy  # noqa: E402


class PublishPreflightTests(unittest.TestCase):
    def test_formal_copy_wins_and_is_not_modified(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "fixture"
            (root / "work").mkdir(parents=True)
            (root / "output").mkdir()
            draft = root / "work" / "publish-copy.md"
            draft.write_text("旧草稿", encoding="utf-8")
            (root / "output" / "fixture.hardsub.v01.mp4").touch()
            formal = root / "output" / "fixture.publish-copy.v01.md"
            text = "## 微博\n\n标题：人工标题\n\n正文：\n\n人工正文#MV#\n"
            formal.write_text(text, encoding="utf-8")
            self.assertEqual(resolve_copy(root), formal)
            self.assertIn("人工标题", preflight(root))
            self.assertEqual(formal.read_text(encoding="utf-8"), text)
            self.assertEqual(draft.read_text(encoding="utf-8"), "旧草稿")

    def test_latest_video_cannot_silently_use_old_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "fixture"
            (root / "output").mkdir(parents=True)
            (root / "output" / "fixture.hardsub.v02.mp4").touch()
            (root / "output" / "fixture.publish-copy.v01.md").touch()
            with self.assertRaises(FileNotFoundError):
                resolve_copy(root)

    def test_preserves_manual_paragraphs_and_separates_note_metadata(self):
        body = "人工第一段\n\n\n第二段\n&#x23;标签[话题]#"
        post = parse_copy("## 小红书\n标题：人工标题\n正文：\n" + body
                          + "\n\n上一篇笔记ID：" + "a" * 24)["小红书"]
        self.assertEqual(post.body, body)
        self.assertEqual(post.previous_note_id, "a" * 24)

    def test_previous_reference_is_per_platform(self):
        posts = {"B站": Post("标题", "上一首：BV1dxto6fEBE"),
                 "小红书": Post("标题", "正文")}
        issues = check_posts(posts, list(posts), True)
        self.assertEqual(len(issues), 2)
        self.assertTrue(all(issue.startswith("小红书") for issue in issues))
        posts["小红书"] = Post("标题", "上一篇：准确标题", previous_note_id="a" * 24)
        self.assertEqual(check_posts(posts, list(posts), True), [])

    def test_previous_bv_must_be_attached_to_previous_label(self):
        post = Post("标题", "本期：BV1dxto6fEBE\n上一首：还不知道")
        self.assertTrue(check_posts({"B站": post}, ["B站"], True))

    def test_link_boundaries(self):
        posts = {"小红书": Post("标题", "https://www.bilibili.com/video/BV1dxto6fEBE/"),
                 "B站": Post("标题", "https://youtube.com/watch?v=fixture")}
        self.assertEqual(len(check_posts(posts, list(posts), False)), 2)
        posts["B站"].body = "上一首：https://www.bilibili.com/video/BV1dxto6fEBE/"
        self.assertEqual(check_posts(posts, ["B站"], True), [])

    def test_duplicate_sections_rejected(self):
        with self.assertRaises(ValueError):
            parse_copy("## 微博\n标题：甲\n正文：乙\n## 微博\n标题：丙\n正文：丁")

    def test_body_directly_below_title_is_not_part_of_title(self):
        post = parse_copy("## 小红书\n\n标题：中字｜原文歌名\n\n第一段\n\n第二段\n\n转载链接：油管\n")["小红书"]
        self.assertEqual(post.title, "中字｜原文歌名")
        self.assertEqual(post.body, "第一段\n\n第二段")
        self.assertEqual(post.source, "油管")

    def test_explicit_original_title_blocks_unrequested_translation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "work").mkdir()
            (root / "work/publish-title.txt").write_text("君はロックを聴かない", encoding="utf-8")
            bad = {"小红书": Post("中字｜爱缪《你不听摇滚》", "正文")}
            self.assertTrue(check_title_preference(root, bad))
            good = {"小红书": Post("中字｜あいみょん《君はロックを聴かない》", "正文")}
            self.assertEqual(check_title_preference(root, good), [])

    def test_jpeg_delivery_keeps_jpeg_and_image_content(self):
        from PIL import Image
        from mv_pipeline import _write_versioned_cover

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original = root / "source.jpg"
            final = root / "cover.jpg"
            Image.new("RGB", (1280, 720), "blue").save(original)
            _write_versioned_cover(original, final)
            self.assertEqual(final.read_bytes(), original.read_bytes())
            with Image.open(final) as im:
                self.assertEqual((im.format, im.size), ("JPEG", (1280, 720)))


if __name__ == "__main__":
    unittest.main()
