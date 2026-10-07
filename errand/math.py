"""Bounded parser for a small, non-executable TeX math subset."""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Formula:
    kind: str
    text: str = ""
    children: tuple = ()

    def readable(self):
        parts = [child.readable() for child in self.children]
        if self.kind == "fraction":
            return f"（{parts[0]}）割る（{parts[1]}）"
        if self.kind == "root":
            return f"平方根（{parts[0]}）"
        if self.kind == "sup":
            return f"（{parts[0]}）の（{parts[1]}）乗"
        if self.kind == "sub":
            return f"{parts[0]}、下付き（{parts[1]}）"
        return self.text or "".join(parts)


SYMBOLS = {"times": "×", "cdot": "·", "div": "÷", "pm": "±", "mp": "∓",
           "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥", "ne": "≠", "neq": "≠",
           "pi": "π", "theta": "θ", "alpha": "α", "beta": "β", "infty": "∞"}


def parse_formula(source):
    if len(source) > 2048:
        raise ValueError("数式が長すぎます。")
    position, count = 0, 0

    def node(kind, text="", children=()):
        nonlocal count
        count += 1
        if count > 512:
            raise ValueError("数式が複雑すぎます。")
        return Formula(kind, text, tuple(children))

    def skip():
        nonlocal position
        while position < len(source) and source[position].isspace():
            position += 1

    def atom(depth):
        nonlocal position
        if depth > 24:
            raise ValueError("数式の入れ子が深すぎます。")
        skip()
        if position == len(source):
            raise ValueError("数式が途中です。")
        char = source[position]
        position += 1
        if char == "{":
            result = sequence(depth + 1)
            if position == len(source) or source[position] != "}":
                raise ValueError("閉じ括弧がありません。")
            position += 1
            return result
        if char in "}^_$":
            raise ValueError("未対応の数式です。")
        if char == "\\":
            match = re.match(r"[A-Za-z]+|.", source[position:])
            if not match:
                raise ValueError("数式が途中です。")
            command = match[0]
            position += len(command)
            if command in SYMBOLS:
                return node("text", SYMBOLS[command])
            if command in {",", ";", " ", "!"}:
                return node("text", " " if command != "!" else "")
            if command in {"{", "}", "%", "_"}:
                return node("text", command)
            if command in {"frac", "dfrac", "tfrac"}:
                return node("fraction", children=(atom(depth + 1), atom(depth + 1)))
            if command == "sqrt":
                skip()
                if source[position:position + 1] == "[":
                    raise ValueError("添字付きの根号には対応していません。")
                return node("root", children=(atom(depth + 1),))
            if command in {"boxed", "mathrm", "text"}:
                return node("box" if command == "boxed" else "row", children=(atom(depth + 1),))
            raise ValueError("未対応の記法: \\" + command)
        return node("text", char)

    def sequence(depth):
        nonlocal position
        parts = []
        skip()
        while position < len(source) and source[position] != "}":
            part = atom(depth)
            skip()
            used = set()
            while position < len(source) and source[position] in "^_":
                operator = source[position]
                if operator in used:
                    raise ValueError("上付き・下付きが重複しています。")
                used.add(operator)
                position += 1
                part = node("sup" if operator == "^" else "sub", children=(part, atom(depth + 1)))
                skip()
            parts.append(part)
        return node("row", children=parts)

    result = sequence(0)
    if position != len(source) or not result.children:
        raise ValueError("数式を解釈できません。")
    return result
