"""
Uploaded-image screening, now with an evidence record.

The upload endpoint was the one place "every number is traceable" was not
true: a bare percentage, a severity label, and the sentence "it is likely to
be a flood-affected region" inferred from blue pixels. These tests pin down
what replaced it - and, as much as what it measures, what it refuses to
claim.

Images are synthesised in memory with known colours, so the expected
fraction is exact rather than eyeballed.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

cv2 = pytest.importorskip("cv2")

from core import cache  # noqa: E402
from detection import rgb_upload  # noqa: E402

# BGR, as OpenCV stores it.
BLUE_WATER = (200, 120, 40)       # (b - g) / (b + g) = 0.25, well above 0.08
BROWN_WATER = (60, 110, 150)      # sediment-laden: more green than blue
GREEN_FIELD = (40, 160, 60)


def image(width=100, height=100, fill=GREEN_FIELD, patches=()):
    """A BGR image with rectangular patches: [(x0, y0, x1, y1, colour)]."""
    array = np.zeros((height, width, 3), dtype=np.uint8)
    array[:, :] = fill
    for x0, y0, x1, y1, colour in patches:
        array[y0:y1, x0:x1] = colour
    return array


def encode(array, ext=".png"):
    ok, buffer = cv2.imencode(ext, array)
    assert ok
    return buffer.tobytes()


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")


@pytest.fixture
def analyse(tmp_path):
    def run(array, name="test.png", question=""):
        return rgb_upload.analyse(encode(array), name, question,
                                  output_dir=tmp_path / "overlays")
    return run


def evidence(response):
    return {item["quantity"]: item for item in response["evidence"]}


# --------------------------------------------------------------- measuring

def test_a_known_blue_patch_is_measured_exactly():
    # A quarter of the image is blue water.
    stats = rgb_upload.measure(image(patches=[(0, 0, 50, 50, BLUE_WATER)]))
    assert stats["water_pixels"] == 2500
    assert stats["total_pixels"] == 10000
    assert stats["fraction_percent"] == 25.0


def test_brown_floodwater_is_missed_and_that_is_why_the_caveat_exists():
    """The failure mode the caveat is about, demonstrated rather than asserted.
    Half this image is brown water. The screen sees none of it."""
    stats = rgb_upload.measure(image(patches=[(0, 0, 100, 50, BROWN_WATER)]))
    assert stats["water_pixels"] == 0


def test_pure_black_does_not_become_nan():
    stats = rgb_upload.measure(image(fill=(0, 0, 0)))
    assert stats["water_pixels"] == 0
    assert np.isfinite(stats["fraction_percent"])


def test_a_greyscale_image_has_no_colourfulness():
    assert rgb_upload.colourfulness(image(fill=(128, 128, 128))) == pytest.approx(0.0)


def test_a_vivid_image_is_colourful():
    vivid = image(patches=[(0, 0, 50, 100, (255, 0, 0)), (50, 0, 100, 100, (0, 0, 255))])
    assert rgb_upload.colourfulness(vivid) > rgb_upload.LOW_COLOURFULNESS


def test_the_overlay_is_drawn_from_the_pixels_that_were_counted(tmp_path):
    """One mask for both, so the picture cannot show different water from
    the number."""
    array = image(patches=[(0, 0, 50, 50, BLUE_WATER)])
    stats = rgb_upload.measure(array)
    path = rgb_upload.draw_overlay(array, stats["mask"], tmp_path / "o.png")

    drawn = cv2.imread(str(path))
    changed = np.any(drawn != array, axis=2)
    assert int(changed.sum()) == stats["water_pixels"]


# ------------------------------------------------------ the evidence record

def test_every_number_is_an_evidence_record(analyse):
    response = analyse(image(patches=[(0, 0, 50, 50, BLUE_WATER)]))
    ev = evidence(response)

    assert ev["water_like_pixel_fraction"]["value"] == 25.0
    assert ev["water_like_pixels"]["value"] == 2500
    assert ev["image_pixels"]["value"] == 10000
    assert "colourfulness" in ev


def test_the_fraction_says_what_it_was_derived_from(analyse):
    ev = evidence(analyse(image()))
    fraction = ev["water_like_pixel_fraction"]
    assert set(fraction["derived_from"]) == {
        ev["water_like_pixels"]["id"], ev["image_pixels"]["id"],
    }
    assert fraction["denominator"] == "image_pixels"


def test_no_area_of_ground_is_ever_claimed(analyse):
    """A photo has no scale. 40% of its pixels could be a puddle or a
    floodplain. Any km2 figure here would be invented."""
    response = analyse(image(patches=[(0, 0, 50, 50, BLUE_WATER)]))
    for item in response["evidence"]:
        assert item["unit"] != "km2", item
    assert "km2" not in response["report"]["text"]
    assert "km²" not in response["report"]["text"]


def test_no_confidence_is_claimed_for_an_unvalidated_method(analyse):
    """The SAR rule's precision is a measurement. A confidence here would be
    a guess dressed the same way."""
    for item in analyse(image())["evidence"]:
        assert "confidence" not in item


def test_no_flood_verdict_is_made(analyse):
    """The old path said "likely to be a flood-affected region" when 30% of
    pixels were bluish - a flood claim from pixel colour."""
    response = analyse(image(fill=BLUE_WATER))
    text = response["report"]["text"].lower()

    assert "flood-affected" not in text
    # The old phrase exactly. "likely to be" alone also matches the brown-water
    # caveat ("likely to be missed"), which is a claim worth keeping.
    assert "likely to be a flood" not in text
    assert "not an assessment of flooding" in text
    assert "severity" not in response


# ---------------------------------------------------------------- caveats

def test_the_standing_caveats_are_always_attached(analyse):
    notes = analyse(image())["unobserved"]["notes"]
    joined = " ".join(notes)
    assert "not been validated" in joined
    assert "not an area of ground" in joined
    assert "brown" in joined
    assert "Permanent water" in joined


def test_a_colourless_image_gets_its_own_caveat(analyse):
    grey = analyse(image(fill=(128, 128, 128)))
    assert any("very little colour" in n for n in grey["unobserved"]["notes"])

    colourful = analyse(image(patches=[(0, 0, 50, 100, (255, 0, 0))]))
    assert not any("very little colour" in n for n in colourful["unobserved"]["notes"])


# ----------------------------------------------------------- verification

@pytest.mark.parametrize("fill,patches", [
    (GREEN_FIELD, []),
    (GREEN_FIELD, [(0, 0, 50, 50, BLUE_WATER)]),
    (BLUE_WATER, []),
    ((128, 128, 128), []),
    ((0, 0, 0), []),
])
def test_the_report_passes_the_same_verifier_as_the_satellite_reports(analyse, fill, patches):
    """Not a special lenient check for uploads: verify_report and
    check_caveats, exactly as build_report uses them."""
    verification = analyse(image(fill=fill, patches=patches))["verification"]

    assert verification["passed"], verification
    assert verification["faithfulness_rate"] == 1.0
    assert verification["caveats"]["completeness"] == 1.0
    assert verification["invalid_citations"] == []


def test_the_verifier_would_catch_an_invented_number(analyse):
    """Proof the check is live rather than rubber-stamping the template."""
    response = analyse(image())
    response["report"]["text"] += " About 812 km2 of ground is flooded."
    assert not rgb_upload.verify(response)["passed"]


def test_uploads_never_go_to_the_language_model(analyse):
    report = analyse(image())["report"]
    assert report["generator_model"] is None
    assert "never sent to the language model" in report["written_by"]


# ------------------------------------------------------------- identity

def test_the_same_bytes_get_the_same_request_id(analyse):
    array = image(patches=[(0, 0, 30, 30, BLUE_WATER)])
    assert analyse(array)["request_id"] == analyse(array, name="renamed.png")["request_id"]


def test_different_bytes_get_different_request_ids(analyse):
    assert analyse(image())["request_id"] != analyse(image(fill=BLUE_WATER))["request_id"]


def test_a_method_change_changes_the_request_id(monkeypatch):
    """So a cached result from an older method is never served as though it
    came from this one."""
    content = encode(image())
    before = cache.key_for(rgb_upload.request_key(content))
    monkeypatch.setattr(rgb_upload, "METHOD_VERSION", "rgb_screen_v2")
    assert cache.key_for(rgb_upload.request_key(content)) != before


def test_the_result_is_stored_so_it_can_be_fetched_and_exported(analyse):
    response = analyse(image())
    stored = cache.get_by_id(response["request_id"])
    assert stored is not None
    assert stored["evidence"] == response["evidence"]


def test_the_filename_cannot_smuggle_a_path_into_the_response(analyse):
    response = analyse(image(), name="../../etc/passwd.png")
    assert ".." not in response["region"]["name"]
    assert "/" not in response["region"]["name"].split("(", 1)[1]


# ------------------------------------------------------------- refusals

def test_bytes_that_are_not_an_image_are_refused_with_a_reason():
    with pytest.raises(rgb_upload.UnreadableImage, match="could not be read"):
        rgb_upload.decode(b"this is a text file with a .png name")


def test_an_empty_upload_is_refused():
    with pytest.raises(rgb_upload.UnreadableImage, match="empty"):
        rgb_upload.decode(b"")


def test_an_oversized_upload_is_refused_before_decoding(monkeypatch):
    monkeypatch.setattr(rgb_upload, "MAX_UPLOAD_BYTES", 100)
    with pytest.raises(rgb_upload.UnreadableImage, match="limit"):
        rgb_upload.decode(encode(image()))


def test_too_many_pixels_is_refused(monkeypatch):
    monkeypatch.setattr(rgb_upload, "MAX_PIXELS", 5000)
    with pytest.raises(rgb_upload.UnreadableImage, match="megapixel"):
        rgb_upload.decode(encode(image()))


# -------------------------------------------------------------- the PDF

def test_an_upload_exports_to_pdf_with_its_caveats(analyse):
    pytest.importorskip("reportlab")
    pypdf = pytest.importorskip("pypdf")
    from io import BytesIO

    from pipeline import export_pdf

    response = analyse(image(patches=[(0, 0, 50, 50, BLUE_WATER)]))
    doc = export_pdf.outline(response)
    assert doc["status"]["level"] == "ok"

    pdf = export_pdf.render(response)
    text = "\n".join(p.extract_text() for p in pypdf.PdfReader(BytesIO(pdf)).pages)
    assert "25.0" in text
    assert "not georeferenced" in text
    assert "brown" in text


# ---------------------------------------------------------- the endpoint

MAIN = (BACKEND / "main.py").read_text(encoding="utf-8")


def test_the_endpoint_uses_the_evidence_path_not_the_legacy_one():
    assert "from legacy.ndwi" not in MAIN
    assert "detect_water_rgb" not in MAIN
    assert "rgb_upload.analyse(" in MAIN


def test_the_original_upload_is_not_written_to_disk():
    """A phone photo's EXIF can carry the GPS position it was taken at. The
    old path kept every upload; this one keeps only the re-encoded overlay."""
    body = MAIN.split("async def analyze_upload(")[1]
    assert "write_bytes" not in body
    assert "UPLOAD_DIR" not in MAIN


def test_a_refusal_is_a_400_not_a_200_with_an_error_key():
    body = MAIN.split("async def analyze_upload(")[1]
    assert 'return {"error"' not in body
    assert "status_code=400" in body


@pytest.fixture
def client(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    import main
    monkeypatch.setattr(rgb_upload, "OUTPUT_DIR", tmp_path / "overlays")
    return TestClient(main.app)


def test_the_endpoint_end_to_end(client):
    content = encode(image(patches=[(0, 0, 50, 50, BLUE_WATER)]))
    response = client.post(
        "/analyze-upload",
        files={"image": ("field.png", content, "image/png")},
        data={"question": "is there water"},
    )
    assert response.status_code == 200
    body = response.json()
    assert evidence(body)["water_like_pixel_fraction"]["value"] == 25.0
    assert body["verification"]["passed"]

    # And the id it returns works with the rest of the API.
    assert client.get(f"/analyze/{body['request_id']}").status_code == 200
    pdf = client.get(f"/analyze/{body['request_id']}/report.pdf")
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")


def test_the_endpoint_refuses_a_disguised_file(client):
    response = client.post(
        "/analyze-upload",
        files={"image": ("photo.png", b"not an image", "image/png")},
    )
    assert response.status_code == 400
    assert "could not be read" in response.json()["detail"]


def test_the_endpoint_refuses_a_wrong_extension(client):
    response = client.post(
        "/analyze-upload",
        files={"image": ("notes.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 400
