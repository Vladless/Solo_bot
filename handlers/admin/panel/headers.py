import re


MENU_TITLE_EM = 15.0
MENU_TITLE_MAX = 24.0
MENU_TEXT_EM = 15.6
QUOTE_TEXT_EM = 14.8

MENU_DASHES = 2
MONO_LINE_LIMIT = 28

_NARROW = set("iljtfrI.,:;!'`|/\\()[]{}")
_WIDE_LAT = set("mwMW")
_WIDE_CYR = set("мжшщюфыъМЖШЩЮФЫЪ")
_CYR = set("абвгдеёзийклнопрстухцчьэяАБВГДЕЁЗИЙКЛНОПРСТУХЦЧЬЭЯ")

_TAG_RE = re.compile(r"<[^>]+>")
_MASK_RE = re.compile("\x00(\\d+)\x00")
_PRE_RE = re.compile(r"<pre>.*?</pre>|<code>.*?</code>", re.S)


def char_width(char: str) -> float:
    """Возвращает ширину символа в долях em."""
    if char == " ":
        return 0.26
    if char == "-":
        return 0.33
    if char in _NARROW:
        return 0.30
    if char in _WIDE_LAT:
        return 0.88
    if char in _WIDE_CYR:
        return 0.82
    if char.isdigit():
        return 0.56
    if char in _CYR:
        return 0.68 if char.isupper() else 0.56
    if char.isascii() and char.isalpha():
        return 0.66 if char.isupper() else 0.56
    if ord(char) > 0x2000:
        return 1.15
    return 0.56


def text_width(text: str) -> float:
    """Возвращает ширину строки в em без учёта html-тегов."""
    return sum(char_width(char) for char in _TAG_RE.sub("", text))


def strip_tags(text: str) -> str:
    """Возвращает текст без html-тегов."""
    return _TAG_RE.sub("", text)


def content_width(*blocks: str) -> float:
    """Возвращает ширину самой длинной строки содержимого в em."""
    lines = [line for block in blocks if block for line in block.split("\n")]
    return max((text_width(line) for line in lines), default=0.0)


def wrap_text(text: str, width: float = MENU_TEXT_EM) -> str:
    """Переносит текст в колонку заданной ширины, не разрывая теги и моноширинные блоки."""
    if _PRE_RE.search(text):
        parts = _PRE_RE.split(text)
        blocks = _PRE_RE.findall(text)
        out = [wrap_text(parts[0], width)]
        for block, tail in zip(blocks, parts[1:], strict=False):
            out += [block, wrap_text(tail, width)]
        return "".join(out)

    tags: list[str] = []

    def hide(match: re.Match) -> str:
        tags.append(match.group(0))
        return f"\x00{len(tags) - 1}\x00"

    space = char_width(" ")
    result: list[str] = []
    for paragraph in _TAG_RE.sub(hide, text).split("\n"):
        if _MASK_RE.sub("", paragraph).lstrip().startswith("--"):
            result.append(paragraph)
            continue
        line: list[str] = []
        size = 0.0
        for word in paragraph.split(" "):
            length = text_width(_MASK_RE.sub("", word))
            if line and size + space + length > width:
                result.append(" ".join(line))
                line, size = [word], length
                continue
            size += length + (space if line else 0)
            line.append(word)
        result.append(" ".join(line))
    return _MASK_RE.sub(lambda m: tags[int(m.group(1))], "\n".join(result))


def keyboard_width(markup) -> float:
    """Возвращает ширину клавиатуры в em."""
    rows = getattr(markup, "inline_keyboard", None) or []
    widths = [len(row) * max(text_width(button.text) for button in row) for row in rows if row]
    return max(widths, default=0.0)


def menu_title(title: str, width: float = 0.0) -> str:
    """Возвращает заголовок, добитый чёрточками до общей ширины меню."""
    target = min(max(MENU_TITLE_EM, width), MENU_TITLE_MAX)
    dash, space = char_width("-"), char_width(" ")
    best = None
    for gap in (1, 2):
        free = target - text_width(title) - 2 * gap * space
        dashes = int(free / (2 * dash) + 1e-6)
        if dashes < MENU_DASHES:
            continue
        rest = free - 2 * dashes * dash
        if best is None or rest < best[0]:
            best = (rest, dashes, gap)

    if best is None:
        return f"<b>{title}</b>"

    _, dashes, gap = best
    line = "-" * dashes
    pad = " " * gap
    return f"<b>{line}{pad}{title}{pad}{line}</b>"


def _table(rows: list[str]) -> str | None:
    """Выравнивает строки «метка: значение» в колонки, если они влезают."""
    if len(rows) < 2:
        return None
    pairs = [row.split(": ", 1) for row in rows]
    if not all(len(pair) == 2 and pair[0] and "<" not in pair[0] for pair in pairs):
        return None
    width = max(len(label) for label, _ in pairs)
    aligned = [f"{label.ljust(width)}  {value}" for label, value in pairs]
    if max(len(row) for row in aligned) + 2 > MONO_LINE_LIMIT:
        return None
    return "\n".join(f"├ {row}" for row in aligned[:-1]) + f"\n└ {aligned[-1]}"


def quote(*paragraphs: str) -> str:
    """Оборачивает абзацы в цитату; пары «метка: значение» выстраивает таблицей."""
    blocks = []
    for paragraph in paragraphs:
        if not paragraph:
            continue
        table = _table([line for line in paragraph.split("\n") if line])
        blocks.append(f"<code>{table}</code>" if table else wrap_text(paragraph, QUOTE_TEXT_EM))
    return "<blockquote>" + "\n\n".join(blocks) + "</blockquote>" if blocks else ""


def section(title: str, *lines: str) -> str:
    """Возвращает подписанный блок данных: заголовок и таблица под ним."""
    rows = [_TAG_RE.sub("", line) if line.startswith("<code>") else line for line in lines if line]
    if not rows:
        return ""
    table = _table(rows)
    if table is None:
        table = "\n".join(f"├ {row}" for row in rows[:-1]) + f"\n└ {rows[-1]}" if len(rows) > 1 else rows[0]
    return f"<b>{title}</b>\n<blockquote><code>{table}</code></blockquote>"


def note(title: str, *paragraphs: str) -> str:
    """Возвращает подписанный блок прозы: заголовок и цитата под ним."""
    body = quote(*paragraphs)
    return f"<b>{title}</b>\n{body}" if body else ""


def card(*sections: str) -> str:
    """Собирает секции подряд: у каждой свой заголовок и своя цитата."""
    return "\n".join(block for block in sections if block)


def menu_text(title: str, *paragraphs: str, markup=None) -> str:
    """Собирает текст экрана: заголовок и абзацы. Секции идут вплотную друг к другу."""
    body = [wrap_text(p) for p in paragraphs if p]
    width = max(content_width(*body), keyboard_width(markup) if markup is not None else 0.0)

    return "\n\n".join([menu_title(title, width), *body])


def render_version_block(version_text: str | None) -> str:
    """Возвращает версию бота цитатой."""
    if not version_text:
        return ""
    parts = [p.strip() for p in str(version_text).split("\n") if p.strip()]
    if not parts:
        return ""
    version = parts[0]
    note = " ".join(parts[1:]).strip("()") if len(parts) > 1 else ""
    inner = f"<code>{version}</code>"
    if note:
        inner = f"{inner}\n{note}"
    return f"<blockquote>{inner}</blockquote>"


def render_panel_text(version_text: str | None) -> str:
    """Возвращает текст главного экрана админки."""
    lines = [menu_title("Панель администратора")]
    block = render_version_block(version_text)
    if block:
        lines += ["", "Версия бота:", block]
    return "\n".join(lines)
