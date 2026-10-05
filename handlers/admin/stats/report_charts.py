import io

from logger import logger


_BG = (13, 17, 23)
_PANEL = (22, 27, 34)
_GRID = (48, 54, 61)
_TEXT = (201, 209, 217)
_MUTED = (139, 148, 158)


def _font(size: int):
    from PIL import ImageFont

    candidates = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
    )
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


def _fmt_value(value: float) -> str:
    if value >= 1000:
        return f"{value / 1000:.1f}k".replace(".0k", "k")
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.0f}"


def _nice_max(value: float) -> float:
    if value <= 0:
        return 1.0
    import math

    exp = math.floor(math.log10(value))
    base = 10**exp
    for mult in (1, 2, 2.5, 5, 10):
        candidate = mult * base
        if candidate >= value:
            return candidate
    return 10 * base


def chart_legend(panels: list[dict]) -> str:
    names = [str(p.get("name", "")).strip() for p in panels]
    names = [n for n in names if n]
    if not names:
        return ""
    return "Сверху вниз: " + " · ".join(names)


def render_stats_chart(x_labels: list[str], panels: list[dict]) -> io.BytesIO | None:
    """Рисует вертикальные бар-чарты (по панели на метрику) и возвращает PNG в BytesIO.

    panels: [{"name": str, "color": (r,g,b), "values": [float, ...]}]
    Названия панелей рисуются на картинке системным шрифтом (DejaVu/Liberation/Arial).
    """
    try:
        from PIL import Image, ImageDraw

        n = max(1, len(x_labels))
        slot = 44 if n <= 16 else (32 if n <= 24 else 26)
        left_pad, right_pad, top_pad = 58, 22, 16
        plot_h = 150
        title_h = 24
        panel_gap = 18
        axis_h = 28

        plot_w = n * slot
        width = left_pad + right_pad + plot_w
        panel_h = title_h + plot_h
        height = top_pad + len(panels) * (panel_h + panel_gap) + axis_h

        img = Image.new("RGB", (width, height), _BG)
        draw = ImageDraw.Draw(img)
        f_small = _font(15)
        f_title = _font(17)

        show_values = n <= 16
        if n <= 16:
            label_step = 1
        elif n <= 24:
            label_step = 2
        else:
            label_step = 3

        bar_w = int(slot * 0.6)
        bar_off = (slot - bar_w) // 2

        last_baseline = top_pad
        for p_idx, panel in enumerate(panels):
            values = panel.get("values") or [0] * n
            color = panel.get("color", (88, 166, 255))
            name = str(panel.get("name", ""))
            panel_top = top_pad + p_idx * (panel_h + panel_gap)
            plot_top = panel_top + title_h
            baseline = plot_top + plot_h
            last_baseline = baseline

            draw.rounded_rectangle(
                [left_pad - 8, plot_top - 4, left_pad + plot_w + 4, baseline + 4],
                radius=10,
                fill=_PANEL,
            )
            draw.text((left_pad - 8, panel_top), name, font=f_title, fill=color)

            vmax = max(values) if values else 1.0
            vmin = min(values) if values else 0.0
            top = _nice_max(vmax) if vmax > 0 else 0.0
            bottom = -_nice_max(-vmin) if vmin < 0 else 0.0
            span = (top - bottom) or 1.0

            def _y_of(val: float) -> float:
                return baseline - ((val - bottom) / span) * plot_h

            zero_y = _y_of(0.0)

            if bottom < 0:
                for level in (top, 0.0, bottom):
                    gy = _y_of(level)
                    draw.line([(left_pad - 6, gy), (left_pad + plot_w, gy)], fill=_GRID, width=1)
                    draw.text((6, gy - 8), _fmt_value(level), font=f_small, fill=_MUTED)
            else:
                for frac in (0.5, 1.0):
                    gy = baseline - plot_h * frac
                    draw.line([(left_pad - 6, gy), (left_pad + plot_w, gy)], fill=_GRID, width=1)
                    draw.text((6, gy - 8), _fmt_value(top * frac), font=f_small, fill=_MUTED)

            for i, v in enumerate(values):
                x0 = left_pad + i * slot + bar_off
                yv = _y_of(v)
                y_top = min(zero_y, yv)
                y_bot = max(zero_y, yv)
                if v != 0 and y_bot - y_top < 2:
                    if v > 0:
                        y_top = y_bot - 2
                    else:
                        y_bot = y_top + 2
                bar_color = (240, 90, 90) if v < 0 else color
                draw.rounded_rectangle([x0, y_top, x0 + bar_w, y_bot], radius=4, fill=bar_color)
                if show_values and v != 0:
                    label = _fmt_value(v)
                    tw = draw.textlength(label, font=f_small)
                    ly = (y_top - 17) if v > 0 else (y_bot + 3)
                    draw.text((x0 + bar_w / 2 - tw / 2, ly), label, font=f_small, fill=_TEXT)

        for i, lbl in enumerate(x_labels):
            if i % label_step != 0 and i != len(x_labels) - 1:
                continue
            x_center = left_pad + i * slot + slot / 2
            tw = draw.textlength(lbl, font=f_small)
            draw.text((x_center - tw / 2, last_baseline + 8), lbl, font=f_small, fill=_MUTED)

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        buf.seek(0)
        return buf
    except Exception as e:
        logger.warning(f"[Stats] Не удалось отрисовать график: {e}")
        return None
