# Data Sources — Edge Base-Defense Copilot

This file documents every dataset used for drone detector training and evaluation.
All data goes through `services/perception/data_prep.py` before training.
Processed data is DVC-tracked; raw downloads are in `data/raw/` (not in git).

---

## Dataset Selection Notes

We need visible-light (EO) imagery only. The Anti-UAV410 and Anti-UAV600 datasets
are thermal infrared (IR) only — excluded. Anti-UAV300 includes an RGB component —
only that component is used.

All datasets are normalized to single-class YOLO format: `class_id=0` maps to `drone`.
Multi-class capability (bird, aircraft discrimination) is deferred to post-baseline.

---

## 1. MAV-VID — Multirotor Aerial Vehicle VID

| Field | Value |
|---|---|
| Authors | Rodriguez-Ramos et al. |
| Format | YOLO txt (already normalized — no conversion needed) |
| Images | 40,232 (53 train videos: 29,500 images, 11 val videos: 10,732 images) |
| Classes | 1 (`drone`) |
| Resolution | Mixed (handheld, ground surveillance, drone-to-drone) |
| License | See Kaggle page (permissive for research) |
| Source | Kaggle: `alejodosr/multirotor-aerial-vehicle-vid-mavvid-dataset` |
| Notes | Benchmark conversion scripts at github.com/KostadinovShalon/UAVDetectionTrackingBenchmark |

**Why included:** largest of the candidate datasets, already in YOLO format, single-class,
diverse capture conditions (handheld, fixed camera, drone-to-drone). Forms the backbone
of the training set.

**Download command:**
```bash
# STUB — replace with actual download once Kaggle CLI is confirmed available
# kaggle datasets download -d alejodosr/multirotor-aerial-vehicle-vid-mavvid-dataset -p data/raw/mav-vid
```

---

## 2. DUT Anti-UAV — Dalian University Detection Dataset

| Field | Value |
|---|---|
| Authors | Zhao et al., Dalian University of Technology |
| Format | Detection: COCO JSON (assumed — verify on download) |
| Images | 10,000 (train: 5,200 / val: 2,600 / test: 2,200) |
| Videos | 20 tracking sequences (not used for detection training) |
| Classes | 1 (`drone`) |
| Resolution | Outdoor EO, mixed backgrounds |
| License | Apache-2.0 (GitHub) / MIT cited in paper — either is permissive |
| Source | github.com/wangdongdut/DUT-Anti-UAV |
| Paper | arxiv.org/abs/2205.10851 |

**Why included:** 10K annotated EO images, permissive license, structured detection splits.
Adds outdoor background diversity (tree lines, low contrast skies) that complements MAV-VID.

**Download command:**
```bash
# STUB — manual Google Drive download, links on GitHub repo
# Extract to data/raw/dut-anti-uav/
```

---

## 3. Anti-UAV300 — RGB Component Only

| Field | Value |
|---|---|
| Authors | Jiang et al., via github.com/ZhaoJ9014/Anti-UAV |
| Format | JSON per-sequence ([x1,y1,x2,y2] + existence flag) |
| Images | 300+ video pairs; RGB subset only (~150 sequences) |
| Classes | 1 (`drone`) — existence flag used to filter "no target" frames |
| Resolution | Full HD 1920×1080 |
| License | Research/academic (check repo before commercial use) |
| Source | github.com/ZhaoJ9014/Anti-UAV (Google Drive, Baidu Drive, ModelScope) |
| Notes | IR-only versions (Anti-UAV410, Anti-UAV600) are excluded — EO detector only |

**Why included:** high-resolution video sequences, multi-scale UAVs, densely annotated.
The 580K+ bounding box count makes it the highest-annotation dataset after filtering for RGB.

**Format note:** raw annotations are JSON `[x1,y1,x2,y2]`. `data_prep.py` converts to
YOLO normalized format `[cx, cy, w, h]`. Frames where `exist=0` are kept as negative
examples (no annotation file → YOLO treats as background).

**Download command:**
```bash
# STUB — download RGB sequences from ModelScope or Google Drive link on GitHub
# Extract to data/raw/anti-uav300/
```

---

## 4. Drone-vs-Bird (Roboflow / CC BY 4.0)

| Field | Value |
|---|---|
| Authors | "dam" on Roboflow Universe (re-hosted from original challenge) |
| Format | YOLO txt (pre-converted on Roboflow) |
| Images | ~2,920 |
| Classes | 2 (`drone`, `bird`) — bird labels converted to negatives for single-class model |
| Resolution | Mixed |
| License | CC BY 4.0 — full permissive, commercial and derivative use allowed |
| Source | universe.roboflow.com/dam-tpuul/drone-vs-bird-lanzg |
| Notes | Official challenge dataset requires DUA signing (non-commercial). This Roboflow re-host is CC BY 4.0 and avoids the DUA requirement. |

**Why included:** the only dataset with explicit bird labels. Even in single-class mode,
the bird images serve as hard negatives (background class in training). In multi-class mode,
this enables false-alarm-rate measurement on the held-out bird split.

**Class handling:** `bird` annotations are dropped for the single-class model training run.
The bird images themselves are retained — they appear as no-annotation images (hard negatives).

**Download command:**
```bash
# STUB — Roboflow CLI or manual download
# rf export --format yolov8 --location data/raw/drone-vs-bird
```

---

## Merged Dataset Summary

| Dataset | Images (approx) | Split contribution |
|---|---|---|
| MAV-VID | 40,232 | ~67% train backbone |
| DUT Anti-UAV | 10,000 | Detection-split train/val |
| Anti-UAV300 (RGB) | ~30,000 est. (frame extracted) | High-res sequences |
| Drone-vs-Bird | 2,920 | Hard negatives + small supplement |
| **Total (est.)** | **~83,000** | — |

Splits after merge: **80% train / 10% val / 10% test**, stratified by source dataset.
Each split is DVC-tracked as `data/raw/merged/{train,val,test}/`.

---

## Class Map

```yaml
# models/configs/yolo_data.yaml
nc: 1
names: ["drone"]
```

A bird or airplane detection in the raw source data is treated as background (no label).
Class expansion (drone / bird / aircraft) is a post-baseline decision — log it in decision-log.md
when you're ready to add it.

---

## DVC Tracking

```bash
dvc add data/raw/mav-vid data/raw/dut-anti-uav data/raw/anti-uav300 data/raw/drone-vs-bird
dvc add data/raw/merged
git add data/raw/*.dvc data/raw/.gitignore
```

DVC lock files record the exact dataset version used for each MLflow training run.
The `data_version` tag in MLflow is the DVC commit hash.
