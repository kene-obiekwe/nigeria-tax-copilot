"""One-off re-OCR of the NRS print of the Nigeria Tax Administration Act (chunking design, section 2).

Runs only in the OCR venv (D-13):

    .venv-ocr\\Scripts\\python python\\ingest\\ocr_ntaa.py

Each page is rendered in memory at native scan resolution and read with RapidOCR. Every text box
is kept, so the margin split can be redone later without re-running OCR. Page images are never
written to disk; only their SHA-256 goes into the manifest.

Outputs (committed, D-12): data/derived/ocr/ntaa_nrs/pNNN.json (boxes), pNNN.txt (reading-order
view) and ocr_manifest.json (input hash, versions, settings, per-page hashes).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import sys
import time
from datetime import date
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
from rapidocr_onnxruntime import RapidOCR

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data" / "raw" / "nigeria_tax_administration_act_2025_gazette_nrs.pdf"
OUT_DIR = ROOT / "data" / "derived" / "ocr" / "ntaa_nrs"
MANIFEST = ROOT / "data" / "manifest.csv"

SCALE = 14.4  # renders the 147 x 203 pt pages at about the embedded scan size (2116 x 2916 px)
DET_UNCLIP_RATIO = 1.3
LIBRARIES = ["rapidocr-onnxruntime", "onnxruntime", "opencv-python", "numpy", "pypdfium2"]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def expected_source_hash() -> str:
    with MANIFEST.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["file"] == "raw/" + SOURCE.name:
                return row["sha256"]
    raise SystemExit(f"{SOURCE.name} is not in {MANIFEST}")


def reading_order(boxes: list[dict]) -> list[str]:
    """Group boxes into lines by vertical centre, left to right. For human review only."""
    rows: list[list[dict]] = []
    for b in sorted(boxes, key=lambda b: (b["cy"], b["x0"])):
        if rows and abs(b["cy"] - np.mean([r["cy"] for r in rows[-1]])) <= 0.6 * b["h"]:
            rows[-1].append(b)
        else:
            rows.append([b])
    return [" ".join(b["text"] for b in sorted(r, key=lambda b: b["x0"])) for r in rows]


FILL_RATIO = 1.8  # a body-line gap this many times the page's median line pitch is re-read
FILL_PAD = 12  # pixels added above and below each re-read band
FILL_DET_LIMIT = 1280  # detector size for the bands (the full-page pass uses the full image)


def to_boxes(result, y_offset: float = 0.0, ocr_pass: int = 1) -> list[dict]:
    boxes = []
    for points, text, score in result or []:
        bbox = [[round(float(x), 1), round(float(y) + y_offset, 1)] for x, y in points]
        xs, ys = [p[0] for p in bbox], [p[1] for p in bbox]
        boxes.append({"text": text, "score": round(float(score), 4), "bbox": bbox,
                      "x0": min(xs), "x1": max(xs), "cy": round((min(ys) + max(ys)) / 2, 1),
                      "h": round(max(ys) - min(ys), 1), "pass": ocr_pass})
    return boxes


def gap_bands(boxes: list[dict], width: int, height: int) -> list[tuple[int, int]]:
    """Vertical bands where the full-page pass may have missed a line.

    The detector sometimes drops a whole line (for example the first line of section 69 on
    page 42). Missed lines leave gaps that look like paragraph breaks, so every gap over
    FILL_RATIO times the median pitch is re-read, plus a band below the last line.
    """
    body = sorted((b for b in boxes if b["x1"] - b["x0"] > 0.25 * width), key=lambda b: b["cy"])
    if len(body) < 3:
        return []
    pitch = float(np.median([b["cy"] - a["cy"] for a, b in zip(body, body[1:])]))
    bands = []
    for a, b in zip(body, body[1:]):
        if b["cy"] - a["cy"] > FILL_RATIO * pitch:
            bands.append((a["cy"] + a["h"] / 2 - FILL_PAD, b["cy"] - b["h"] / 2 + FILL_PAD))
    last = body[-1]
    bands.append((last["cy"] + last["h"] / 2 - FILL_PAD, last["cy"] + 3 * pitch))
    return [(max(0, int(y0)), min(height, int(y1))) for y0, y1 in bands if y1 - y0 > 0.5 * pitch]


def overlaps(new: dict, old: dict) -> bool:
    y_overlap = min(new["cy"] + new["h"] / 2, old["cy"] + old["h"] / 2) - max(new["cy"] - new["h"] / 2,
                                                                          old["cy"] - old["h"] / 2)
    x_overlap = min(new["x1"], old["x1"]) - max(new["x0"], old["x0"])
    return y_overlap > 0.3 * min(new["h"], old["h"]) and x_overlap > 0.3 * min(new["x1"] - new["x0"],
                                                                              old["x1"] - old["x0"])


def fill_gaps(pixels, boxes: list[dict]) -> list[dict]:
    """Second pass: OCR each gap band on its own and keep boxes that are not already there."""
    height, width = pixels.shape[:2]
    engine = RapidOCR(det_unclip_ratio=DET_UNCLIP_RATIO, det_limit_side_len=FILL_DET_LIMIT)
    added: list[dict] = []
    for y0, y1 in gap_bands(boxes, width, height):
        result, _ = engine(pixels[y0:y1, :])
        for box in to_boxes(result, y_offset=y0, ocr_pass=2):
            if not any(overlaps(box, old) for old in boxes + added):
                added.append(box)
    return added


def ocr_page(pdf, index: int, first_pass: list[dict] | None = None) -> tuple[dict, str, str]:
    """OCR one 1-based page. Returns (page record, image sha256, reading-order text).

    With `first_pass` the full-page pass is taken from earlier output and only the gap-fill
    pass runs; the result is the same as a full run because both passes are deterministic.
    """
    image = pdf[index - 1].render(scale=SCALE).to_pil().convert("RGB")
    width, height = image.size
    pixels = np.array(image)
    if first_pass is None:
        engine = RapidOCR(det_unclip_ratio=DET_UNCLIP_RATIO, det_limit_side_len=max(width, height))
        result, _ = engine(pixels)
        first_pass = to_boxes(result)
    boxes = first_pass + fill_gaps(pixels, first_pass)
    record = {"page": index, "width": width, "height": height, "boxes": boxes}
    return record, sha256_bytes(pixels.tobytes()), "\n".join(reading_order(boxes)) + "\n"


def run(pages: list[int] | None, out_dir: Path, refill: bool = False) -> dict:
    data = SOURCE.read_bytes()
    source_hash = sha256_bytes(data)
    if source_hash != expected_source_hash():
        raise SystemExit(f"{SOURCE.name}: sha256 {source_hash} does not match the manifest")
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf = pdfium.PdfDocument(str(SOURCE))
    indices = pages or list(range(1, len(pdf) + 1))
    started = time.time()
    per_page = {}
    for index in indices:
        t0 = time.time()
        first_pass = None
        if refill:
            earlier = json.loads((out_dir / f"p{index:03d}.json").read_text(encoding="utf-8"))
            first_pass = [{**b, "pass": 1} for b in earlier["boxes"] if b.get("pass", 1) == 1]
        record, image_hash, text = ocr_page(pdf, index, first_pass)
        json_bytes = (json.dumps(record, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
        (out_dir / f"p{index:03d}.json").write_bytes(json_bytes)
        (out_dir / f"p{index:03d}.txt").write_bytes(text.encode("utf-8"))
        per_page[f"{index:03d}"] = {
            "image_sha256": image_hash, "image_size": [record["width"], record["height"]],
            "json_sha256": sha256_bytes(json_bytes), "txt_sha256": sha256_bytes(text.encode("utf-8")),
            "boxes": len(record["boxes"]),
            "gap_fill_boxes": sum(1 for b in record["boxes"] if b["pass"] == 2),
            "seconds": round(time.time() - t0, 1),
        }
        print(f"page {index:3d}: {len(record['boxes']):3d} boxes "
              f"({per_page[f'{index:03d}']['gap_fill_boxes']} from gap fill), "
              f"{per_page[f'{index:03d}']['seconds']} s", flush=True)
    manifest = {
        "source_file": "data/raw/" + SOURCE.name,
        "source_sha256": source_hash,
        "run_date": date.today().isoformat(),
        "python": platform.python_version(),
        "libraries": {name: version(name) for name in LIBRARIES},
        "settings": {"render_scale": SCALE, "det_unclip_ratio": DET_UNCLIP_RATIO,
                     "det_limit_side_len": "max(image width, image height)", "colour": "RGB",
                     "gap_fill": {"ratio_to_median_pitch": FILL_RATIO, "band_pad_px": FILL_PAD,
                                  "det_limit_side_len": FILL_DET_LIMIT}},
        "mode": "refill (pass 1 reused from earlier output)" if refill else "full",
        "pages": len(per_page),
        "total_seconds": round(time.time() - started, 1),
        "per_page": per_page,
    }
    (out_dir / "ocr_manifest.json").write_bytes((json.dumps(manifest, indent=1) + "\n").encode("utf-8"))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pages", help="comma-separated 1-based pages (default: all)")
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--refill", action="store_true",
                        help="reuse pass-1 boxes from existing output and run only the gap-fill pass")
    args = parser.parse_args()
    pages = [int(p) for p in args.pages.split(",")] if args.pages else None
    manifest = run(pages, args.out_dir, refill=args.refill)
    print(f"{manifest['pages']} pages in {manifest['total_seconds']} s -> {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
