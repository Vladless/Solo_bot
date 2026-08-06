def render_version_block(version_text: str | None) -> str:
    """Версия цитатой; сам номер в <code>, чтобы копировался тапом."""
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
    lines = ["<b>--- Панель администратора ---</b>"]
    block = render_version_block(version_text)
    if block:
        lines += ["", "Версия бота:", block]
    return "\n".join(lines)
