#!/usr/bin/env python3
"""展开中文 Markdown 段落中的硬换行。

为了控制源码行宽而在段落中间手动断行，会带来两个问题：源文件读起来很碎；
中西文边界上的断行在部分渲染器里会多出或吞掉空格。本脚本把段落和列表项还原为
「一段一行」，正文靠编辑器软换行阅读。

拼接边界按中西文留白规则处理：中文与中文之间不加空格，中文与半角英文、数字之间
加一个空格，全角标点两侧不加空格。

不改动的内容：

- Markdown front matter
- 围栏代码块与缩进代码块
- 含竖线的表格行
- 标题、Setext 下划线、分隔线
- HTML 块、链接与脚注引用定义
- 引用块，它可能是需要保持原样的引用
- 以两个空格或反斜杠结尾的显式换行

文件中出现 ``<!-- unwrap-disable-file -->`` 时整份文件跳过。
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TARGETS = ["."]
FILE_IGNORE_MARKER = "<!-- unwrap-disable-file -->"
SKIP_DIR_NAMES = {".git", ".venv", "node_modules", "vendor"}
CONTINUATION_SLACK = 3
CODE_INDENT = 4

FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
ATX_HEADING_RE = re.compile(r"^ {0,3}#{1,6}(?:\s|$)")
SETEXT_UNDERLINE_RE = re.compile(r"^ {0,3}(?:=+|-+)\s*$")
THEMATIC_BREAK_RE = re.compile(r"^ {0,3}([-*_])[ \t]*(?:\1[ \t]*){2,}$")
LIST_ITEM_RE = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])([ \t]+)(\S.*)$")
BLOCKQUOTE_RE = re.compile(r"^ {0,3}>")
HTML_BLOCK_RE = re.compile(r"^ {0,3}<")
REFERENCE_DEF_RE = re.compile(r"^ {0,3}\[[^\]]+\]:\s")
TABLE_CELL_RE = re.compile(r"(?<!\\)\|")
HARD_BREAK_RE = re.compile(r"(?:[ \t]{2,}|\\)$")

# 中文、日文、韩文、全角字符等宽字符区段。
WIDE_RE = re.compile(
    "["
    "ᄀ-ᅟ"  # 韩文字母
    "⺀-〾"  # CJK 部首与标点
    "ぁ-㏿"  # 假名、注音、兼容字符
    "㐀-䶿"  # CJK 扩展 A
    "一-鿿"  # CJK 基本区
    "ꀀ-꓏"  # 彝文
    "가-힣"  # 韩文音节
    "豈-﫿"  # CJK 兼容表意文字
    "︰-﹏"  # CJK 兼容形式
    "＀-￦"  # 全角形式
    "]"
)
# 全角标点，两侧不再补空格。
WIDE_PUNCT_RE = re.compile(
    "["
    "‘’“”"  # 弯引号
    "—…"  # 破折号、省略号
    "、。"  # 顿号、句号
    "《-】"  # 书名号、方头括号、直角引号
    "〔-〟"  # 六角括号等
    "！（），：；？～｟｠"
    "]"
)
# 拼接时需要跳过的行内标记，取到真正的可见字符再判断留白。
EMPHASIS_CHARS = set("*_~`")
HEAD_SKIP_CHARS = EMPHASIS_CHARS | {"["}
# 行尾的链接目标不参与留白判断，取链接文字的最后一个字符。
LINK_TAIL_RE = re.compile(r"\]\((?:[^()\s]*)(?:\s+\"[^\"]*\")?\)$")
ASCII_TRAILING_NO_SPACE = set("([{<-/@#$&+=\\\"'")
ASCII_LEADING_NO_SPACE = set(",.;:!?)]}>%\"'")


@dataclass(frozen=True)
class Join:
    """一处段落硬换行，用于命令行输出。"""

    file: Path
    line: int
    merged: int
    preview: str


def is_wide(char: str) -> bool:
    return bool(WIDE_RE.match(char)) or bool(WIDE_PUNCT_RE.match(char))


def is_wide_punct(char: str) -> bool:
    return bool(WIDE_PUNCT_RE.match(char))


def effective_tail(text: str) -> str:
    text = LINK_TAIL_RE.sub("", text)
    index = len(text) - 1
    while index >= 0 and text[index] in EMPHASIS_CHARS:
        index -= 1
    return text[index] if index >= 0 else ""


def effective_head(text: str) -> str:
    index = 0
    while index < len(text) and text[index] in HEAD_SKIP_CHARS:
        index += 1
    return text[index] if index < len(text) else ""


def joiner(left: str, right: str) -> str:
    """判断两行拼接处是否需要一个空格。"""
    tail, head = effective_tail(left), effective_head(right)
    if not tail or not head:
        return ""

    if is_wide(tail) or is_wide(head):
        if is_wide(tail) and is_wide(head):
            return ""
        if is_wide_punct(tail) or is_wide_punct(head):
            return ""
        return " "
    if tail in ASCII_TRAILING_NO_SPACE or head in ASCII_LEADING_NO_SPACE:
        return ""
    return " "


def indent_width(line: str) -> int:
    width = 0
    for char in line:
        if char == " ":
            width += 1
        elif char == "\t":
            width += CODE_INDENT
        else:
            break
    return width


def visual_width(text: str) -> int:
    """按列宽计算前缀长度，用于判断续行缩进。"""
    return sum(CODE_INDENT if char == "\t" else 1 for char in text)


def is_structural(line: str) -> bool:
    """标题、表格、分隔线、引用块等结构行不参与拼接。"""
    return bool(
        ATX_HEADING_RE.match(line)
        or SETEXT_UNDERLINE_RE.match(line)
        or THEMATIC_BREAK_RE.match(line)
        or BLOCKQUOTE_RE.match(line)
        or HTML_BLOCK_RE.match(line)
        or REFERENCE_DEF_RE.match(line)
        or TABLE_CELL_RE.search(line)
    )


class _Block:
    """正在拼接的段落或列表项。"""

    def __init__(self, prefix: str, text: str, start_line: int) -> None:
        self.prefix = prefix
        self.text = text
        self.start_line = start_line
        # 正文起始列，续行缩进和缩进代码块的判断都以它为基准。
        self.content_indent = visual_width(prefix)
        self.joined_lines: list[int] = []

    def append(self, line_no: int, content: str) -> None:
        self.text += joiner(self.text, content) + content
        self.joined_lines.append(line_no)

    @property
    def merged(self) -> int:
        return len(self.joined_lines)

    def render(self, suffix: str = "") -> str:
        return f"{self.prefix}{self.text}{suffix}"


def unwrap_text(text: str) -> tuple[str, list[tuple[int, int, str]]]:
    """返回展开后的文本，以及每处硬换行的起始行号、合并行数和预览。"""
    lines = text.splitlines()
    output: list[str] = []
    joins: list[tuple[int, int, str]] = []
    block: _Block | None = None
    fence_delimiter: str | None = None
    in_front_matter = bool(lines and lines[0].strip() == "---")
    list_content_indent: int | None = None
    after_hard_break = False

    def close(current: _Block, suffix: str = "") -> None:
        rendered = current.render(suffix)
        output.append(rendered)
        if current.joined_lines:
            joins.append((current.start_line, current.merged, rendered.strip()[:60]))

    def flush() -> None:
        nonlocal block
        if block is not None:
            close(block)
            block = None

    for line_no, raw in enumerate(lines, start=1):
        if in_front_matter:
            output.append(raw)
            if line_no != 1 and raw.strip() in {"---", "..."}:
                in_front_matter = False
            continue

        fence_match = FENCE_RE.match(raw)
        if fence_match:
            delimiter = fence_match.group(1)
            if fence_delimiter is None:
                flush()
                fence_delimiter = delimiter
            elif delimiter[0] == fence_delimiter[0] and len(delimiter) >= len(
                fence_delimiter
            ):
                fence_delimiter = None
            output.append(raw)
            continue

        if fence_delimiter is not None:
            output.append(raw)
            continue

        if not raw.strip():
            flush()
            after_hard_break = False
            output.append(raw)
            continue

        list_match = LIST_ITEM_RE.match(raw)
        if list_match:
            flush()
            after_hard_break = False
            indent, marker, spaces, content = list_match.groups()
            prefix = f"{indent}{marker}{spaces}"
            block = _Block(prefix, content.rstrip(), line_no)
            list_content_indent = block.content_indent
        elif is_structural(raw):
            flush()
            after_hard_break = False
            list_content_indent = None
            output.append(raw)
            continue
        elif block is not None:
            if indent_width(raw) > block.content_indent + CONTINUATION_SLACK:
                # 比正文缩进深太多，按缩进代码或嵌套结构处理。
                flush()
                output.append(raw)
                continue
            block.append(line_no, raw.strip())
        else:
            code_indent = CODE_INDENT
            if list_content_indent is not None:
                code_indent += list_content_indent
            if indent_width(raw) >= code_indent and not after_hard_break:
                output.append(raw)
                continue
            after_hard_break = False
            leading = raw[: len(raw) - len(raw.lstrip())]
            block = _Block(leading, raw.strip(), line_no)

        if block is not None:
            hard_break = HARD_BREAK_RE.search(raw)
            if hard_break:
                # 行尾两个空格或反斜杠是显式换行，保留原样并结束当前拼接。
                suffix = hard_break.group(0)
                if block.text.endswith(suffix):
                    block.text = block.text[: -len(suffix)]
                close(block, suffix)
                block = None
                after_hard_break = True

    flush()

    result = "\n".join(output)
    if text.endswith("\n"):
        result += "\n"
    return result, joins


def collect_targets(raw_targets: list[str]) -> list[Path]:
    targets: list[Path] = []
    for item in raw_targets:
        path = Path(item)
        if not path.exists():
            print(f"[WARN] 文件不存在，已跳过: {item}", file=sys.stderr)
            continue
        if path.is_dir():
            for markdown in sorted(path.rglob("*.md")):
                if any(part in SKIP_DIR_NAMES for part in markdown.parts):
                    continue
                targets.append(markdown)
        else:
            targets.append(path)

    return sorted(
        {path.resolve(): path for path in targets}.values(),
        key=lambda path: str(path),
    )


def process_file(path: Path) -> tuple[str, list[Join]]:
    original = path.read_text(encoding="utf-8")
    if FILE_IGNORE_MARKER in original:
        return original, []
    unwrapped, joins = unwrap_text(original)
    return unwrapped, [
        Join(file=path, line=line, merged=merged, preview=preview)
        for line, merged, preview in joins
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="展开中文 Markdown 段落的硬换行")
    parser.add_argument(
        "files",
        nargs="*",
        help="要处理的 Markdown 文件或目录；为空时处理当前目录",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只报告段落硬换行，不写回文件；发现问题时返回 2",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="把结果打印到标准输出，不写回文件",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    targets = collect_targets(args.files if args.files else DEFAULT_TARGETS)
    if not targets:
        print("未找到可处理的 Markdown 文件。", file=sys.stderr)
        return 1

    findings: list[Join] = []
    changed: list[Path] = []

    for path in targets:
        unwrapped, joins = process_file(path)
        if args.stdout:
            print(unwrapped, end="")
            continue
        if not joins:
            continue
        findings.extend(joins)
        changed.append(path)
        if not args.check:
            path.write_text(unwrapped, encoding="utf-8")

    if args.stdout:
        return 0

    if not findings:
        print(f"PASS: 共检查 {len(targets)} 个文件，段落未发现硬换行。")
        return 0

    for item in findings:
        print(
            f"- {item.file}:{item.line} 段落硬换行，"
            f"共 {item.merged + 1} 行\n  {item.preview}"
        )

    if args.check:
        print(
            f"FAIL: 共检查 {len(targets)} 个文件；"
            f"{len(changed)} 个文件存在段落硬换行，共 {len(findings)} 处。"
        )
        return 2

    print(
        f"DONE: 共检查 {len(targets)} 个文件；"
        f"已展开 {len(changed)} 个文件，共 {len(findings)} 处硬换行。"
    )
    print("请复查拼接边界的中西文留白，并确认列表、表格和代码结构未受影响。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
