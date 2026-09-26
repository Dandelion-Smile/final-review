"""Bounded conversion of uploaded course files into searchable Markdown text."""

import os
import re
import shutil
import subprocess
import sys
import tempfile
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from markitdown import MarkItDown
from PIL import Image, UnidentifiedImageError

SUPPORTED_SUFFIXES = {
    ".md",
    ".txt",
    ".pdf",
    ".ppt",
    ".pptx",
    ".doc",
    ".docx",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
}
IMAGE_FORMATS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}
MAX_IMAGE_PIXELS = 20_000_000
CONVERSION_TIMEOUT_SECONDS = 45
LEGACY_OFFICE_TIMEOUT_SECONDS = 120


def _find_executable(names: tuple[str, ...], windows_relative_path: str) -> str | None:
    for name in names:
        executable = shutil.which(name)
        if executable:
            return executable
    if sys.platform == "win32":
        for variable in ("ProgramFiles", "ProgramFiles(x86)"):
            root = os.environ.get(variable)
            if root:
                candidate = Path(root) / windows_relative_path
                if candidate.is_file():
                    return str(candidate)
    return None


def _tessdata_dir() -> Path | None:
    configured = os.environ.get("TESSDATA_PREFIX")
    local_app_data = os.environ.get("LOCALAPPDATA") if sys.platform == "win32" else None
    candidates = [Path(configured)] if configured else []
    if local_app_data:
        candidates.append(Path(local_app_data) / "FinalReview" / "tessdata")
    for directory in candidates:
        if all(
            (directory / f"{language}.traineddata").is_file()
            for language in ("chi_sim", "eng")
        ):
            return directory
    return None


def _check_office_archive(content: bytes, max_bytes: int) -> None:
    try:
        with ZipFile(BytesIO(content)) as archive:
            if sum(info.file_size for info in archive.infolist()) > max_bytes * 20:
                raise ValueError("Office 文件解压体积超过限制")
    except BadZipFile as exc:
        raise ValueError("Office 文件损坏或格式与扩展名不符") from exc


def _run(command: list[str], label: str, timeout_seconds: int = CONVERSION_TIMEOUT_SECONDS) -> None:
    try:
        result = subprocess.run(
            command, capture_output=True, timeout=timeout_seconds, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"{label}超时，请上传较小或更清晰的文件") from exc
    except OSError as exc:
        raise ValueError(f"{label}服务不可用") from exc
    if result.returncode:
        raise ValueError(f"{label}失败，请检查文件是否损坏")


def _convert_legacy(path: Path, directory: Path) -> Path:
    executable = _find_executable(
        ("libreoffice", "soffice"), "LibreOffice/program/soffice.com"
    )
    if not executable:
        raise ValueError("旧版 Office 转换服务不可用")
    target_suffix = ".docx" if path.suffix == ".doc" else ".pptx"
    profile = (directory / "lo-profile").as_uri()
    _run(
        [
            executable,
            f"-env:UserInstallation={profile}",
            "--headless",
            "--convert-to",
            target_suffix[1:],
            "--outdir",
            str(directory),
            str(path),
        ],
        "旧版 Office 转换",
        LEGACY_OFFICE_TIMEOUT_SECONDS,
    )
    converted = directory / f"{path.stem}{target_suffix}"
    if not converted.is_file() or not converted.stat().st_size:
        raise ValueError("旧版 Office 转换未生成可读文件")
    return converted


def _ocr_image(path: Path, content: bytes, suffix: str) -> str:
    try:
        with Image.open(BytesIO(content)) as image:
            if (
                image.format != IMAGE_FORMATS[suffix]
                or image.width * image.height > MAX_IMAGE_PIXELS
            ):
                raise ValueError("图片格式不符或像素超过限制")
            image.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("图片损坏或格式与扩展名不符") from exc
    executable = _find_executable(("tesseract",), "Tesseract-OCR/tesseract.exe")
    if not executable:
        raise ValueError("图片 OCR 服务不可用")
    command = [executable]
    tessdata_dir = _tessdata_dir()
    if tessdata_dir:
        command.extend(["--tessdata-dir", str(tessdata_dir)])
    command.extend([str(path), "stdout", "-l", "chi_sim+eng"])
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=CONVERSION_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("图片 OCR 超时，请上传较小或更清晰的图片") from exc
    except OSError as exc:
        raise ValueError("图片 OCR 服务不可用") from exc
    if result.returncode:
        raise ValueError("图片 OCR 失败，请检查中文和英文语言包")
    markdown = re.sub(r"(?<=[\u4e00-\u9fff])[ \t]+(?=[\u4e00-\u9fff])", "", result.stdout)
    if not markdown.strip():
        raise ValueError("图片中没有识别到可检索文字")
    return markdown


def convert_upload(content: bytes, filename: str, max_bytes: int) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError("不支持此文件格式；支持 md/txt/pdf/ppt/pptx/doc/docx/png/jpg/webp")
    if not content or len(content) > max_bytes:
        raise ValueError("文件为空或超过上传大小限制")
    if suffix in {".md", ".txt"}:
        try:
            return content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("文本文件不是 UTF-8 编码") from exc
    if suffix in {".pptx", ".docx"}:
        _check_office_archive(content, max_bytes)
    with tempfile.TemporaryDirectory(prefix="final-review-") as temporary:
        directory = Path(temporary)
        path = directory / f"material{suffix}"
        path.write_bytes(content)
        if suffix in IMAGE_FORMATS:
            return _ocr_image(path, content, suffix)
        if suffix in {".ppt", ".doc"}:
            path = _convert_legacy(path, directory)
            _check_office_archive(path.read_bytes(), max_bytes)
        try:
            markdown = MarkItDown(enable_plugins=False).convert(str(path)).text_content
        except Exception as exc:
            raise ValueError("文件文字提取失败，请检查格式和可读性") from exc
        if not markdown.strip():
            raise ValueError("文件没有可检索文字；扫描版 PDF 暂不支持 OCR")
        return markdown
