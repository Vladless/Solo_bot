import re


MENU_TEXT_EM = 15.6
QUOTE_TEXT_EM = 14.8

MONO_LINE_LIMIT = 32
LABEL_COLUMN = 10

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


RULE_CHAR_EM = 0.52

# Широкий экран для редакторов: в ряду несколько кнопок, и подписям нужна ширина.
WIDE_TEXT_EM = 26.0
WIDE_QUOTE_TEXT_EM = 25.0

MENU_RULE = "_" * int(MENU_TEXT_EM / RULE_CHAR_EM)
WIDE_MENU_RULE = "_" * int(WIDE_TEXT_EM / RULE_CHAR_EM)


def menu_title(title: str, wide: bool = False) -> str:
    """Возвращает заголовок экрана с подчёркивающей линией."""
    return f"<b>{title}</b>\n{WIDE_MENU_RULE if wide else MENU_RULE}"


_TREE_CHARS = ("├", "└", "│")


def branch(text: str, last: bool = False) -> str:
    """Ставит строке соединитель дерева, если она не несёт свой."""
    if text.lstrip().startswith(_TREE_CHARS):
        return text
    return f"{'└' if last else '├'} {text}"


def _table(rows: list[str]) -> str | None:
    """Выравнивает строки «метка: значение» в колонки, если они влезают."""
    if len(rows) < 2:
        return None
    pairs = [row.split(": ", 1) for row in rows]
    if not all(len(pair) == 2 and pair[0] and "<" not in pair[0] for pair in pairs):
        return None
    labels = [branch(label, index == len(pairs) - 1) for index, (label, _) in enumerate(pairs)]
    width = max(LABEL_COLUMN + 2, max(len(label) for label in labels))
    aligned = [f"{label.ljust(width)}  {value}" for label, (_, value) in zip(labels, pairs, strict=True)]
    if max(len(row) for row in aligned) > MONO_LINE_LIMIT:
        return None
    return "\n".join(aligned)


_CODE_BLOCK_RE = re.compile(r"<code>(.*?)</code>", re.S)


_COLUMN_GAP_RE = re.compile(r"\s{2,}")


def _split_pair(row: str) -> tuple[str, str] | None:
    """Разбирает строку таблицы на метку и значение: и «метка: значение», и уже выравненную."""
    label, sep, value = row.partition(": ")
    if sep and label.strip() and "<" not in label:
        return label, value

    parts = _COLUMN_GAP_RE.split(row.strip(), maxsplit=1)
    if len(parts) == 2 and parts[0] and parts[1] and "<" not in parts[0]:
        return parts[0], parts[1]
    return None


def align_screen(text: str) -> str:
    """Выравнивает значения во всех таблицах экрана по одной вертикали. Метка и значение
    идут отдельными моноблоками: Telegram копирует по нажатию ровно одно значение,
    а не всю таблицу. Разделяющий пробел один на строку, поэтому колонки не разъезжаются."""
    blocks: list[list[str]] = []
    for body in _CODE_BLOCK_RE.findall(text):
        rows = [row for row in body.split("\n") if row.strip()]
        pairs = [_split_pair(row) for row in rows]
        blocks.append(rows if rows and all(pairs) else [])

    def cells_of(rows: list[str]) -> list[tuple[str, str]]:
        return [
            (branch(_split_pair(row)[0], index == len(rows) - 1), _split_pair(row)[1])
            for index, row in enumerate(rows)
        ]

    aligned = [rows for rows in blocks if rows]
    width = 0
    while aligned:
        cells = [cell for rows in aligned for cell in cells_of(rows)]
        width = max(LABEL_COLUMN + 2, max(len(label) for label, _ in cells))
        if max(width + 2 + len(value) for _, value in cells) <= MONO_LINE_LIMIT:
            break
        widest = max(aligned, key=lambda rows: max(len(label) for label, _ in cells_of(rows)))
        aligned.remove(widest)

    order = iter(blocks)

    def render(match: re.Match) -> str:
        rows = next(order)
        if not rows:
            return match.group(0)
        if rows not in aligned:
            lines = [branch(row, index == len(rows) - 1) for index, row in enumerate(rows)]
            return "<code>" + "\n".join(lines) + "</code>"
        lines = []
        for index, row in enumerate(rows):
            label, value = _split_pair(row)
            cell = branch(label, index == len(rows) - 1).ljust(width)
            lines.append(f"<code>{cell}</code> <code>{value}</code>")
        return "\n".join(lines)

    return _CODE_BLOCK_RE.sub(render, text)


def quote(*paragraphs: str, wide: bool = False) -> str:
    """Оборачивает абзацы в цитату; пары «метка: значение» выстраивает таблицей."""
    width = WIDE_QUOTE_TEXT_EM if wide else QUOTE_TEXT_EM
    blocks = []
    for paragraph in paragraphs:
        if not paragraph:
            continue
        table = _table([line for line in paragraph.split("\n") if line])
        blocks.append(f"<code>{table}</code>" if table else wrap_text(paragraph, width))
    return "<blockquote>" + "\n\n".join(blocks) + "</blockquote>" if blocks else ""


def table_text(*lines: str) -> str:
    """Возвращает таблицу строк «метка: значение» без обёрток: выравненную или деревом."""
    rows = [_TAG_RE.sub("", line) if line.startswith("<code>") else line for line in lines if line]
    if not rows:
        return ""
    table = _table(rows)
    if table is not None:
        return table
    if len(rows) == 1:
        return rows[0]
    return "\n".join(branch(row, index == len(rows) - 1) for index, row in enumerate(rows))


def section(title: str, *lines: str) -> str:
    """Возвращает подписанный блок данных: заголовок и таблица под ним."""
    table = table_text(*lines)
    if not table:
        return ""
    return f"<b>{title}</b>\n<blockquote><code>{table}</code></blockquote>"


def note(title: str, *paragraphs: str) -> str:
    """Возвращает подписанный блок прозы: заголовок и цитата под ним."""
    body = quote(*paragraphs)
    return f"<b>{title}</b>\n{body}" if body else ""


def card(*sections: str) -> str:
    """Собирает секции подряд и выравнивает значения всех таблиц по одной вертикали."""
    return align_screen("\n".join(block for block in sections if block))


def menu_text(title: str, *paragraphs: str, markup=None, wide: bool = False) -> str:
    """Собирает текст экрана: заголовок и абзацы. Значения таблиц выравниваются по одной вертикали.

    wide — экран-редактор: подчёркивание длиннее, поэтому пузырь и клавиатура шире,
    и подписи кнопок в многокнопочных рядах не обрезаются.
    """
    body = [wrap_text(p, WIDE_TEXT_EM if wide else MENU_TEXT_EM) for p in paragraphs if p]

    return align_screen("\n\n".join([menu_title(title, wide), *body]))


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
