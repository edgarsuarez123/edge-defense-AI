# Edge Base-Defense Copilot

An offline-capable AI copilot for a simulated outpost: detects drones, triages threats, and supports operator decisions — fully without a cloud connection.

---

## Problem

Forward operating bases operate in Denied, Disrupted, Intermittent, and Limited (DDIL) connectivity environments. Current AI-assisted threat detection and reporting tools depend on cloud connectivity for inference, model serving, and logging. When the uplink drops, the tools drop with it. A drone doesn't wait for the link to come back.

This project builds a single edge node that runs the full detection-to-decision pipeline offline, syncing to AWS GovCloud only when the link returns.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Edge Node (GPU laptop, Docker resource-constrained)            │
│                                                                 │
│  AirSim/Gazebo ──ROS 2──► Perception ──► Decision Model        │
│                                               │                 │
│                                    Redis incident_events        │
│                                               │                 │
│                                        Agent Supervisor         │
│                                      ┌────────┴────────┐       │
│                               Threat Analyst      Ops Agent     │
│                                      └────────┬────────┘       │
│                                        Reporting Agent          │
│                                               │                 │
│                                      Approval Interrupt         │
│                                               │                 │
│                                    FastAPI ◄─ Operator UI       │
└───────────────────────────┬─────────────────────────────────────┘
                            │ uplink when available
                    AWS GovCloud (PostgreSQL sync, MLflow registry)
```

---

## Demo Scenario

1. Network link drops. Edge node enters offline mode — all services continue running locally.
2. A drone appears on camera and RF sensors. The YOLO detector and RF classifier pick it up; the Kalman tracker fuses the signals into a single track.
3. The decision model crosses the escalation threshold. The track is written to PostgreSQL and published to Redis.
4. The LangGraph agent supervisor spawns an incident: threat analyst and ops agent run in parallel (RAG + Qwen2.5-7B-Instruct). The reporting agent drafts a structured alert.
5. The operator receives an approval request in the UI. They review the draft alert and approve. The incident is closed, the audit log is written, and everything is marked for sync.
6. When the link returns, the sync worker drains `incidents`, `pending_actions`, and `audit_log` to GovCloud. The updated model registry pushes any approved model revisions back to the edge.

---

## Tech Stack

| Component | Technology | Why |
|---|---|---|
| Detector | YOLOv8 + TensorRT INT8 | Real-time EO detection at edge-constrained GPU budget |
| Tracker | Kalman filter (ROS 2) | Fuses EO + RF + audio into stable tracks |
| Decision model | Custom classifier | Dual-threshold: alert vs. full escalation |
| Sensor bus | ROS 2 Humble (Docker) | Industry standard for defense robotics |
| Agent framework | LangGraph | Incident-centric graph, human approval interrupt, PostgreSQL checkpoints |
| LLM | Qwen2.5-7B-Instruct-AWQ (vLLM) | Best structured-output quality at 4-bit; fits 10GB VRAM alongside TensorRT |
| RAG | nomic-embed-text-v1.5 + MiniLM re-ranker + pgvector hnsw | 350MB total, edge-deployable, high recall for military docs |
| API | FastAPI + SSE | REST mutations, server-to-client streams only |
| UI | React | Served on operator terminal, not on edge node — React weight is irrelevant to edge budget |
| Database | PostgreSQL 16 + pgvector | Operational data + vector store + LangGraph checkpoints in one instance |
| Event bus | Redis Streams | Primary bus; PostgreSQL fallback — Redis failure degrades performance, not data |
| Experiment tracking | MLflow | Baseline comparison CI gate, model registry for edge sync |
| Sync | asyncio uplink monitor | 30s polling, edge-wins operational data, cloud-wins models/config |
| Observability | Structured JSON → audit_log | Cloud observability (Langfuse) in dev only — DDIL node cannot depend on it |

---

## Status

**Week 2 of 12 — Phase 1: Perception + Fine-tuning**

| Component | Status |
|---|---|
| Repo structure | Done |
| Docker infra (postgres, redis, mlflow) | Done — verified |
| Database schema + migrations | Done — 5 tables, all indexes, pgvector |
| Seed / smoke test | Done |
| YOLO dataset sourcing | In progress |
| YOLO fine-tuning | Planned |
| ROS 2 Docker setup | Planned |

See [progress.md](progress.md) for the full build log.

---

## Quick Start

```bash
# 1. Copy env and fill in values
cp .env.example .env
# Edit .env: set POSTGRES_PASSWORD and API_KEY at minimum

# 2. Start core services
docker compose -f docker-compose.dev.yml up -d postgres redis

# 3. Apply schema (run from infra/postgres/)
cd infra/postgres
pip install -r requirements.txt
python -m alembic upgrade head

# 4. Smoke test — verifies all tables, constraints, and pgvector
python seed.py

# 5. Start MLflow (model tracking UI at localhost:5000)
cd ../..
docker compose -f docker-compose.dev.yml up -d mlflow
```

**Port note for dev:** if 5432/6379 are in use by other projects, update the host-side ports in `docker-compose.dev.yml` (`5433:5432`, `6380:6379`) and update `DATABASE_SYNC_URL`/`REDIS_URL` in `.env` to match.

---

## Repo Structure

```
edge-defense-copilot/
├── services/
│   ├── perception/          # ROS 2 nodes: YOLO, classifiers, tracker, decision model, bridge
│   ├── agents/              # LangGraph supervisor + 3 agents, tools, prompts/
│   ├── vllm/                # vLLM server config
│   ├── api/                 # FastAPI: REST, SSE, static UI
│   ├── ui/                  # React app
│   └── sync/                # Uplink monitor, sync worker, ingest script
├── infra/
│   ├── docker/              # Per-service Dockerfiles
│   ├── postgres/            # Alembic migrations, schema, seed
│   └── scripts/             # DDIL simulator (tc helpers), trtexec runner
├── data/
│   ├── raw/                 # DVC-tracked datasets (not in git)
│   ├── synthetic/           # AirSim/Gazebo configs and renders
│   └── docs/                # Distribution A manuals for RAG
├── models/
│   ├── configs/             # Training configs (YOLO yaml, hparams)
│   └── cards/               # Model cards
├── evals/
│   ├── scenarios/           # YAML + Python for 20+ T&E scenarios
│   ├── metrics/             # mAP, ECE, RAG faithfulness, routing accuracy
│   └── reports/             # Generated T&E report output
├── .github/workflows/ci.yml
├── docker-compose.edge.yml  # Production: resource-constrained, Docker secrets
├── docker-compose.dev.yml   # Dev: hot reload, Langfuse, no resource limits
├── .env.example
├── decision-log.md          # Every architecture decision with reasoning
├── progress.md              # Build log, newest entry first
└── README.md
```
