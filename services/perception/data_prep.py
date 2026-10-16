"""
Data preparation pipeline — drone detector training

What it does:
    Converts each source dataset to YOLO format (one .txt per image, each line:
    class_id cx cy w h with normalized coordinates), then merges all sources into
    a unified train/val/test split.

Why it exists:
    Each dataset uses a different annotation format (Anti-UAV JSON, COCO JSON,
    or already-YOLO). This script is the single conversion boundary — training
    code only ever sees YOLO format, regardless of what the raw data looks like.

Usage:
    python services/perception/data_prep.py --config models/configs/yolo_data.yaml

Outputs:
    data/raw/merged/
        train/images/, train/labels/
        val/images/,   val/labels/
        test/images/,  test/labels/
    models/configs/yolo_data.yaml  (dataset config for YOLO training)

Decision: single class (drone only).
    Bird / aircraft multi-class is deferred post-baseline.
    See decision-log.md for rationale.
"""

import json
import os
import random
import shutil
import argparse
import xml.etree.ElementTree as ET
from pathlib import Path
from dataclasses import dataclass, field

try:
    import cv2
    _CV2_AVAILABLE = True
except ImportError:
    _CV2_AVAILABLE = False


# ── Constants ─────────────────────────────────────────────────────────────────

DRONE_CLASS_ID = 0   # single class: drone

# Train/val/test proportions must sum to 1.0
SPLIT_RATIOS = {"train": 0.80, "val": 0.10, "test": 0.10}

RANDOM_SEED = 42     # reproducible splits — same seed = same split every run


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class AnnotatedSample:
    """One image + its YOLO-format bounding boxes, ready to write."""
    image_path: Path          # absolute path to source image
    boxes: list[tuple]        # list of (class_id, cx, cy, w, h) — all normalized [0,1]
    # boxes is empty if the image is a hard negative (no drone present)


# ── Format converters ─────────────────────────────────────────────────────────

def load_mav_vid(dataset_dir: Path) -> list[AnnotatedSample]:
    """
    MAV-VID is already in YOLO format: images/ and labels/ directories.
    Walk both directories, pair image and label files by stem, return samples.

    If a label file is missing for an image, that image is skipped — MAV-VID
    has complete annotations so this shouldn't happen, but it's safer to skip
    than to silently add a mislabeled negative.
    """
    samples = []
    images_dir = dataset_dir / "images"
    labels_dir = dataset_dir / "labels"

    for img_path in sorted(images_dir.glob("**/*.jpg")) + sorted(images_dir.glob("**/*.png")):
        # Reconstruct the label path: same relative structure under labels/
        relative = img_path.relative_to(images_dir)
        label_path = labels_dir / relative.with_suffix(".txt")

        if not label_path.exists():
            continue

        boxes = _parse_yolo_label(label_path)
        samples.append(AnnotatedSample(image_path=img_path, boxes=boxes))

    print(f"[mav-vid] Loaded {len(samples)} samples")
    return samples


def load_dut_anti_uav(dataset_dir: Path) -> list[AnnotatedSample]:
    """
    DUT Anti-UAV detection subset.
    Actual structure (confirmed on download):
        {split}/img/*.jpg   — images
        {split}/xml/*.xml   — Pascal VOC XML annotations, one per image

    NOTE: Original data/README.md assumed COCO JSON — that was wrong.
    The actual format is Pascal VOC XML with image dimensions embedded in <size>.

    VOC bounding boxes are [xmin, ymin, xmax, ymax] in pixel coordinates.
    Conversion to YOLO: cx = (xmin+xmax)/2 / img_w, cy = (ymin+ymax)/2 / img_h,
    w = (xmax-xmin) / img_w, h = (ymax-ymin) / img_h.

    We load all splits and re-split ourselves for consistent proportions
    across the merged dataset (same approach as other loaders).
    """
    samples = []

    for split_name in ["train", "val", "test"]:
        img_dir = dataset_dir / split_name / "img"
        xml_dir = dataset_dir / split_name / "xml"
        if not img_dir.exists():
            continue

        for img_path in sorted(img_dir.glob("*.jpg")):
            xml_path = xml_dir / img_path.with_suffix(".xml").name
            if not xml_path.exists():
                # Missing annotation — skip rather than silently mislabel
                continue

            tree = ET.parse(xml_path)
            root = tree.getroot()

            # Image dimensions are embedded in the XML — no need to open the image
            size = root.find("size")
            img_w = int(size.find("width").text)
            img_h = int(size.find("height").text)

            boxes = []
            for obj in root.findall("object"):
                bbox = obj.find("bndbox")
                xmin = int(bbox.find("xmin").text)
                ymin = int(bbox.find("ymin").text)
                xmax = int(bbox.find("xmax").text)
                ymax = int(bbox.find("ymax").text)
                # VOC (absolute corners) -> YOLO (normalized center)
                cx = ((xmin + xmax) / 2) / img_w
                cy = ((ymin + ymax) / 2) / img_h
                w  = (xmax - xmin) / img_w
                h  = (ymax - ymin) / img_h
                boxes.append((DRONE_CLASS_ID, cx, cy, w, h))

            # If no <object> elements: hard negative (empty boxes list is valid)
            samples.append(AnnotatedSample(image_path=img_path, boxes=boxes))

    print(f"[dut-anti-uav] Loaded {len(samples)} samples")
    return samples


def _extract_anti_uav300_frames(seq_dir: Path) -> Path:
    """
    Extract visible.mp4 → visible/visibleI{NNNN}.jpg for one sequence.
    Returns the path to the frames directory.

    Naming convention matches the bundled framecut.py: visibleI0000.jpg, visibleI0001.jpg, ...
    Idempotent: if visible/ already contains .jpg files, extraction is skipped.

    Why here and not in framecut.py:
        framecut.py hardcodes './datasets/Anti-UAV-RGBT' which doesn't match our layout.
        data_prep.py is the single conversion boundary — all raw → YOLO conversion
        happens here, including frame extraction. See decision-log.md.
    """
    frames_dir = seq_dir / "visible"
    if frames_dir.exists() and any(frames_dir.glob("*.jpg")):
        return frames_dir  # Already extracted — skip

    video_path = seq_dir / "visible.mp4"
    frames_dir.mkdir(exist_ok=True)

    cap = cv2.VideoCapture(str(video_path))
    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        out_path = frames_dir / f"visibleI{frame_idx:04d}.jpg"
        cv2.imwrite(str(out_path), frame)
        frame_idx += 1
    cap.release()
    return frames_dir


def load_anti_uav300_rgb(dataset_dir: Path) -> list[AnnotatedSample]:
    """
    Anti-UAV300 RGB sequences.
    Actual structure (confirmed on download):
        anti-uav300/Anti-UAV-RGBT/{train,val,test}/{sequence_name}/
            visible.json   — {"exist": [1,1,0,...], "gt_rect": [[x,y,w,h],...]}
            visible.mp4    — frames extracted to visible/visibleI{NNNN}.jpg

    NOTE: Original code was wrong on multiple counts:
        - File was "RGB_label.json", actual is "visible.json"
        - exist/gt_rect are top-level parallel lists, not per-frame dicts
        - bbox format is [x, y, w, h] (top-left + size), not [x1, y1, x2, y2]
        - Frames must be extracted from .mp4 first (done by _extract_anti_uav300_frames)

    Requires cv2 for frame extraction. Skipped entirely if cv2 is unavailable.
    """
    if not _CV2_AVAILABLE:
        print("[anti-uav300] SKIP — cv2 not installed. Run: pip install opencv-python")
        return []

    samples = []
    rgbt_dir = dataset_dir / "Anti-UAV-RGBT"

    for split_name in ["train", "val", "test"]:
        split_dir = rgbt_dir / split_name
        if not split_dir.exists():
            continue

        seq_dirs = sorted(d for d in split_dir.iterdir() if d.is_dir())
        print(f"  [anti-uav300/{split_name}] {len(seq_dirs)} sequences — extracting frames...")

        for seq_dir in seq_dirs:
            json_file = seq_dir / "visible.json"
            video_file = seq_dir / "visible.mp4"
            if not json_file.exists() or not video_file.exists():
                continue  # IR-only or incomplete sequence — skip

            # Extract mp4 → jpg frames (idempotent: skips if already done)
            frames_dir = _extract_anti_uav300_frames(seq_dir)

            with open(json_file) as f:
                labels = json.load(f)

            # exist and gt_rect are parallel lists: one entry per frame
            exist_list = labels["exist"]
            gt_rect_list = labels["gt_rect"]

            frame_files = sorted(frames_dir.glob("*.jpg"))
            if len(frame_files) != len(exist_list):
                print(f"  [anti-uav300] Skipping {seq_dir.name}: "
                      f"{len(frame_files)} frames vs {len(exist_list)} labels")
                continue

            # Read image dimensions from first frame (once per sequence, not per frame)
            first = cv2.imread(str(frame_files[0]))
            if first is None:
                continue
            img_h, img_w = first.shape[:2]

            for frame_path, exist, rect in zip(frame_files, exist_list, gt_rect_list):
                if not exist:
                    # exist=0: drone not visible — hard negative
                    samples.append(AnnotatedSample(image_path=frame_path, boxes=[]))
                    continue

                x, y, w_px, h_px = rect   # top-left x,y + pixel width,height
                if w_px <= 0 or h_px <= 0:
                    # Degenerate bbox (occasionally present) — treat as negative
                    samples.append(AnnotatedSample(image_path=frame_path, boxes=[]))
                    continue

                # [x, y, w, h] top-left -> YOLO [cx, cy, w, h] normalized
                cx = (x + w_px / 2) / img_w
                cy = (y + h_px / 2) / img_h
                nw = w_px / img_w
                nh = h_px / img_h
                samples.append(AnnotatedSample(
                    image_path=frame_path,
                    boxes=[(DRONE_CLASS_ID, cx, cy, nw, nh)]
                ))

    print(f"[anti-uav300] Loaded {len(samples)} samples")
    return samples


def load_drone_vs_bird(dataset_dir: Path) -> list[AnnotatedSample]:
    """
    Drone-vs-Bird (Roboflow YOLOv8 export).
    Actual structure (confirmed on download):
        {split}/images/*.jpg
        {split}/labels/*.txt
    where split is 'train', 'valid', or 'test'.

    NOTE: Original code had this inverted as images/{split}/ — wrong.
    The Roboflow YOLOv8 export puts the split name first.

    Two classes in source: drone (0) and bird (1).
    Bird annotations are dropped; bird images are kept as hard negatives.
    Why: the model must learn that birds are not drones.
    """
    samples = []

    for split in ["train", "valid", "val", "test"]:
        images_dir = dataset_dir / split / "images"
        labels_dir = dataset_dir / split / "labels"
        if not images_dir.exists():
            continue

        for img_path in sorted(images_dir.glob("*.jpg")) + sorted(images_dir.glob("*.png")):
            label_path = labels_dir / img_path.with_suffix(".txt").name
            boxes = []

            if label_path.exists():
                raw_boxes = _parse_yolo_label(label_path)
                # Keep only drone class (class_id == 0); drop bird (class_id == 1)
                boxes = [(DRONE_CLASS_ID, cx, cy, w, h)
                         for class_id, cx, cy, w, h in raw_boxes
                         if class_id == 0]

            samples.append(AnnotatedSample(image_path=img_path, boxes=boxes))

    print(f"[drone-vs-bird] Loaded {len(samples)} samples")
    return samples


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_yolo_label(label_path: Path) -> list[tuple]:
    """
    Read a YOLO .txt file and return a list of (class_id, cx, cy, w, h) tuples.
    Empty file (hard negative) returns an empty list — this is valid.
    """
    boxes = []
    with open(label_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) != 5:
                continue
            class_id = int(parts[0])
            cx, cy, w, h = map(float, parts[1:])
            boxes.append((class_id, cx, cy, w, h))
    return boxes


def _write_yolo_label(label_path: Path, boxes: list[tuple]) -> None:
    """Write YOLO .txt annotation file. Empty file = hard negative."""
    label_path.parent.mkdir(parents=True, exist_ok=True)
    with open(label_path, "w") as f:
        for class_id, cx, cy, w, h in boxes:
            f.write(f"{class_id} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}\n")


# ── Merge and split ───────────────────────────────────────────────────────────

def merge_and_split(
    all_samples: list[AnnotatedSample],
    output_dir: Path,
    ratios: dict = SPLIT_RATIOS,
    seed: int = RANDOM_SEED,
) -> dict[str, int]:
    """
    Shuffle all samples, split into train/val/test, copy images and write
    YOLO labels into the merged output directory.

    Returns counts per split for logging.

    Why shuffle before split: source datasets have different characteristics.
    Without shuffling, the first 80% of the merged list would be entirely MAV-VID,
    val would be DUT-Anti-UAV, and test would be Anti-UAV300. That's not a valid
    train/val/test split — it tests generalization to dataset distribution, not
    to the drone detection task.
    """
    rng = random.Random(seed)
    rng.shuffle(all_samples)

    n = len(all_samples)
    n_train = int(n * ratios["train"])
    n_val = int(n * ratios["val"])
    # Test gets the remainder to avoid off-by-one from float truncation
    splits = {
        "train": all_samples[:n_train],
        "val":   all_samples[n_train : n_train + n_val],
        "test":  all_samples[n_train + n_val :],
    }

    counts = {}
    for split_name, samples in splits.items():
        img_out = output_dir / split_name / "images"
        lbl_out = output_dir / split_name / "labels"
        img_out.mkdir(parents=True, exist_ok=True)
        lbl_out.mkdir(parents=True, exist_ok=True)

        for i, sample in enumerate(samples):
            # Unique filename: source stem + index to avoid collisions across datasets
            stem = f"{sample.image_path.stem}_{i:06d}"
            ext = sample.image_path.suffix

            dest_img = img_out / f"{stem}{ext}"
            dest_lbl = lbl_out / f"{stem}.txt"

            shutil.copy2(sample.image_path, dest_img)
            _write_yolo_label(dest_lbl, sample.boxes)

        counts[split_name] = len(samples)
        print(f"[merge] {split_name}: {len(samples)} samples -> {split_name}/")

    return counts


# ── Dataset YAML ──────────────────────────────────────────────────────────────

def write_yolo_yaml(output_dir: Path, yaml_path: Path) -> None:
    """
    Write the YOLO dataset config yaml.
    YOLO training reads this to find images and class names.

    path must be absolute so the training script works regardless of cwd.
    """
    abs_output = output_dir.resolve()
    yaml_content = f"""# YOLO dataset config — generated by data_prep.py
# DO NOT EDIT manually. Re-run data_prep.py to regenerate.

path: {abs_output}       # dataset root
train: train/images
val:   val/images
test:  test/images

nc: 1                   # number of classes
names: ["drone"]        # class 0 = drone

# Multi-class (drone / bird / aircraft) is deferred post-baseline.
# See decision-log.md for the single-class rationale.
"""
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    with open(yaml_path, "w") as f:
        f.write(yaml_content)
    print(f"[yaml] Dataset config written to {yaml_path}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Prepare drone detection datasets for YOLO training")
    parser.add_argument("--raw-dir",    type=Path, default=Path("data/raw"),          help="Root directory containing source datasets")
    parser.add_argument("--output-dir", type=Path, default=Path("data/raw/merged"),   help="Output directory for merged YOLO dataset")
    parser.add_argument("--yaml-path",  type=Path, default=Path("models/configs/yolo_data.yaml"), help="Output path for YOLO dataset yaml")
    args = parser.parse_args()

    all_samples: list[AnnotatedSample] = []

    # Load each dataset. Skip gracefully if not yet downloaded.
    # Drone-vs-Bird: Roboflow exports into a versioned subdirectory
    # (e.g. drone-vs-bird.v2i.yolov8/). Auto-detect it instead of hardcoding.
    dvb_root = args.raw_dir / "drone-vs-bird"
    dvb_subdirs = [d for d in dvb_root.glob("*.yolov8") if d.is_dir()] if dvb_root.exists() else []
    dvb_dir = dvb_subdirs[0] if dvb_subdirs else dvb_root  # fallback to root if no subdir found

    loaders = [
        (args.raw_dir / "mav-vid",      load_mav_vid),
        (args.raw_dir / "dut-anti-uav", load_dut_anti_uav),
        (args.raw_dir / "anti-uav300",  load_anti_uav300_rgb),
        (dvb_dir,                        load_drone_vs_bird),
    ]

    for dataset_dir, loader_fn in loaders:
        if not dataset_dir.exists():
            print(f"[skip] {dataset_dir} not found — download it first")
            continue
        samples = loader_fn(dataset_dir)
        all_samples.extend(samples)

    if not all_samples:
        print("No datasets found. Download at least one dataset to data/raw/ first.")
        return

    print(f"\n[total] {len(all_samples)} samples from all sources")

    # Log class distribution: samples with at least one drone box vs hard negatives
    n_positive = sum(1 for s in all_samples if s.boxes)
    n_negative = len(all_samples) - n_positive
    print(f"[stats] positive (drone present): {n_positive}")
    print(f"[stats] negative (background): {n_negative}")
    print(f"[stats] ratio: {n_positive / len(all_samples):.1%} positive\n")

    counts = merge_and_split(all_samples, args.output_dir)
    write_yolo_yaml(args.output_dir, args.yaml_path)

    print(f"\nDone. Split counts: {counts}")
    print("Next: dvc add data/raw/merged && git add data/raw/merged.dvc")


if __name__ == "__main__":
    main()
