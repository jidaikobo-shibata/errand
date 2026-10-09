"""Render a deliberately small Markdown subset as escaped Pango markup.

No HTML, scripts, embedded images, or automatic external resource loading.
"""

from html import escape
from dataclasses import dataclass
from pathlib import Path
import re
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Block:
    kind: str
    text: str
    language: str = ""
    complete: bool = True
    rows: tuple = ()
    alignments: tuple = ()


def blocks(source):
    """Split fenced code from prose, keeping code whitespace exactly as received."""
    result, prose, code = [], [], []
    fence = None
    language = ""
    for line in source.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line.rstrip("\r\n"))
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                result.append(Block("code", "".join(code), language))
                fence, code = None, []
            else:
                code.append(line)
        elif marker:
            if prose:
                result.extend(prose_blocks("".join(prose)))
                prose = []
            fence, language = marker[1], marker[2].strip()
        else:
            prose.append(line)
    if fence:
        result.append(Block("code", "".join(code), language, complete=False))
    elif prose:
        result.extend(prose_blocks("".join(prose)))
    return result


def table_cells(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    cells, cell, escaped, fence = [], [], False, 0
    index = 0
    while index < len(line):
        char = line[index]
        if escaped:
            cell.append(char if char == "|" else "\\" + char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "`":
            end = index
            while end < len(line) and line[end] == "`":
                end += 1
            count = end - index
            fence = 0 if fence == count else count if not fence else fence
            cell.append(line[index:end])
            index = end - 1
        elif char == "|" and not fence:
            cells.append("".join(cell).strip())
            cell = []
        else:
            cell.append(char)
        index += 1
    if escaped:
        cell.append("\\")
    return cells + ["".join(cell).strip()]


def math_blocks(source):
    """Find math delimiters outside inline code; preserve unfinished displays."""
    result, start, position = [], 0, 0
    while position < len(source):
        if source[position] == "`":
            fence = re.match(r"`+", source[position:])[0]
            end = source.find(fence, position + len(fence))
            position = len(source) if end < 0 else end + len(fence)
            continue
        opener = next((mark for mark in (r"\[", r"\(", "$$", "$")
                       if source.startswith(mark, position)), None)
        if not opener or (position and source[position - 1] == "\\"):
            position += 1
            continue
        closer = {r"\[": r"\]", r"\(": r"\)", "$$": "$$", "$": "$"}[opener]
        begin = position + len(opener)
        end = source.find(closer, begin)
        while end >= 0 and closer.startswith("$") and source[end - 1] == "\\":
            end = source.find(closer, end + len(closer))
        display = opener in (r"\[", "$$")
        if end < 0 and not display:
            position = begin
            continue
        body = source[begin:end if end >= 0 else len(source)]
        # Single dollar pairs containing prose/currency are not math.
        if not display and (not body.strip() or "\n" in body or
                            (opener == "$" and not re.search(r"[\\^_={}+*/-]", body))):
            position = begin
            continue
        if start < position:
            result.append(Block("text", source[start:position]))
        result.append(Block("math", body.strip(), complete=end >= 0))
        position = len(source) if end < 0 else end + len(closer)
        start = position
    if start < len(source):
        result.append(Block("text", source[start:]))
    return result


def prose_blocks(source):
    lines = source.splitlines(keepends=True)
    result, prose = [], []
    index = 0
    while index < len(lines):
        if re.match(r"^ {0,3}>", lines[index]):
            if prose:
                result.extend(math_blocks("".join(prose)))
                prose = []
            quoted = []
            while index < len(lines) and (quote := re.match(r"^ {0,3}>[ \t]?(.*)$", lines[index].rstrip("\r\n"))):
                ending = lines[index][len(lines[index].rstrip("\r\n")):]
                quoted.append(quote[1] + ending)
                index += 1
            result.append(Block("quote", "".join(quoted)))
            continue
        header = table_cells(lines[index])
        separators = table_cells(lines[index + 1]) if index + 1 < len(lines) else []
        if ("|" in lines[index] and len(header) == len(separators)
                and all(re.fullmatch(r":?-{3,}:?", cell) for cell in separators)):
            if prose:
                result.extend(math_blocks("".join(prose)))
                prose = []
            start = index
            rows = [tuple(header)]
            index += 2
            while index < len(lines) and lines[index].strip() and "|" in lines[index]:
                cells = table_cells(lines[index])
                rows.append(tuple((cells + [""] * len(header))[:len(header)]))
                index += 1
            alignments = tuple("center" if cell.startswith(":") and cell.endswith(":")
                               else "right" if cell.endswith(":") else "left" for cell in separators)
            result.append(Block("table", "".join(lines[start:index]), rows=tuple(rows), alignments=alignments))
        else:
            prose.append(lines[index])
            index += 1
    if prose:
        result.extend(math_blocks("".join(prose)))
    return result


def link_target(target):
    target = target.strip()
    if target.startswith("/"):
        return Path(target).as_uri()
    try:
        parsed = urlsplit(target)
        if parsed.scheme in {"https", "http"} and parsed.netloc:
            return target
        if parsed.scheme == "file" and parsed.netloc in {"", "localhost"} and parsed.path.startswith("/"):
            return target
    except ValueError:
        pass
    return None


def inline(text, depth=0):
    if depth > 8:
        return escape(text)
    output = []
    position = 0
    while position < len(text):
        rest = text[position:]
        if rest.startswith("\\") and len(rest) > 1 and rest[1] in r"\`*_{}[]()#+-.!>":
            output.append(escape(rest[1]))
            position += 2
            continue
        code = re.match(r"(`+)(.+?)\1(?!`)", rest)
        if code:
            output.append('<span font_family="monospace">' + escape(code[2]) + '</span>')
            position += code.end()
            continue
        link = re.match(r"(!?)\[([^\]\n]+)\]\((<[^>\n]+>|[^\s)]+)(?:\s+\"[^\"]*\")?\)", rest)
        if link:
            target = link_target(link[3].removeprefix("<").removesuffix(">"))
            title = inline(link[2], depth + 1)
            if target and not link[1]:
                output.append(f'<a href="{escape(target, quote=True)}">{title}</a>')
            else:
                output.append(title)
            position += link.end()
            continue
        auto = re.match(r"<(https?://[^<>\s]+)>", rest)
        if auto and link_target(auto[1]):
            output.append(f'<a href="{escape(auto[1], quote=True)}">{escape(auto[1])}</a>')
            position += auto.end()
            continue
        matched = False
        for delimiter, tag in [("**", "b"), ("__", "b"), ("~~", "s"), ("*", "i"), ("_", "i")]:
            if not rest.startswith(delimiter):
                continue
            if delimiter == "_" and position and text[position - 1].isalnum():
                continue
            end = rest.find(delimiter, len(delimiter))
            if end > len(delimiter) and not rest[len(delimiter)].isspace():
                output.append(f'<{tag}>' + inline(rest[len(delimiter):end], depth + 1) + f'</{tag}>')
                position += end + len(delimiter)
                matched = True
                break
        if matched:
            continue
        output.append(escape(text[position]))
        position += 1
    return "".join(output)


def render(source):
    lines = source.split("\n")
    output = []
    fence = None
    code_lines = []
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                output.append('<span font_family="monospace">' + escape("\n".join(code_lines)) + '</span>')
                fence, code_lines = None, []
            else:
                code_lines.append(line)
            continue
        if marker:
            fence = marker[1]
            continue
        heading = re.match(r"^ {0,3}(#{1,6})\s+(.+?)(?:\s+#+)?$", line)
        if heading:
            sizes = ["xx-large", "x-large", "large", "medium", "medium", "medium"]
            output.append(f'<span weight="bold" size="{sizes[len(heading[1]) - 1]}">' + inline(heading[2]) + '</span>')
        elif index < len(lines) and line.strip() and re.fullmatch(r" {0,3}(=+|-+)\s*", lines[index]):
            size = "xx-large" if lines[index].strip().startswith("=") else "x-large"
            output.append(f'<span weight="bold" size="{size}">' + inline(line) + '</span>')
            index += 1
        elif re.fullmatch(r" {0,3}(\*\s*){3,}| {0,3}(-\s*){3,}| {0,3}(_\s*){3,}", line):
            output.append("────────────")
        elif (item := re.match(r"^(\s*)(?:[-+*]|(\d+)[.)])\s+(.+)$", line)):
            prefix = (item[2] + ".") if item[2] else "•"
            output.append(item[1] + prefix + " " + inline(item[3]))
        elif (quote := re.match(r"^ {0,3}>\s?(.*)$", line)):
            output.append("│ " + inline(quote[1]))
        else:
            output.append(inline(line))
    if fence:
        # Render unfinished fences as code while a response is streaming.
        output.append('<span font_family="monospace">' + escape("\n".join(code_lines)) + '</span>')
    return "\n".join(output)
