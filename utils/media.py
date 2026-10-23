import os


def get_media_type(media_path: str) -> str:
    """Определяет тип медиа по расширению файла."""
    if not media_path:
        return "photo"
    ext = os.path.splitext(media_path.lower())[1]
    if ext in {".mp4", ".mov", ".avi"}:
        return "video"
    if ext == ".gif":
        return "animation"
    return "photo"
