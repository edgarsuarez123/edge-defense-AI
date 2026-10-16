# Edge Base-Defense Copilot — Build Log

Newest entry first. Each entry: what shipped, what's next, any blockers.

---

## 2026-10-16 — Week 3 · ROS 2 Docker Stack Verified

### Done
- **6 issues found and fixed** in the Week 2 ROS 2 scaffold:
  - `setup.py`: removed extra whitespace in console_scripts entry points
  - `perception.Dockerfile`: removed ultralytics + mlflow pip install (saves ~500MB, those packages are never used inside the image)
  - `docker-compose.perception.yml`: replaced `network_mode: host` with named `ros2_net` bridge network (host mode doesn't work correctly on Docker Desktop/Windows)
  - `docker-compose.perception.yml`: added healthchecks to stub_publisher and stub_subscriber using `ros2 node list | grep -q <node>`
  - Healthcheck bug: `CMD-SHELL` uses `/bin/sh` which has no `source` — fixed to `CMD + /bin/bash -c`
  - Healthcheck timeout bumped to 10s and start_period to 20s (DDS registration takes ~5-10s)
- **Build verified**: `docker compose -f docker-compose.perception.yml build` — clean, no pip bloat
- **Pub/sub verified**: stub_publisher and stub_subscriber exchanged messages at 1 Hz with zero dropped frames, cycling BENIGN → UNKNOWN → LIKELY_THREAT
- **Topic verified**: `ros2 topic list` shows `/detections` via tools service
- **Health verified**: both services report `healthy` after 35s
- **2 new decision-log entries**: ROS 2 image slimming, bridge network over host mode

### Next (Week 3 continued)
- Download remaining datasets (Anti-UAV300 RGB, Drone-vs-Bird from Roboflow)
- Run `data_prep.py` once datasets are ready
- Run `train.py --mode baseline` → log to MLflow
- Run `train.py --mode finetune` → register drone-detector v1
- TensorRT INT8 quantization of best.pt
- Replace stub_publisher with real YOLOv8 + TensorRT inference node (subscribes to `/camera/image_raw`)

### Blockers
- MAV-VID unavailable on Kaggle; Bitbucket clone fails (pack corruption). Proceeding without it — DUT Anti-UAV + Anti-UAV300 + Drone-vs-Bird (~43K images) is sufficient.
- Anti-UAV300 and Drone-vs-Bird not yet downloaded.

---

## 2026-10-15 — Day 2 · Week 1 Close-Out

### Done
- Docker verification: postgres + redis + mlflow all healthy
- **Bugs found and fixed:**
  - Port conflicts: 5432 and 6379 occupied by other projects. Updated `docker-compose.dev.yml` to use host ports 5433 and 6380. Updated `.env` accordingly.
  - Migration files in wrong location: Alembic expects `migrations/versions/`, not `migrations/`. Moved `0001_initial_schema.py` to `migrations/versions/`.
  - MLflow startup command: YAML `>` folded scalar preserves newlines for more-indented lines, causing bash to treat each `--flag` as a separate command. Fixed by flattening to a single-line command with `>-`.
  - MLflow `db upgrade` on fresh DB: MLflow 2.16.2 migration ordering fails on a fresh schema. Removed the explicit `mlflow db upgrade` call — `mlflow server` handles DB initialization internally.
  - `mlflow` database not auto-created: PostgreSQL requires `CREATE DATABASE mlflow` before MLflow can initialize. Added to Quick Start.
- `alembic upgrade head` applied migration 0001 cleanly: 5 tables, 12 custom indexes, pgvector extension
- `seed.py` passed: all tables accepted inserts, CHECK constraints enforced, VECTOR(768) insert verified
- MLflow UI confirmed at localhost:5000 with PostgreSQL backend
- `README.md` written: problem, architecture diagram, demo scenario, tech stack table, status, quick start, repo structure

### Next
- Week 2 Step 6: dataset research — Anti-UAV, Drone-vs-Bird, MAV-VID sourcing + `data/README.md` ✓ (see Day 2 Week 2 entry below)
- Week 2 Step 7: `services/perception/data_prep.py` ✓
- Week 2 Step 8: YOLO baseline + Step 9: fine-tuning — scripts written, execution deferred until datasets downloaded
- Week 2 Step 10: ROS 2 Docker setup ✓ — Dockerfile + stub nodes written

---

## 2026-10-15 — Day 2 · Week 2

### Done
- **Step 6: Dataset research** — verified 4 EO-compatible datasets, documented in `data/README.md`
  - MAV-VID: 40,232 images, already YOLO format, single-class drone
  - DUT Anti-UAV: 10,000 images, Apache-2.0 / MIT, EO visible-light
  - Anti-UAV300: RGB component only (~30K frames est.), Anti-UAV410/600 excluded (IR-only)
  - Drone-vs-Bird (Roboflow CC BY 4.0): 2,920 images, cleaner license than official DUA version
  - Anti-UAV410 / Anti-UAV600: excluded — thermal IR only, useless for an EO (camera) detector

- **Step 7: Data preparation pipeline** — `services/perception/data_prep.py`
  - Converts Anti-UAV300 JSON, DUT-Anti-UAV COCO JSON, MAV-VID YOLO, Drone-vs-Bird YOLO
  - 80/10/10 train/val/test split with RANDOM_SEED=42 (reproducible)
  - Generates `models/configs/yolo_data.yaml` for YOLO training
  - Bird annotations from Drone-vs-Bird dropped; bird images retained as hard negatives

- **Step 8+9: Training and eval scripts**
  - `services/perception/train.py` — baseline (COCO pretrained eval) + finetune mode, MLflow logging, model registry registration
  - `services/perception/eval.py` — standalone eval, FPS measurement, CI gate comparison against registered baseline
  - Both scripts ready; actual runs deferred until datasets are downloaded

- **Step 10: ROS 2 Docker setup**
  - `infra/docker/perception.Dockerfile` — ros:humble base, colcon build of perception_nodes package
  - `docker-compose.perception.yml` — separate compose for ROS 2 stack (Ubuntu base isolation)
  - `services/perception/ros2_nodes/` — complete ROS 2 package: package.xml, setup.py, stub_publisher, stub_subscriber
  - Stub publisher: publishes JSON-encoded fake detections to /detections at 1 Hz
  - Stub subscriber: reads /detections, logs received messages
  - Real YOLO detector replaces stub_publisher in Week 3

- **5 new decision-log entries**: model size, single-class, dataset license, ROS 2 message type, separate compose

### Next (Week 3)
- Download datasets: MAV-VID (Kaggle), DUT Anti-UAV (Google Drive), Anti-UAV300 RGB, Drone-vs-Bird (Roboflow)
- Run `data_prep.py` to produce merged YOLO dataset, DVC track the output
- Run `train.py --mode baseline` → log to MLflow
- Run `train.py --mode finetune` → log to MLflow, register drone-detector v1
- Build `docker-compose.perception.yml` and verify `ros2 topic list`, `ros2 topic echo /detections`
- TensorRT INT8 quantization of best.pt
- Replace stub_publisher with real YOLOv8 + TensorRT inference node

### Blockers
- Datasets not yet downloaded (manual download from Google Drive, Kaggle — requires human action)
- Kaggle CLI needs API key to automate MAV-VID download

---

## 2026-10-15 — Day 1 · Week 1

### Done
- Repo directory structure created (matches architecture-design.md §10.1)
- `.env.example` — all environment variables documented with no values
- `.gitignore` — Python, .env, DVC-tracked data, model binaries, editor/OS files
- `docker-compose.dev.yml` — PostgreSQL 16 (pgvector/pgvector:pg16) + Redis 7 with healthchecks
- Alembic setup: `alembic.ini`, `migrations/env.py` (load_dotenv + sync psycopg2 driver), `script.py.mako`
- Migration `0001_initial_schema.py` — 5 tables, all indexes, CHECK constraints
  - `incidents`, `tracks`, `pending_actions`, `audit_log`, `document_chunks`
  - pgvector extension + VECTOR(768) hnsw index for RAG
  - Compound partial index `idx_tracks_unprocessed_threats` for Redis fallback polling
  - `synced_at` removed from tracks (tracks are local-only per decision log)
- `seed.py` — smoke test for all tables and constraints (uses load_dotenv)
- Decision log updated: 4 new entries (CHECK constraints, synced_at removal, compound index, Alembic sync driver)
- Syntax check passed on all Python files

### Next (carried to Day 2 — all completed)
- `progress.md` ✓
- `docker-compose.edge.yml` ✓ resource-constrained version (4 CPUs, 16GB RAM, GPU 0.75)
- Per-service `requirements.txt` files ✓
- MLflow service added to dev compose + `models/configs/mlflow_config.py` ✓
- DVC init + local remote configured ✓
- Docker verification ✓ (bugs fixed — see Day 2 entry)
- Git init + first commit ✓

### Blockers
None.

---
