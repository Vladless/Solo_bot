from io import BytesIO


BCRYPT_MAX_PASSWORD_BYTES = 72
BCRYPT_ROUNDS = 12

IMAGE_MAX_SIDE = 2048
IMAGE_JPEG_QUALITY = 85
IMAGE_WEBP_QUALITY = 85


def optimize_image_bytes(data: bytes, ext: str) -> bytes:
    """Возвращает картинку, уменьшенную до IMAGE_MAX_SIDE и пережатую."""
    try:
        from PIL import Image, ImageOps

        with Image.open(BytesIO(data)) as img:
            img = ImageOps.exif_transpose(img)
            w, h = img.size
            if max(w, h) <= IMAGE_MAX_SIDE and len(data) < 500_000:
                return data
            if max(w, h) > IMAGE_MAX_SIDE:
                img.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE), Image.Resampling.LANCZOS)
            buffer = BytesIO()
            save_kwargs: dict = {}
            if ext in (".jpg", ".jpeg"):
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                save_kwargs = {"format": "JPEG", "quality": IMAGE_JPEG_QUALITY, "optimize": True, "progressive": True}
            elif ext == ".webp":
                save_kwargs = {"format": "WEBP", "quality": IMAGE_WEBP_QUALITY, "method": 6}
            elif ext == ".png":
                save_kwargs = {"format": "PNG", "optimize": True}
            else:
                return data
            img.save(buffer, **save_kwargs)
            optimized = buffer.getvalue()
            return optimized if len(optimized) < len(data) else data
    except Exception:
        return data


def generate_qr_file(payload: str, path: str) -> str:
    """Сохраняет QR-код payload в path и возвращает путь."""
    import qrcode

    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(payload)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    with open(path, "wb") as file:
        file.write(buffer.read())
    return path


def _password_bytes(password: str) -> bytes:
    raw = password.encode("utf-8")
    if len(raw) > BCRYPT_MAX_PASSWORD_BYTES:
        return raw[:BCRYPT_MAX_PASSWORD_BYTES]
    return raw


def hash_password(password: str) -> str:
    """Возвращает bcrypt-хеш пароля."""
    import bcrypt

    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    return bcrypt.hashpw(_password_bytes(password), salt).decode("ascii")


def check_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        import bcrypt

        return bcrypt.checkpw(_password_bytes(password), password_hash.encode("ascii"))
    except Exception:
        return False
