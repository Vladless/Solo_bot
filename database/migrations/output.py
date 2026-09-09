def mig_out(msg: str, color: str | None = None) -> None:
    """Вывод шагов схемы: успешное завершение подсвечиваем зелёным."""
    if color:
        try:
            from rich.console import Console

            Console().print(msg, style=color, markup=False, highlight=False)
            return
        except Exception:
            pass
    print(msg, flush=True)
