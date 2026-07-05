import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bookrecall.parser import is_chapter_heading, parse_chapters


class ParserTest(unittest.TestCase):
    def test_jie_is_recognized(self) -> None:
        # 《蛊真人》真实格式：行首缩进 + 第X节 标题，独立成行
        self.assertTrue(is_chapter_heading("    第一节 突围"))
        self.assertTrue(is_chapter_heading("第二节 暴风雨"))
        self.assertTrue(is_chapter_heading("第一百零一节 奇遇"))
        # 章也应继续被识别
        self.assertTrue(is_chapter_heading("第1章 起点"))
        self.assertTrue(is_chapter_heading("第十章 回声"))

    def test_inline_jie_not_misjudged(self) -> None:
        # 正文里夹带的「第X节」长句不应被误判为标题
        self.assertFalse(is_chapter_heading("    方源是第二十八节的思想，他以为这样就能逃脱命运的安排。"))
        self.assertFalse(
            is_chapter_heading(
                "他们说的是第三十节的内容，但其实和这件事没有关系。"
            )
        )

    def test_parse_jie_chapters(self) -> None:
        text = (
            "    第一节 突围\n"
            "明天就要开战了。\n"
            "    第二节 和解\n"
            "他们终于放下武器。\n"
            "    第三节 远行\n"
            "马车驶向远方。\n"
        )
        chapters = parse_chapters(text)
        self.assertEqual(len(chapters), 3)
        # 标题应只保留「突围/和解/远行」
        self.assertEqual(chapters[0].number, 1)
        self.assertEqual(chapters[0].title, "突围")
        self.assertEqual(chapters[1].number, 2)
        self.assertEqual(chapters[1].title, "和解")
        self.assertEqual(chapters[2].number, 3)
        # 内容不串章
        self.assertIn("开战", chapters[0].content)
        self.assertIn("武器", chapters[1].content)
        self.assertIn("远方", chapters[2].content)

    def test_volume_heading_scopes_section_titles(self) -> None:
        text = (
            "第一卷  魔性不改\n"
            "第一节：纵身亡魔心仍不悔\n"
            "古月方源纵身一跃。\n"
            "第二节 白日梦醒\n"
            "山寨晨光渐亮。\n"
            "第二卷 风起青茅\n"
            "第一节 再入山林\n"
            "山风吹来。\n"
        )
        chapters = parse_chapters(text)
        self.assertEqual(len(chapters), 3)
        self.assertEqual(chapters[0].title, "第一卷 魔性不改 / 纵身亡魔心仍不悔")
        self.assertEqual(chapters[1].title, "第一卷 魔性不改 / 白日梦醒")
        self.assertEqual(chapters[2].title, "第二卷 风起青茅 / 再入山林")
        self.assertIn("纵身一跃", chapters[0].content)
        self.assertNotIn("魔性不改", chapters[0].content)

    def test_volume_only_does_not_create_empty_chapter(self) -> None:
        text = "第一卷 魔性不改\n第一节 开端\n正文。\n"
        chapters = parse_chapters(text)
        self.assertEqual(len(chapters), 1)
        self.assertEqual(chapters[0].title, "第一卷 魔性不改 / 开端")

    def test_volume_without_name_still_scopes_sections(self) -> None:
        text = "第一卷\n第一节 开端\n正文。\n"
        chapters = parse_chapters(text)
        self.assertEqual(len(chapters), 1)
        self.assertEqual(chapters[0].title, "第一卷 / 开端")

    def test_chapter_numbers_are_sequential(self) -> None:
        # 真实序号在正文里不可靠恢复；本实现统一按出现顺序递增
        text = (
            "第1章 起\n内容一\n"
            "第3章 终\n内容二\n"
        )
        chapters = parse_chapters(text)
        self.assertEqual([c.number for c in chapters], [1, 2])

    def test_no_chapter_falls_back_to_whole_text(self) -> None:
        text = "这是一段没有任何章节标记的纯文本。\n第二行也没用。\n"
        chapters = parse_chapters(text)
        self.assertEqual(len(chapters), 1)
        self.assertEqual(chapters[0].title, "全文")

    def test_zhang_chapters_still_work(self) -> None:
        # 旧 sample_book 采用「第X章」，必须保持兼容
        text = "第1章 起点\n林澈看到了钥匙。\n第2章 雨夜\n黑衣人出现。\n"
        chapters = parse_chapters(text)
        self.assertEqual(len(chapters), 2)
        self.assertEqual(chapters[0].title, "起点")
        self.assertEqual(chapters[1].title, "雨夜")


if __name__ == "__main__":
    unittest.main()
