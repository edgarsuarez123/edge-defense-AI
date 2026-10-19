# Edge Base-Defense Copilot — Build Log

Newest entry first. Each entry: what shipped, what's next, any blockers.

---

## 2026-10-18 — Week 3 · YOLO Inference Node + Full Pipeline (COMPLETE)

### Done
- **Designed and implemented real YOLO ROS 2 inference pipeline** (grilling session → plan → implementation):
  - `services/perception/ros2_nodes/perception_nodes/image_replay.py` — replays test set images as `sensor_msgs/Image` at configurable FPS; manual numpy conversion (no cv_bridge); depth=1 publisher
  - `services/perception/ros2_nodes/perception_nodes/yolo_detector.py` — subscribes to `/camera/image_raw` (depth=1, BEST_EFFORT), runs `YOLO.predict()`, publishes per-frame JSON to `/detections`
  - Detection format: `{frame_id, timestamp, detections: [{confidence, classification, bbox, sensor_source}]}`
  - `stub_subscriber.py` updated to parse new nested format
- **Updated `setup.py`** — added `image_replay` and `yolo_detector` entry points
- **Updated `perception.Dockerfile`**:
  - Added `libgl1` (ultralytics pulls opencv-python which links libGL.so.1)
  - Reordered: colcon build BEFORE pip install (avoids setuptools/ament_python conflict)
  - Two separate pip RUN layers: torch (~2GB) + ultralytics (~200MB) — Docker layer cache efficiency
  - Added `NVIDIA_VISIBLE_DEVICES=all`, `NVIDIA_DRIVER_CAPABILITIES=compute,utility`
- **Updated `docker-compose.perception.yml`** — added `image_replay` + `yolo_detector` services with healthchecks; GPU reservation for detector; stub_publisher moved to `profiles: ["stubs"]`
- **Fixed MLflow resume bug** — Ultralytics' MlflowCallback conflicts with our run on resume; fixed by temporarily popping `MLFLOW_TRACKING_URI` from env during `model.train(resume=True)`, restored in `finally`
- **Training completed** — epoch 50, mAP50=0.9944, mAP50-95=0.7486
- **best.pt copied** to `weights/best.pt` for Docker volume mount
- **MLflow run logged** — finetune metrics logged, `drone-detector v1` registered using v2-compatible `create_model_version()` API (MLflow client 3.17.1 vs server 2.16.2 mismatch workaround)
- **GPU passthrough verified** — `torch.cuda.is_available()` = True inside yolo_detector container
- **End-to-end pipeline verified** — real drone detections flowing: confidence 0.26–0.85 per frame
- **4 new decision-log entries**: per-frame JSON format, manual numpy conversion, QoS depth=1 BEST_EFFORT, separate Docker pip layers

### Measured FPS — Week 3 Gate Numbers
| Topic | Rate | Bottleneck |
|-------|------|------------|
| `/camera/image_raw` | ~3 FPS | WSL2 Windows filesystem mount (cv2.imread latency) |
| `/detections` | ~3 FPS | Input-limited (same as above) |

**Note:** YOLO inference is NOT the bottleneck. WSL2 Docker volume mount throttles disk reads to ~330ms/frame. With AirSim/Gazebo camera feed (Week 5-6), inference will be the constraint (~60+ FPS on RTX-class GPU at 640x640).

### What is NOT done yet
- Test set eval (`eval.py` on held-out 30,983 images) — CI gate threshold not yet set
- DVC tracking of merged dataset (`dvc add data/raw/merged`)
- TensorRT INT8 quantization of best.pt
- Bird false-positive eval (run inference on bird-only subset, count FPs)
- Extend training to epoch 75-100 (mAP50-95 still gaining at epoch 50, worth continuing)

### Next
1. **DVC track merged dataset** — `dvc add data/raw/merged`
2. **Run test set eval** — `eval.py` on 30,983 held-out images; log mAP to MLflow (CI gate threshold)
3. **TensorRT INT8 quantization** — `best.pt` -> `best.engine`; measure FPS before/after
4. **FastAPI service skeleton** — Week 4 gate: `/incidents`, `/tracks`, `/detections/stream` SSE

### Blockers
None — pipeline running, model registered, gate numbers measured.

---

## 2026-10-17 — Week 3 · YOLO Training + Training Deep Dive

### Done
- **Diagnosed CPU vs GPU training bug** — system Python 3.14 has `torch+cpu` only. Identified correct GPU venv: `.venv-training/` (torch 2.6.0+cu124, CUDA=True). All future training must use `.venv-training/Scripts/python.exe`.
- **Patched checkpoint epoch count** — `last.pt` had `epochs=30` baked in. Patched `ckpt['train_args']['epochs']` to 50 via torch.load/save before resuming. Backup saved as `last_epoch15_backup.pt`.
- **Resumed training on GPU** — training restarted from epoch 17, confirmed GPU active (`3.77–4.98G VRAM`, ~10 it/s, ~25 min/epoch). Stopped cleanly at epoch 37 for the day.
- **`FINETUNE_EPOCHS` updated 30 → 50** in `train.py`
- **Understood training metrics** — full walkthrough of mAP50, mAP50-95, IoU, box loss, and what each means for drone detection in a defense context.
- **Confirmed dataset is correct** — Drone-vs-Bird images ARE in the merged training set as hard negatives (bird labels dropped, bird images retained). Test set (30,983 images) is confirmed held out and untouched.

### Metrics at epoch 37 (best so far)
| mAP50 | mAP50-95 | Precision | Recall | Val box_loss |
|-------|----------|-----------|--------|-------------|
| 0.9943 | 0.7241 | 0.994 | 0.987 | 0.974 |

Trend: mAP50-95 gaining ~0.005 per 5 epochs, val box_loss still falling — not plateaued. Worth continuing to epoch 50 or 100.

### What is NOT done yet
- Epochs 38–50 (or 100) not run — paused for the day, resume with command below
- `best.pt` not logged to MLflow — no registered model yet
- Baseline mAP not logged to MLflow — CI gate has no threshold yet
- TensorRT INT8 quantization not started
- Test set eval (`eval.py` on held-out 30,983 images) not run
- DVC tracking of merged dataset not done (`dvc add data/raw/merged`)
- `stub_publisher` not replaced with real YOLO inference ROS 2 node
- Bird false-positive eval not written (run inference on bird-only subset, count FPs)

### To resume training tomorrow
```bash
cd "C:/Users/Edgar/Defense Edge AI"
.venv-training/Scripts/python.exe services/perception/train.py \
  --mode resume \
  --last-pt runs/detect/runs/train/drone-finetune/weights/last.pt \
  --data models/configs/yolo_data.yaml
```
**Always use `.venv-training/Scripts/python.exe` — system python is CPU-only.**

### Tomorrow (priority order)
1. **Resume training** — let it run to epoch 50. Check epoch 45–50 gain; if still >0.004/5 epochs, patch and extend to 100.
2. **DVC track merged dataset** — `dvc add data/raw/merged` before training finishes so MLflow data_version tag is meaningful.
3. **Replace stub_publisher** — write the real YOLOv8 ROS 2 inference node (subscribes to `/camera/image_raw`, publishes to `/detections`). Can be done while training runs.
4. **Post-training** (after epoch 50): log baseline to MLflow, register `best.pt` as `drone-detector v1`, run test set eval, begin TensorRT INT8 quantization.

### Blockers
None — training paused intentionally, resume command above.

---

## 2026-10-16 — Week 3 · Agents Service Skeleton (ahead of schedule)

### Done
- **Full `services/agents/` skeleton implemented** (11 files):
  - `state.py` — `IncidentState` + `TrackRecord` TypedDicts; `completed_agents` with `Annotated[list[str], operator.add]` reducer for parallel fan-in
  - `prompts/threat_analyst.py`, `ops_agent.py`, `reporting_agent.py`, `supervisor.py` — system prompt constants with injection meta-instructions; `prompts/__init__.py` re-exports all under canonical names
  - `tools.py` — `search_knowledge_base` (shared, all agents), `get_track_history`, `get_drone_specs` (threat analyst), `get_procedures`, `get_incident_context` (ops agent), `get_approved_actions`, `draft_alert`, `request_approval` (reporting agent) — all stubbed, real shape
  - `validation.py` — `validate_agent_output(output: str) -> bool` with all 5 injection regex patterns from architecture §4.4
  - `vllm_config.py` — `VLLM_MODEL_ARGS`, `LLM_CLIENT_CONFIG`, VRAM budget comment
  - `graph.py` — full LangGraph graph with all 9 nodes, Send API fan-out, dual-edge fan-in, 2-pass validation, `interrupt()` for human approval, `MemorySaver` stub checkpointer
  - `__init__.py` — package init, re-exports `build_graph`, `IncidentState`, `TrackRecord`
  - `requirements.txt` — langgraph, langgraph-checkpoint-postgres, langchain-openai, sqlalchemy, asyncpg, redis, pydantic
- **All 4 verification checks pass:**
  - `from services.agents.state import IncidentState, TrackRecord` — OK
  - `from services.agents.prompts import THREAT_ANALYST_PROMPT, OPS_AGENT_PROMPT, REPORTING_AGENT_PROMPT` — OK
  - All 5 injection patterns caught; clean output passes — OK
  - `build_graph()` compiles — 9 nodes confirmed: `initialize_incident`, `run_threat_analyst`, `run_ops_agent`, `fan_in_gate`, `validate_output_pass1`, `run_reporting_agent`, `validate_output_pass2`, `request_human_approval`, `finalize`
- **3 new decision-log entries**: agents built ahead of schedule, Send API fan-out, inside-node interrupt()

### What is stubbed (labeled in code, ships Week 6)
- All LLM calls (vLLM not running)
- All PostgreSQL reads/writes (incidents, tracks, audit_log, pending_actions)
- Redis XADD events
- SSE emissions
- RAG embed+retrieve+rerank pipeline (`search_knowledge_base` returns [])
- PostgreSQL checkpointer (MemorySaver used for now)

### Blockers
None — skeleton is complete and testable independently of vLLM/DB.

---

## 2026-10-16 — Week 3 · Data Prep Pipeline (COMPLETE)

### Done
- **All 3 datasets downloaded and verified:**
  - DUT Anti-UAV: 10,000 images (5200 train / 2600 val / 2200 test), Pascal VOC XML annotations
  - Anti-UAV300 (RGB): ~318 sequences with visible.json + visible.mp4 per sequence
  - Drone-vs-Bird: ~2,920 images in `drone-vs-bird.v2i.yolov8/` subdir, YOLO format
  - MAV-VID: unavailable (Kaggle removed, Bitbucket clone corrupted) — excluded
- **Fixed all 3 broken loaders in `services/perception/data_prep.py`:**
  - `load_dut_anti_uav()` — rewrote from COCO JSON to Pascal VOC XML parser using stdlib `xml.etree.ElementTree`
  - `load_anti_uav300_rgb()` — fixed: `RGB_label.json` → `visible.json`, `get_rect` → `gt_rect`, per-frame-dict → parallel lists, `[x1,y1,x2,y2]` → `[x,y,w,h]` top-left format; added cv2 frame extraction
  - `load_drone_vs_bird()` — fixed inverted path (`images/train/` → `train/images/`) and wrong base dir in `main()`
  - Added `_extract_anti_uav300_frames()` helper — extracts visible.mp4 → visible/visibleI{NNNN}.jpg, idempotent
- **Added `opencv-python>=4.8.0` to `services/perception/requirements.txt`**

### Pipeline output (verified complete)
- **309,821 total samples** — DUT Anti-UAV (10,000) + Anti-UAV300 (296,901) + Drone-vs-Bird (2,920)
- **Split:** train 247,856 / val 30,982 / test 30,983
- **Class balance:** 290,632 positive (93.8%) / 19,189 background (6.2%) — imbalance noted, cls loss weight to be tuned in train.py
- **`models/configs/yolo_data.yaml`** written and ready for training

### Next
- `dvc add data/raw/merged && git add data/raw/merged.dvc` — version the merged dataset before training
- `python services/perception/train.py --mode baseline` → COCO pretrained mAP on held-out test set, log to MLflow (becomes CI gate threshold)
- `python services/perception/train.py --mode finetune` → fine-tune on merged dataset, register drone-detector v1 in MLflow registry
- TensorRT INT8 quantization of best.pt — measure FPS before/after (number goes in T&E report)
- Replace stub_publisher with real YOLOv8 + TensorRT inference node
- **Week 3 gate:** tracks streaming with measured mAP and FPS numbers

### Blockers
None.

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
