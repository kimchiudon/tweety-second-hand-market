from __future__ import annotations

import hashlib
import hmac
import io
import re
import secrets
import time
import warnings
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs

from PIL import Image, ImageOps, UnidentifiedImageError


PBKDF2_ITERATIONS = 600_000
SESSION_SECONDS = 60 * 60 * 8
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,24}$")
NICKNAME_RE = re.compile(r"^[A-Za-z0-9_가-힣]{2,20}$")
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 50_000_000
MAX_UPLOAD_COUNT = 10
MAX_REQUEST_BYTES = 32 * 1024 * 1024
MAX_FIELD_BYTES = 64 * 1024
MAX_STORED_SIDE = 4096

Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


@dataclass(frozen=True)
class UploadedFile:
    filename: str
    content_type: str
    data: bytes


def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_text, salt_hex, expected_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        if iterations < 100_000 or iterations > 2_000_000:
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), iterations
        )
        return hmac.compare_digest(actual, bytes.fromhex(expected_hex))
    except (ValueError, TypeError):
        return False


def validate_password(password: str) -> list[str]:
    errors: list[str] = []
    if len(password) < 10:
        errors.append("비밀번호는 10자 이상이어야 합니다.")
    if len(password) > 128:
        errors.append("비밀번호는 128자 이하여야 합니다.")
    if password.lower() == password or password.upper() == password:
        errors.append("비밀번호에는 영문 대문자와 소문자를 모두 포함해 주세요.")
    if not any(character.isdigit() for character in password):
        errors.append("비밀번호에는 숫자를 포함해 주세요.")
    return errors


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def privacy_hash(value: str) -> str:
    return hashlib.sha256(("tiny-market-audit:" + value).encode("utf-8")).hexdigest()


def parse_cookies(environ: dict) -> SimpleCookie:
    cookies = SimpleCookie()
    cookies.load(environ.get("HTTP_COOKIE", ""))
    return cookies


def read_form_data(environ: dict, *, max_bytes: int = MAX_REQUEST_BYTES) -> tuple[dict[str, str], dict[str, list[UploadedFile]]]:
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError as error:
        raise ValueError("Invalid Content-Length") from error
    if length < 0 or length > max_bytes:
        raise ValueError("사진을 포함한 요청 전체 크기는 최대 32MB입니다.")
    body = environ["wsgi.input"].read(length)
    content_type = environ.get("CONTENT_TYPE", "application/x-www-form-urlencoded")
    if content_type.lower().startswith("multipart/form-data"):
        if len(content_type) > 512:
            raise ValueError("Invalid Content-Type")
        try:
            content_type_bytes = content_type.encode("ascii", "strict")
        except UnicodeEncodeError as error:
            raise ValueError("Invalid Content-Type") from error
        message = BytesParser(policy=policy.default).parsebytes(
            b"Content-Type: " + content_type_bytes + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
        )
        if not message.is_multipart():
            raise ValueError("Invalid multipart form data")
        fields: dict[str, str] = {}
        files: dict[str, list[UploadedFile]] = {}
        file_count = 0
        if len(message.get_payload()) > 30:
            raise ValueError("Too many form fields")
        for part in message.iter_parts():
            if part.get_content_disposition() != "form-data":
                continue
            name = part.get_param("name", header="content-disposition")
            if not name:
                continue
            filename = part.get_filename()
            payload = part.get_payload(decode=True) or b""
            if filename:
                file_count += 1
                if file_count > MAX_UPLOAD_COUNT:
                    raise ValueError("사진은 한 번에 최대 10장까지 업로드할 수 있습니다.")
                if len(payload) > MAX_IMAGE_BYTES:
                    raise ValueError("사진 한 장은 최대 8MB까지 업로드할 수 있습니다.")
                files.setdefault(name, []).append(UploadedFile(filename, part.get_content_type(), payload))
            else:
                if len(payload) > MAX_FIELD_BYTES:
                    raise ValueError("입력 항목이 너무 큽니다.")
                try:
                    fields[name] = payload.decode(part.get_content_charset() or "utf-8")
                except (LookupError, UnicodeDecodeError) as error:
                    raise ValueError("Invalid form data") from error
        return fields, files
    try:
        parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True, max_num_fields=30)
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError("Invalid form data") from error
    return {key: values[-1] for key, values in parsed.items()}, {}


def read_form(environ: dict, *, max_bytes: int = 32_768) -> dict[str, str]:
    fields, _ = read_form_data(environ, max_bytes=max_bytes)
    return fields


def _sanitize_image(data: bytes, *, allow_legacy_webp: bool = False) -> tuple[str, bytes]:
    allowed_formats = {"PNG", "JPEG"} | ({"WEBP"} if allow_legacy_webp else set())
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as probe:
                source_format = (probe.format or "").upper()
                if source_format not in allowed_formats:
                    raise ValueError("PNG 또는 JPEG 형식의 사진만 업로드할 수 있습니다.")
                if getattr(probe, "n_frames", 1) != 1:
                    raise ValueError("움직이는 이미지는 업로드할 수 없습니다.")
                probe.verify()
            with Image.open(io.BytesIO(data)) as decoded:
                image = ImageOps.exif_transpose(decoded)
                image.load()
                if image.width < 1 or image.height < 1 or image.width * image.height > MAX_IMAGE_PIXELS:
                    raise ValueError("사진 해상도가 너무 큽니다.")
                image.thumbnail((MAX_STORED_SIDE, MAX_STORED_SIDE), Image.Resampling.LANCZOS)
                output = io.BytesIO()
                has_alpha = image.mode in {"RGBA", "LA"} or "transparency" in image.info
                if source_format == "PNG" or (source_format == "WEBP" and has_alpha):
                    image.convert("RGBA" if has_alpha else "RGB").save(output, format="PNG", optimize=True)
                    extension = "png"
                else:
                    image.convert("RGB").save(output, format="JPEG", quality=90, optimize=True, progressive=True)
                    extension = "jpg"
    except ValueError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, UnidentifiedImageError, OSError, SyntaxError) as error:
        raise ValueError("손상되었거나 지원하지 않는 사진입니다.") from error
    sanitized = output.getvalue()
    if not sanitized or len(sanitized) > MAX_IMAGE_BYTES:
        raise ValueError("안전하게 변환한 사진이 8MB를 초과합니다.")
    return extension, sanitized


def validate_image(upload: UploadedFile) -> tuple[str, bytes]:
    data = upload.data
    if not data:
        raise ValueError("상품 사진을 선택해 주세요.")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("사진 한 장은 최대 8MB까지 업로드할 수 있습니다.")
    suffix = Path(upload.filename).suffix.lower()
    content_type = upload.content_type.lower()
    if suffix == ".png" and content_type == "image/png" and data.startswith(b"\x89PNG\r\n\x1a\n"):
        expected_extension = "png"
    elif suffix in {".jpg", ".jpeg"} and content_type in {"image/jpeg", "image/jpg"} and data.startswith(b"\xff\xd8"):
        expected_extension = "jpg"
    else:
        raise ValueError("파일 확장자, MIME 형식과 실제 PNG/JPEG 형식이 일치해야 합니다.")
    extension, sanitized = _sanitize_image(data)
    if extension != expected_extension:
        raise ValueError("파일 확장자와 실제 이미지 형식이 일치하지 않습니다.")
    return extension, sanitized


def sanitize_legacy_image(data: bytes) -> tuple[str, bytes]:
    """Decode old PNG/JPEG/WebP data and rewrite it without metadata."""
    return _sanitize_image(data, allow_legacy_webp=True)


def constant_time_equal(left: str, right: str) -> bool:
    return bool(left and right and hmac.compare_digest(left, right))


def session_expiry() -> int:
    return int(time.time()) + SESSION_SECONDS
