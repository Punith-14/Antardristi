from pathlib import Path

import cv2
import numpy as np

from core import paths


OUTPUT_DIR = paths.OUTPUTS / "images"


def detect_water_rgb(image_path, output_name="api_result.png"):
    """
    Approximate water screening for ordinary RGB uploads.
    This is not NDWI because uploaded consumer images do not contain NIR/SWIR bands.
    """
    image = cv2.imread(str(image_path))

    if image is None:
        return None, None, "Error: Image not found", {}

    image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    green = image_rgb[:, :, 1].astype(float)
    blue = image_rgb[:, :, 2].astype(float)

    rgb_water_index = (blue - green) / (blue + green + 1e-5)
    threshold = 0.08
    water_mask = rgb_water_index > threshold

    total_pixels = water_mask.size
    water_pixels = int(np.sum(water_mask))
    water_percentage = (water_pixels / total_pixels) * 100

    if water_percentage > 30:
        explanation = "High water-like presence detected in the uploaded RGB image so it is likely to be a flood-affected region."
        severity = "high"
    elif water_percentage > 10:
        explanation = "Moderate water-like presence detected in the uploaded RGB image."
        severity = "moderate"
    else:
        explanation = "Low water-like presence detected in the uploaded RGB image."
        severity = "low"

    output = image.copy()
    output[water_mask] = [255, 0, 0]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / output_name
    cv2.imwrite(str(output_path), output)

    metadata = {
        "method": "RGB heuristic water screening from uploaded image",
        "threshold": threshold,
        "severity": severity,
        "water_pixels": water_pixels,
        "total_pixels": int(total_pixels),
        "output_image_url": f"/outputs/images/{output_path.name}",
        "index_type": "RGB heuristic",
    }

    return str(output_path), water_percentage, explanation, metadata
