from __future__ import annotations

import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "unwrap_md_paragraphs.py"
UNWRAPPER = runpy.run_path(str(SCRIPT), run_name="unwrap_md_paragraphs_test")
unwrap_text = UNWRAPPER["unwrap_text"]
joiner = UNWRAPPER["joiner"]


class JoinSpacingTest(unittest.TestCase):
    def test_chinese_to_chinese_has_no_space(self):
        self.assertEqual(joiner("模型把它写成", "多行文本"), "")

    def test_chinese_to_latin_keeps_one_space(self):
        self.assertEqual(joiner("其中还提到了作为", "ASR 输入"), " ")
        self.assertEqual(joiner("输入的", "stdio 模式"), " ")

    def test_latin_to_chinese_keeps_one_space(self):
        self.assertEqual(joiner("HTTP", "请求超时"), " ")

    def test_full_width_punctuation_takes_no_space(self):
        self.assertEqual(joiner("补充说明，", "API 返回值"), "")
        self.assertEqual(joiner("使用 stdio", "。结束"), "")

    def test_latin_to_latin_keeps_one_space(self):
        self.assertEqual(joiner("controlled", "language"), " ")

    def test_ascii_punctuation_does_not_gain_space(self):
        self.assertEqual(joiner("stdio", ", grpc"), "")

    def test_inline_markup_uses_visible_characters(self):
        self.assertEqual(joiner("详见 **接口说明**", "文档目录"), "")
        self.assertEqual(joiner("详见 [接口说明](api.md)", "文档目录"), "")
        self.assertEqual(joiner("参数", "`--check` 可用"), " ")


class UnwrapParagraphTest(unittest.TestCase):
    def unwrap(self, text: str) -> str:
        return unwrap_text(text)[0]

    def test_paragraph_becomes_one_line(self):
        text = "模型把它写成\n多行，其中提到了作为\nASR 输入的\nstdio 模式。\n"
        self.assertEqual(
            self.unwrap(text),
            "模型把它写成多行，其中提到了作为 ASR 输入的 stdio 模式。\n",
        )

    def test_reports_start_line_and_merged_count(self):
        text = "# 标题\n\n第一行\n第二行\n第三行\n"
        _, joins = unwrap_text(text)
        self.assertEqual(joins[0][0], 3)
        self.assertEqual(joins[0][1], 2)

    def test_keeps_front_matter(self):
        text = "---\nname: demo\ndescription: 第一行\n  第二行\n---\n\n正文。\n"
        self.assertEqual(self.unwrap(text), text)

    def test_keeps_fenced_code_block(self):
        text = "```bash\necho 一\necho 二\n```\n"
        self.assertEqual(self.unwrap(text), text)

    def test_keeps_tables_headings_and_rules(self):
        text = (
            "## 参数\n"
            "\n"
            "| 参数 | 说明 |\n"
            "| --- | --- |\n"
            "| `mode` | 运行模式 |\n"
            "\n"
            "---\n"
            "\n"
            "副标题\n"
            "---\n"
        )
        self.assertEqual(self.unwrap(text), text)

    def test_keeps_blockquote_and_html_block(self):
        text = "> 引用第一行\n> 引用第二行\n\n<div>\nHTML 内容\n</div>\n"
        self.assertEqual(self.unwrap(text), text)

    def test_keeps_indented_code_block(self):
        text = "说明如下。\n\n    indented code\n    stays as is\n"
        self.assertEqual(self.unwrap(text), text)

    def test_keeps_reference_definition(self):
        text = "[ref]: https://example.com\n[note]: https://example.org\n"
        self.assertEqual(self.unwrap(text), text)

    def test_unwraps_list_items_and_keeps_structure(self):
        text = (
            "- 第一项为了行宽断开，\n"
            "  第二行继续说明 API 的\n"
            "  返回值。\n"
            "- 第二项\n"
            "\n"
            "1. 步骤第一行\n"
            "   步骤续行\n"
        )
        self.assertEqual(
            self.unwrap(text),
            "- 第一项为了行宽断开，第二行继续说明 API 的返回值。\n"
            "- 第二项\n"
            "\n"
            "1. 步骤第一行步骤续行\n",
        )

    def test_keeps_nested_list_structure(self):
        text = "- 父项说明\n  - 子项说明\n    继续说明子项\n"
        self.assertEqual(
            self.unwrap(text),
            "- 父项说明\n  - 子项说明继续说明子项\n",
        )

    def test_unwraps_second_paragraph_inside_list_item(self):
        text = (
            "- 第一项说明。\n"
            "\n"
            "    列表项内的第二段，\n"
            "    应当合并为一行。\n"
        )
        self.assertEqual(
            self.unwrap(text),
            "- 第一项说明。\n\n    列表项内的第二段，应当合并为一行。\n",
        )

    def test_keeps_indented_code_inside_list_item(self):
        text = "- 步骤说明。\n\n      indented code\n      stays as is\n"
        self.assertEqual(self.unwrap(text), text)

    def test_keeps_explicit_hard_break(self):
        text = "本项目采用 MIT License。  \n详见 LICENSE。\n"
        self.assertEqual(self.unwrap(text), text)

    def test_keeps_trailing_backslash_break(self):
        text = "第一行\\\n第二行\n"
        self.assertEqual(self.unwrap(text), text)

    def test_keeps_blank_line_structure_and_final_newline(self):
        text = "第一段。\n\n第二段。"
        self.assertEqual(self.unwrap(text), text)

    def test_is_idempotent(self):
        text = "模型把它写成\n多行，提到了作为\nASR 输入。\n"
        once = self.unwrap(text)
        self.assertEqual(self.unwrap(once), once)

    def test_file_ignore_marker_skips_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.md"
            text = "<!-- unwrap-disable-file -->\n\n第一行\n第二行\n"
            path.write_text(text, encoding="utf-8")
            unwrapped, joins = UNWRAPPER["process_file"](path)
            self.assertEqual(unwrapped, text)
            self.assertEqual(joins, [])


class UnwrapCliTest(unittest.TestCase):
    def run_cli(self, path: Path, *flags: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *flags, str(path)],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_check_reports_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.md"
            text = "模型把它写成\n多行。\n"
            path.write_text(text, encoding="utf-8")
            result = self.run_cli(path, "--check")
            self.assertEqual(result.returncode, 2)
            self.assertIn("FAIL", result.stdout)
            self.assertEqual(path.read_text(encoding="utf-8"), text)

    def test_default_run_writes_back(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.md"
            path.write_text("模型把它写成\n多行。\n", encoding="utf-8")
            result = self.run_cli(path)
            self.assertEqual(result.returncode, 0)
            self.assertIn("DONE", result.stdout)
            self.assertEqual(
                path.read_text(encoding="utf-8"), "模型把它写成多行。\n"
            )

    def test_clean_file_passes_check(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.md"
            path.write_text("模型把它写成一行。\n", encoding="utf-8")
            result = self.run_cli(path, "--check")
            self.assertEqual(result.returncode, 0)
            self.assertIn("PASS", result.stdout)

    def test_repository_markdown_has_no_hard_wraps(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--check", str(ROOT)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stdout)


if __name__ == "__main__":
    unittest.main()
