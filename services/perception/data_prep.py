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
from pathlib import Path
from dataclasses import dataclass, field


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
    Expected structure: images/ + annotations/instances_{split}.json (COCO format).

    COCO bounding boxes are [x_min, y_min, width, height] in pixel coordinates.
    YOLO format requires [cx, cy, w, h] normalized by image dimensions.

    We load all splits (train/val/test) and re-split ourselves for consistent
    proportions across the merged dataset.
    """
    samples = []

    # DUT provides separate JSON per split — load all
    for split_name in ["train", "val", "test"]:
        ann_file = dataset_dir / "annotations" / f"instances_{split_name}.json"
        if not ann_file.exists():
            continue

        with open(ann_file) as f:
            coco = json.load(f)

        # Build lookup: image_id -> image metadata
        id_to_image = {img["id"]: img for img in coco["images"]}

        # Group annotations by image_id
        from collections import defaultdict
        anns_by_image: dict = defaultdict(list)
        for ann in coco["annotations"]:
            anns_by_image[ann["image_id"]].append(ann)

        for img_meta in coco["images"]:
            img_id = img_meta["id"]
            img_w = img_meta["width"]
            img_h = img_meta["height"]
            img_path = dataset_dir / split_name / "images" / img_meta["file_name"]

            if not img_path.exists():
                continue

            boxes = []
            for ann in anns_by_image[img_id]:
                x_min, y_min, w_px, h_px = ann["bbox"]
                # COCO -> YOLO: pixel absolute -> normalized center
                cx = (x_min + w_px / 2) / img_w
                cy = (y_min + h_px / 2) / img_h
                w = w_px / img_w
                h = h_px / img_h
                boxes.append((DRONE_CLASS_ID, cx, cy, w, h))

            samples.append(AnnotatedSample(image_path=img_path, boxes=boxes))

    print(f"[dut-anti-uav] Loaded {len(samples)} samples")
    return samples


def load_anti_uav300_rgb(dataset_dir: Path) -> list[AnnotatedSample]:
    """
    Anti-UAV300 — RGB sequences only.
    Each sequence is a directory containing:
        - frames as JPEG files (001.jpg, 002.jpg, ...)
        - RGB_label.json with per-frame annotations

    JSON structure per frame:
        {"exist": 0 or 1, "get_rect": [x1, y1, x2, y2]}
    where x1,y1 is top-left and x2,y2 is bottom-right in pixel coordinates.

    Frames where exist=0 are kept as hard negatives (empty boxes list).
    Frames where exist=1 but get_rect is None/empty are skipped.
    """
    samples = []

    # Each subdirectory is one sequence
    for seq_dir in sorted(dataset_dir.iterdir()):
        if not seq_dir.is_dir():
            continue

        label_file = seq_dir / "RGB_label.json"
        if not label_file.exists():
            continue  # IR-only sequence — skip

        with open(label_file) as f:
            labels = json.load(f)

        # labels is a list of {exist, get_rect} dicts, one per frame
        frame_files = sorted(seq_dir.glob("*.jpg"))
        if len(frame_files) != len(labels):
            # Frame count mismatch — skip sequence rather than misalign
            print(f"  [anti-uav300] Skipping {seq_dir.name}: "
                  f"{len(frame_files)} frames vs {len(labels)} labels")
            continue

        for frame_path, label in zip(frame_files, labels):
            exist = label.get("exist", 0)
            rect = label.get("get_rect")

            if not exist:
                # Hard negative — include image, no boxes
                samples.append(AnnotatedSample(image_path=frame_path, boxes=[]))
                continue

            if not rect or len(rect) != 4:
                continue  # Malformed annotation — skip frame

            x1, y1, x2, y2 = rect
            # Need image dimensions to normalize.
            # Reading image dimensions here would require PIL/cv2 for every frame —
            # too slow at scale. Anti-UAV300 is Full HD: hardcode 1920x1080.
            # STUB: replace with dynamic size read if sequences use other resolutions.
            img_w, img_h = 1920, 1080

            cx = ((x1 + x2) / 2) / img_w
            cy = ((y1 + y2) / 2) / img_h
            w = (x2 - x1) / img_w
            h = (y2 - y1) / img_h
            samples.append(AnnotatedSample(
                image_path=frame_path,
                boxes=[(DRONE_CLASS_ID, cx, cy, w, h)]
            ))

    print(f"[anti-uav300] Loaded {len(samples)} samples")
    return samples


def load_drone_vs_bird(dataset_dir: Path) -> list[AnnotatedSample]:
    """
    Drone-vs-Bird (Roboflow export, YOLO format).
    Structure: images/{train,val,test}/ + labels/{train,val,test}/

    This dataset has two classes: drone (0) and bird (1).
    Bird annotations are dropped — bird images are retained as hard negatives.

    Why keep bird images as negatives: the model must learn that birds are not drones.
    Simply dropping all bird images would remove hard negative signal.
    """
    samples = []

    for split in ["train", "valid", "val", "test"]:
        images_dir = dataset_dir / "images" / split
        labels_dir = dataset_dir / "labels" / split
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
    loaders = [
        (args.raw_dir / "mav-vid",       load_mav_vid),
        (args.raw_dir / "dut-anti-uav",  load_dut_anti_uav),
        (args.raw_dir / "anti-uav300",   load_anti_uav300_rgb),
        (args.raw_dir / "drone-vs-bird", load_drone_vs_bird),
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
