"""PDF writer."""

from pathlib import Path
from typing import List, Optional

import img2pdf

from ..errors import DownloaderError
from .images import _image_to_jpeg_bytes


def _images_to_pdf(image_paths: List[Path], output_path: Path,
                   credit: Optional[str] = None):
    """Convert a list of images to a single PDF."""
    processed_images = []

    for img_path in image_paths:
        try:
            jpeg_data, _, _ = _image_to_jpeg_bytes(img_path)
            processed_images.append(jpeg_data)
        except Exception as e:
            print(f"  Warning: Could not process {img_path}: {e}")

    if not processed_images:
        raise DownloaderError("No images could be processed")

    pdf_bytes = img2pdf.convert(processed_images, author=credit) if credit else img2pdf.convert(processed_images)
    output_path.write_bytes(pdf_bytes)
