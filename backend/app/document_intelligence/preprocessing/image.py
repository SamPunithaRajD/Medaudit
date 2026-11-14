"""OpenCV image preprocessing for OCR: grayscale, denoise, contrast, deskew, orientation, thresholding."""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

MIN_WIDTH_PX = 1000  # ≈ A4 at ~120 DPI; below this OCR quality degrades sharply


@dataclass
class PreprocessResult:
    image: np.ndarray  # single-channel uint8, binarised
    gray: np.ndarray
    steps: list[str] = field(default_factory=list)
    skew_angle: float = 0.0
    rotation: int = 0
    resolution_ok: bool = True
    contrast_std: float = 0.0
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {"steps": self.steps, "skew_angle": round(self.skew_angle, 2), "rotation": self.rotation,
                "resolution_ok": self.resolution_ok, "contrast_std": round(self.contrast_std, 1),
                "warnings": self.warnings}


def to_grayscale(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    return cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)


def estimate_skew(gray: np.ndarray) -> float:
    """Estimate text skew in degrees from the minimum-area rectangle around dark pixels."""
    inv = cv2.bitwise_not(gray)
    _, th = cv2.threshold(inv, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    coords = np.column_stack(np.where(th > 0))
    if coords.shape[0] < 50:
        return 0.0
    angle = cv2.minAreaRect(coords[:, ::-1].astype(np.float32))[-1]
    # OpenCV ≥4.5 returns angles in (0, 90]; map to (-45, 45].
    if angle > 45:
        angle -= 90
    return float(angle)


def rotate(img: np.ndarray, angle: float) -> np.ndarray:
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def correct_orientation(gray: np.ndarray, osd_rotation: int | None) -> tuple[np.ndarray, int]:
    """Apply a 90° multiple rotation, e.g. reported by Tesseract OSD. No guess is made without OSD."""
    if not osd_rotation:
        return gray, 0
    k = {90: cv2.ROTATE_90_COUNTERCLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_CLOCKWISE}.get(osd_rotation % 360)
    return (cv2.rotate(gray, k), osd_rotation) if k is not None else (gray, 0)


def preprocess(img: np.ndarray, *, osd_rotation: int | None = None, denoise: bool = True,
               deskew: bool = True) -> PreprocessResult:
    steps: list[str] = []
    warnings: list[str] = []
    gray = to_grayscale(img)
    steps.append("grayscale")
    h, w = gray.shape[:2]
    resolution_ok = w >= MIN_WIDTH_PX
    if not resolution_ok:
        warnings.append(f"low_resolution:{w}x{h}")
        scale = MIN_WIDTH_PX / max(w, 1)
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        steps.append(f"upscale:{scale:.2f}")
    gray, rotation = correct_orientation(gray, osd_rotation)
    if rotation:
        steps.append(f"orientation:{rotation}")
    if denoise:
        gray = cv2.fastNlMeansDenoising(gray, None, h=10, templateWindowSize=7, searchWindowSize=21)
        steps.append("denoise")
    contrast_std = float(gray.std())
    if contrast_std < 40:
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)
        steps.append("contrast_clahe")
        if contrast_std < 15:
            warnings.append("very_low_contrast")
    angle = 0.0
    if deskew:
        angle = estimate_skew(gray)
        if 0.3 < abs(angle) <= 15:
            gray = rotate(gray, angle)
            steps.append(f"deskew:{angle:.2f}")
        elif abs(angle) > 15:
            warnings.append(f"skew_out_of_range:{angle:.1f}")
            angle = 0.0
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15)
    steps.append("adaptive_threshold")
    return PreprocessResult(binary, gray, steps, angle, rotation, resolution_ok, contrast_std, warnings)
