# Edge Base-Defense Copilot — Decision Log

Living document. Every architecture, design, and technology decision is recorded here with full reasoning. Add new entries at the bottom of the relevant section. Never delete entries — if a decision is reversed, add a new entry that supersedes it and references the original.

Format:
> **[DATE] DECISION TITLE**
> Choice made, alternatives considered, and why this one was selected.

---

## Compute & Deployment

---

**[2026-10-14] Primary compute target: GPU laptop, not AWS g5g**
Use a GPU laptop (NVIDIA RTX class) as the primary development and demo target. AWS g5g (ARM + NVIDIA T4G) is dropped entirely.

*Why:* The g5g uses a different CUDA SM architecture than typical RTX GPUs. TensorRT engines are not portable between architectures — compiling for x86 and then pushing to g5g requires a full engine recompile. The inner dev loop (edit → build → test) is significantly slower with a cloud instance vs local hardware. Docker resource constraints (`--cpus=4`, `--memory=16g`, 0.75 GPU memory utilization) simulate Jetson AGX Orin constraints accurately enough for the T&E scope. The only thing that cannot be simulated is real power draw — this is documented honestly in the T&E report. Validating on real ARM hardware (Jetson or g5g) is a follow-on step, not a 12-week requirement.

*Alternatives considered:* AWS g5g.xlarge as primary ($1.006/hr, ARM + T4G, closer to real Jetson). Rejected because it costs money for every iteration, adds cross-compile complexity from day one, and the Jetson-specific signal can be achieved later.

---

**[2026-10-14] Edge simulation strategy: Docker resource constraints + tc netem**
Simulate an edge device in the field using Docker resource limits and Linux traffic control rather than purchasing or renting real edge hardware.

*Why:* Docker's `--cpus`, `--memory`, and GPU memory fraction flags constrain the laptop to behave like a Jetson AGX Orin (4 cores, 16GB unified memory, single GPU). `tc netem` simulates DDIL conditions (packet loss, latency, complete link loss) on the network interface. This is the standard approach used by defense contractors to validate edge deployments before hardware procurement. If it passes Docker-constrained tests, it will pass on real hardware with equivalent specs.

*Alternatives considered:* AWS g5g for edge validation (rejected — see above). Jetson hardware purchase (not required within 12-week scope; optional for follow-on).

---

## UI & API

---

**[2026-10-14] Operator UI: React + FastAPI, not Streamlit or HTMX**
Use React (SPA) for the frontend and FastAPI for the backend, with the operator terminal treated as a separate device from the edge compute node.

*Why:* The key architectural insight is that React's memory footprint lives on the operator's terminal (a ruggedized laptop or tablet), not on the edge node. The edge node runs only the FastAPI server (~100MB). This means React's weight is irrelevant to edge resource constraints. FastAPI + React signals production instincts to hiring managers — it forces a real API contract, a documented REST surface, and proper component separation. The API layer is separately testable and auto-documents via OpenAPI spec.

*Alternatives considered:* Streamlit (fast to build, ~300MB server-side, reads as "ML demo" not "product"). HTMX + FastAPI (same production FastAPI signal, no JS build step, lighter — but React is the stronger portfolio signal for the target employers).

---

**[2026-10-14] API style: REST mutations + SSE streams, not WebSocket**
Use REST (HTTP request/response) for all mutations and point queries. Use Server-Sent Events (SSE) for all server-to-client real-time streams.

*Why:* The operator UI only needs server-to-client streaming — track updates, agent reasoning steps, approval requests, sync status. It never needs to send data back on the same streaming channel; approvals go over POST /incidents/{id}/approve (REST). SSE is strictly unidirectional (server pushes only), simpler to implement than WebSocket, reconnects natively via Last-Event-ID, and is HTTP/2 compatible. WebSocket is the right choice when the client needs to send on the same channel — that case doesn't exist here.

*Alternatives considered:* Full WebSocket (bidirectional — adds connection management complexity without benefit here). Polling (simpler but laggy for a live track map — unacceptable UX for a counter-UAS operator).

---

**[2026-10-14] Authentication: single API key, no JWT**
Secure the FastAPI backend with a single API key passed as `Authorization: Bearer {key}` (REST) and `?token=` query param (SSE/EventSource). No JWT, no session management.

*Why:* The operator UI is served on a physically isolated LAN segment at the edge node. The threat model is network-based attackers, not multi-user session hijacking. JWT's session management complexity — token refresh, revocation, clock skew handling — adds attack surface with no security benefit on a single-operator edge node. A single API key provides the required authentication boundary. If multi-operator access or role separation is needed, that is a scope change requiring a new decision entry, not an upgrade to JWT.

*Alternatives considered:* JWT with local user store (adds a week of work, no demo value, unnecessary complexity). No auth at all (unacceptable — a hiring manager seeing zero auth on a "production-style" system will notice).

---

## Database & Storage

---

**[2026-10-14] Database: one PostgreSQL instance + pgvector extension**
Use a single PostgreSQL instance with the pgvector extension for all storage: operational data (tracks, incidents, alerts), vector embeddings (RAG), and LangGraph checkpoints.

*Why:* A single PostgreSQL instance means one connection pool, one process, one backup target, one GovCloud sync stream. pgvector with hnsw indexing handles millions of vectors — more than sufficient for a bounded corpus of Army manuals and drone specs (likely under 100K chunks). Transactional consistency: a track escalation and the agent's retrieval result can be written in the same transaction. On restart, `incidents` and `langgraph checkpoints` are in the same DB — resuming open incidents is one JOIN, not a cross-store lookup.

*Alternatives considered:* Separate Qdrant vector DB (richer filtering SDK, but adds ~200-500MB idle RAM, a second failure domain, and a second sync target for GovCloud — not worth it at this corpus scale).

---

**[2026-10-14] Vector dimensions: VECTOR(768), not VECTOR(1536)**
The `document_chunks.embedding` column is `VECTOR(768)`.

*Why:* The selected embedding model is `nomic-ai/nomic-embed-text-v1.5`, which produces 768-dimensional vectors. 1536 is OpenAI's text-embedding-ada-002 output dimension. Using the wrong dimension wastes 2x storage and breaks similarity search.

---

**[2026-10-14] pgvector index type: hnsw, not ivfflat**
Use the hnsw (Hierarchical Navigable Small World) index type for the document_chunks embedding column.

*Why:* hnsw provides consistently better recall than ivfflat without requiring the `nprobe` tuning that ivfflat needs to approach the same recall levels. For a military RAG store where a missed citation could mean an operator acts on incomplete information, recall matters more than build time. hnsw uses more memory (~400MB for 500K vectors at 768d) but the edge node has sufficient RAM. ivfflat is appropriate when you need faster index builds on very large corpora — not the case here.

*Alternatives considered:* ivfflat (faster build, lower memory, worse recall at equivalent settings).

---

**[2026-10-14] Sync buffer: synced_at nullable column, not a separate sync_queue table**
Track what needs to be synced to GovCloud by adding a `synced_at TIMESTAMPTZ NULL` column to `incidents`, `pending_actions`, and `audit_log`. The sync worker queries `WHERE synced_at IS NULL`.

*Why:* Simple, co-located with the data, requires no trigger or application-level bookkeeping beyond a single UPDATE on sync success. A partial index on `synced_at IS NULL` makes the query efficient. A dedicated `sync_queue` table would add a second table to maintain, require a trigger or application-level INSERT on every row write, and add complexity for three tables that have straightforward sync semantics.

*Alternatives considered:* Dedicated `sync_queue` table with operation type and retry count (better for many tables or per-row retry tracking — overkill for three tables with simple append-only sync semantics).

---

**[2026-10-14] Tracks not synced to GovCloud**
Raw sensor tracks are retained locally on the edge node only. GovCloud syncs only `incidents`, `pending_actions`, and `audit_log`.

*Why:* Tracks are high-volume raw sensor data. GovCloud cares about decisions and their outcomes (incidents, approvals, audit trail), not every raw sensor reading. Syncing tracks would inflate bandwidth on the uplink and storage in GovCloud without proportional value. If raw track replay is needed for incident reconstruction, the local `tracks` table and ROS 2 bags serve that purpose.

---

**[2026-10-15] Schema enum columns: CHECK constraints, not PostgreSQL ENUMs**
All enum-like text columns (`status`, `threat_level`, `sensor_source`, `classification`, `action_type`, `agent_id`) use `CHECK (col IN (...))` instead of `CREATE TYPE ... AS ENUM`.

*Why:* PostgreSQL ENUM types cannot have values removed without dropping and recreating the type — any table using that type must be rebuilt. CHECK constraints are altered with a single `ALTER TABLE ... DROP CONSTRAINT ... ADD CONSTRAINT` statement. For a system where status values and classifications may be refined during development, CHECK constraints are strictly easier to change. The one exception is `audit_log.actor`, which uses free-text with no CHECK — actor values include dynamic patterns like `agent:threat_analyst` that can't be enumerated without fragility.

*Alternatives considered:* PostgreSQL ENUMs (type-safe at the DB level, slightly faster comparison — but the modification pain outweighs the benefit at this stage). Application-level validation only (no DB constraint — allows garbage writes from any tool, not acceptable).

---

**[2026-10-15] tracks.synced_at removed**
The `synced_at` column and `idx_tracks_synced` index were removed from the `tracks` table. The architecture-design.md draft included them, but this contradicts the logged decision that tracks are local-only. The migration matches the decision log.

*Why:* A column that is never written to and never queried is dead weight. Keeping it would imply tracks are synced, which they are not. Removing it makes the schema honest.

---

**[2026-10-15] Redis fallback poll index: compound partial index on tracks**
Replaced the simple `idx_tracks_processed` index with a compound partial index `idx_tracks_unprocessed_threats ON tracks(created_at) WHERE processed = false AND classification = 'LIKELY_THREAT'`.

*Why:* All tracks land in the `tracks` table (benign, alerted, escalated), but only `LIKELY_THREAT` tracks need agent processing. A simple index on `processed` would return all unprocessed tracks including benign ones, forcing the fallback consumer to filter in memory. The compound partial index excludes benign tracks at write time — only escalated, unconsumed tracks enter the index. The index also covers the `ORDER BY created_at` in the poll query, eliminating a sort step. At any healthy moment there should be near-zero rows in this index.

*Alternatives considered:* Simple partial index on `processed = false` + compound WHERE in query (index returns too many rows). Replace `processed` boolean with `escalated_at` timestamp (cleaner semantics, but adds complexity without changing behavior given the compound partial index already solves the ambiguity).

---

**[2026-10-15] Alembic uses synchronous psycopg2 driver for migrations**
Alembic migrations connect via `DATABASE_SYNC_URL` (psycopg2, `postgresql+psycopg2://`) while the application uses `DATABASE_URL` (asyncpg, `postgresql+asyncpg://`).

*Why:* Alembic's migration engine is synchronous — it does not support asyncpg. The two URLs point to the same database; they differ only in driver. This is the standard pattern for SQLAlchemy async apps using Alembic. The sync URL is only used during `alembic upgrade` / `alembic downgrade` — never at runtime.

*Alternatives considered:* alembic-utils async wrapper (adds a dependency and complexity with no benefit — the sync driver works perfectly for migrations, which are run once, not in the hot path).

---

## Event Bus

---

**[2026-10-14] Event bus: Redis Streams primary + PostgreSQL LISTEN/NOTIFY fallback**
Use Redis Streams as the primary application event bus between the sensor/decision pipeline and the agent mesh. Use PostgreSQL LISTEN/NOTIFY + `processed` column polling as the automatic fallback if Redis fails.

*Why:* Redis Streams provide backpressure, fan-out (multiple independent consumer groups), message persistence (configurable TTL), and low latency. These properties are needed for the track event → agent supervisor → FastAPI SSE fan-out. PostgreSQL LISTEN/NOTIFY alone would work but lacks consumer group semantics and replay.

The fallback eliminates Redis as a mission-critical single point of failure. The pattern: write to PostgreSQL first (always durable), then XADD to Redis. If Redis is unavailable, the track is already in PostgreSQL. Consumers fall back to polling `WHERE processed = false`. When Redis recovers, they drain the PostgreSQL backlog first, then resubscribe. Redis failure becomes a performance degradation, not a data loss event — critical for a DDIL system.

*Alternatives considered:* PostgreSQL LISTEN/NOTIFY only (no consumer group semantics, no replay without extra columns — adequate but less production-realistic). Redis only (single point of failure for the event bus — unacceptable). ZeroMQ (no persistence, no replay, more complex deployment).

---

## Sensor Pipeline

---

**[2026-10-14] ROS 2: yes, in Docker**
Include ROS 2 in the perception service, running in a Docker container on an Ubuntu base image.

*Why:* Every defense robotics job posting from the target employers (Anduril, Shield AI, Northrop) lists ROS 2 explicitly. It is the lingua franca of defense robotics sensor pipelines — it handles sensor drivers, time synchronization, message typing, and bag recording out of the box. Excluding it is defensible for a pure simulation, but including it is a strong portfolio signal. Running in Docker (Ubuntu container) avoids the Windows/WSL2 GPU passthrough friction.

*Alternatives considered:* asyncio queues instead of ROS 2 (simpler for simulated sensors, but loses the portfolio signal and makes the architecture less honest about how real systems work). ROS 2 for everything including agents (rejected — LangGraph's async executor and ROS 2's rclpy executor conflict; agents should be pure Python).

---

**[2026-10-14] ROS 2 bridge: lifecycle node on /decision/escalated_tracks**
The bridge between the ROS 2 sensor domain and the Redis application bus is a ROS 2 lifecycle node that subscribes to `/decision/escalated_tracks` and publishes to Redis.

*Why:* A ROS 2 lifecycle node is a proper ROS 2 citizen — it has health check semantics (configure → activate → deactivate) that integrate with the ROS 2 node graph monitoring. The bridge subscribes to the decision model's output topic, not the raw perception topics. The decision model is the last ROS 2 node; the bridge is the handoff point. This keeps the clean domain boundary: ROS 2 owns real-time sensor data, Redis owns application events.

*Alternatives considered:* Standalone Python process using roslibpy or zenoh-bridge-ros2dds (no rclpy dependency — but these adapters add their own complexity and latency; since the project already runs ROS 2 in Docker, a lifecycle node is trivial).

---

**[2026-10-18] Detection message format: per-frame JSON array, not per-detection**
`yolo_detector` publishes one `std_msgs/String` message per frame containing `{frame_id, timestamp, detections: [...]}`, where `detections` is an array of all boxes found in that frame (possibly empty).

*Why:* The downstream Kalman tracker needs all detections from a single frame together to associate them by timestamp. One message per detection would require the tracker to group by `frame_id` before associating — extra state management with no benefit. An empty-array message also explicitly signals "inference ran but found nothing," which is more informative than silence.

*Alternatives considered:* One message per individual detection (simpler per-message schema but forces downstream grouping logic). Custom `.msg` file (type-safe but requires CMakeLists, a second package, and a colcon rebuild every time the schema evolves — premature for Week 3).

---

**[2026-10-18] Image transport: manual numpy conversion, not cv_bridge**
`image_replay` converts numpy arrays to `sensor_msgs/Image` manually by setting `height`, `width`, `step`, `encoding`, and `data` fields. `yolo_detector` reverses this with `np.frombuffer().reshape()`.

*Why:* `cv_bridge.CvBridge()` requires `ros-humble-cv-bridge`, a compiled C++ ROS package that adds an apt dependency and a non-trivial build step to the Dockerfile. The manual conversion is 4 lines of Python and is completely transparent about what it does. Once Gazebo takes over as the image source, `image_replay` is retired and `cv_bridge` would be irrelevant to add now.

*Alternatives considered:* `cv_bridge.CvBridge().cv2_to_imgmsg()` (idiomatic ROS 2 but adds compiled dependency). `sensor_msgs_py.point_cloud2` utilities (wrong domain — those are for point clouds).

---

**[2026-10-18] Camera subscriber QoS: depth=1 BEST_EFFORT (latest-only)**
`yolo_detector` subscribes to `/camera/image_raw` with `QoSProfile(depth=1, reliability=BEST_EFFORT)`.

*Why:* Counter-UAS requires the freshest possible detection, not the oldest queued frame. If inference takes 30ms and the replay publishes at 60 FPS, frames are dropped — that is correct behavior. `BEST_EFFORT` tells ROS 2 not to buffer frames for delivery guarantees; `depth=1` caps the queue at one entry. Without `BEST_EFFORT`, a `RELIABLE` publisher + `RELIABLE` subscriber would buffer unprocessed frames and the detector would fall further behind over time.

*Alternatives considered:* `depth=10 RELIABLE` (delivers every frame in order but allows unbounded queue growth when inference lags). `depth=1 RELIABLE` (limits queue size but still applies backpressure to the publisher).

---

**[2026-10-18] torch + ultralytics in separate Dockerfile RUN layers**
The Dockerfile installs torch/torchvision in one `RUN` and ultralytics in a second `RUN`.

*Why:* Docker layer caching. Torch with CUDA is ~2 GB. If both were in one `RUN`, every ultralytics version bump would re-download torch. Separate layers mean only the ~200 MB ultralytics layer is invalidated when its version changes. This meaningfully reduces build iteration time.

*Alternatives considered:* Single `RUN pip install torch ultralytics` (simpler Dockerfile, but sacrifices build caching on the most common change — ultralytics version bumps).

---

**[2026-10-14] Decision model thresholds: dual threshold (ALERT + ESCALATION)**
The decision model uses two independently configurable thresholds: `ALERT_THRESHOLD` (flags track yellow in UI, no agent workflow) and `ESCALATION_THRESHOLD` (triggers full agent workflow, red alert).

*Why:* Operators need situational awareness of developing tracks before they cross the full escalation threshold. A dual threshold mirrors how real counter-UAS operators work — they watch a track develop before committing an agent workflow. Single threshold forces a binary choice: ignore or escalate fully. Both thresholds are env-var configurable (set from ECE calibration, not guesses) so they can be tuned per deployment without rebuilding containers.

*Alternatives considered:* Single threshold (simpler, but forces binary escalation — not realistic). Hardcoded thresholds (simpler, but can't be adjusted for different threat environments without a rebuild).

---

## Agent Design

---

**[2026-10-14] Agent model: incident-centric LangGraph, one graph per incident**
Each escalated incident spawns one LangGraph graph run. The graph persists until the incident is closed (approved or rejected).

*Why:* Incidents are the natural unit of human approval — operators approve or reject actions on incidents, not on individual sensor readings. Track-centric graphs (one per track) would spawn too many graphs and lose the ability to correlate tracks from the same threat. Session-centric graphs (one per operator shift) are complex and harder to checkpoint and resume.

*Alternatives considered:* Track-centric (one graph per track — can't correlate tracks, too many parallel graphs). Session-centric (one graph for entire shift — complex state, harder to isolate and resume per incident).

---

**[2026-10-14] Supervisor routing: parallel first stage (analyst + ops), then reporting**
The threat analyst and ops agent run concurrently. The reporting agent waits for both to complete (fan-in), then produces the draft alert.

*Why:* The ops agent queries procedures by track classification and threat level (set by the decision model on the incident record), not by the threat analyst's prose explanation. There is no data dependency between analyst and ops — they can run simultaneously. Running them in parallel saves 15-30 seconds per incident (the difference between `max(analyst, ops)` and `analyst + ops` latency). The reporting agent genuinely depends on both outputs, so it correctly waits for the fan-in.

*Alternatives considered:* Sequential pipeline (analyst → ops → reporting — analyst output informs ops context, but the ops agent's primary inputs are track data and the incident record, not analyst prose; the latency cost is not justified).

---

**[2026-10-14] Agent tools: one shared search_knowledge_base injected into all agents**
Define `search_knowledge_base(query, top_k, source_filter)` once and inject it into all three agents rather than duplicating it per agent.

*Why:* The tool implementation is identical regardless of which agent calls it. Query intent (threat analysis vs procedure retrieval) is determined by the agent's system prompt, not by having separate tool implementations. One definition = one place to maintain, one place to test, one schema in the OpenAPI spec.

*Alternatives considered:* Separate tool per agent (e.g., `search_for_threat_info`, `search_for_procedures`) — duplicates implementation, harder to maintain, no functional benefit.

---

**[2026-10-14] Citations stored in IncidentState checkpoint, not retrieved from DB**
`threat_citations` and `procedure_citations` are stored directly in the `IncidentState` TypedDict, which is persisted to the LangGraph PostgreSQL checkpoint.

*Why:* Self-contained checkpoints mean every T&E audit can reconstruct exactly what the agent said and why without requiring a separate DB lookup. If the `document_chunks` table changes (re-ingestion, re-chunking), the checkpoint still reflects what the agent actually retrieved at the time of the incident. This is the correct audit trail design for a system subject to T&E and potential ATO review.

*Alternatives considered:* Store only chunk IDs in state, retrieve content on demand (cheaper checkpoint storage, but breaks audit integrity if the RAG store changes).

---

**[2026-10-14] Prompt injection defense: structural isolation + output validation**
Two-layer defense: (1) retrieved text is structurally isolated in a `[DOCUMENTS]...[END DOCUMENTS]` block in the user message, never interpolated into the system prompt; (2) a regex validation pass checks agent outputs for injection artifacts before the graph proceeds.

*Why:* Structural isolation ensures the model's instruction-following context (system prompt) is never contaminated by retrieved text. Meta-instructions in the system prompt tell the model to treat retrieved commands as artifacts. Output validation catches anything that slipped through. Together these provide defense-in-depth without requiring a second classifier model (which would add latency and another VRAM cost). The T&E security test requires zero approval bypasses from poisoned documents.

*Alternatives considered:* Adding a sanitization classifier on retrieved chunks before sending to LLM (adds 200-400ms per retrieval, another model to manage). Output validation only (no structural protection). Structural isolation only (no output check).

---

**[2026-10-14] Agent error handling: retry-once, then graceful degradation**
On LLM failure (timeout or malformed output), retry once with reduced context. If the retry fails, mark the agent as failed, emit a degraded output message, and continue the graph with the available data. The operator always sees the incident.

*Why:* One retry catches transient failures (GPU OOM spike, brief vLLM overload) without excessive latency. Aggressive retry (3+ attempts with backoff) adds 2-3 minutes of delay before an operator can act — unacceptable in a time-sensitive threat scenario. Fail-fast (no retry) misses recoverable transient failures. Degrading gracefully keeps the operator informed and lets them decide how to proceed, which is correct human-in-the-loop design.

---

**[2026-10-14] Startup recovery: auto-resume all open incidents**
On service startup, the agents service queries for all incidents with status `OPEN` or `PENDING_APPROVAL` and resumes their LangGraph graphs from the PostgreSQL checkpoint.

*Why:* Edge nodes in the field restart from generator cuts, power fluctuations, and software crashes. An operator should not have to manually reopen every incident after a restart. Auto-resume restores the exact state before the crash — if an approval was pending, the approval UI reappears. This is correct DDIL design: the system recovers autonomously.

*Alternatives considered:* Require manual reopen (safer but requires operator action after every restart — unacceptable operationally). Auto-resume PENDING_APPROVAL only, not mid-run (partial — misses incidents that were mid-agent-run).

---

**[2026-10-14] Prompt versioning: Python constants in git**
Agent prompts are defined as Python string constants in `services/agents/prompts/`, versioned in git alongside the agent code.

*Why:* A prompt change is a code change. It goes through a PR, gets diffed, reviewed, and automatically triggers the CI eval gate (routing accuracy + RAG faithfulness checks). No separate tooling, no drift between code and prompts, no way to deploy a prompt change without it being gated. Langfuse prompt management is available on the dev compose profile for experimentation but is never used in production (DDIL node cannot depend on cloud tooling).

*Alternatives considered:* YAML files (prompt updates without image rebuild — but bypasses CI eval gate unless path filter is added; adds complexity). PostgreSQL prompts table (versioned rows, rollback via UPDATE — adds DB dependency to agent startup, harder to diff, easy to drift).

---

## Agents Service Build

---

**[2026-10-16] Agents service skeleton built ahead of Week 7 schedule**
Full `services/agents/` skeleton implemented in Week 3 while the perception pipeline is blocked on data prep. All LLM calls, PostgreSQL writes, RAG retrievals, Redis events, and SSE emissions are stubbed with clearly labeled STUB comments. The graph topology, state schema, validation logic, and prompt constants are real and tested.

*Why now:* `data_prep.py` is running a 20-30 minute frame extraction job. The agent skeleton has no dependency on the perception pipeline — it can be built and structurally tested independently. Building it now keeps momentum and gives the full graph topology something to test against when vLLM is wired in Week 6.

*What is real (tested and works):* `IncidentState` + `TrackRecord` TypedDicts, `completed_agents` operator.add reducer, all five injection patterns in `validate_agent_output`, LangGraph graph compilation with all 9 nodes, conditional edges, fan-out via Send API, fan-in via dual edges to `fan_in_gate`, interrupt() in `request_human_approval`, MemorySaver checkpointer.

*What is stubbed (clearly labeled):* LLM calls (vLLM not running), PostgreSQL reads/writes, Redis XADD, SSE events, RAG embed+retrieve+rerank pipeline. Each stub has a comment describing the real implementation and the week it ships.

*Alternatives considered:* Wait until Week 7 as scheduled (loses a week of momentum; no reason to wait when the architecture is locked). Build only state.py now and defer the graph (would require re-reading the same files again next session).

---

**[2026-10-16] LangGraph fan-out via Send API, not conditional edges with polling**
Parallel dispatch to threat_analyst and ops_agent uses `Send("node", state)` objects returned from a conditional edge function, not a conditional edge that checks a condition on each call.

*Why:* The `Send` API is LangGraph's canonical parallel dispatch mechanism. Both nodes are dispatched simultaneously. Fan-in is enforced by graph topology — both parallel nodes add edges to `fan_in_gate`, and LangGraph holds that node until both upstream branches complete. The `completed_agents` reducer (operator.add) tracks which agents finished as an audit trail, not as the actual synchronization mechanism.

*Alternative:* Use a conditional edge after each agent that checks `len(completed_agents) == 2` and returns END otherwise. This creates a polling pattern with non-deterministic convergence — incorrect and fragile. The `Send` + topology approach is both simpler and correct.

---

**[2026-10-16] interrupt() called inside node, not interrupt_before= on compile**
`request_human_approval` calls `interrupt(value)` directly inside the node body (LangGraph >= 0.2 pattern) rather than using `compile(interrupt_before=["request_human_approval"])`.

*Why:* The inside-node approach lets the node control exactly what value is passed to the operator (the full incident payload including draft_alert). `interrupt_before` would pause the graph before the node runs, giving the caller no control over the interrupt payload — they would have to reconstruct it from state. The node-level interrupt is more explicit and testable.

*Tradeoff:* The node must handle the resume value itself (via the return value of `interrupt()`). This is slightly more complex but gives full control.

---

## RAG Stack

---

**[2026-10-14] Embedding model: nomic-embed-text-v1.5 (270MB)**
Use `nomic-ai/nomic-embed-text-v1.5` for document and query embedding.

*Why:* 270MB is significantly lighter than the large embedding models (~1.3GB for bge-large). On an edge node already running vLLM (4GB+), PostgreSQL, Redis, and TensorRT, every GB matters. nomic-embed uses Matryoshka representation learning — dimensions can be truncated if needed. Quality on technical/domain text is competitive with models 5x its size. For a bounded corpus of Army manuals and drone specs, the quality delta between nomic-embed and bge-large is not worth 1GB of additional VRAM.

*Alternatives considered:* `BAAI/bge-large-en-v1.5` (1.3GB, stronger benchmark scores — overkill for this corpus size and edge memory budget). `intfloat/e5-large-v2` (1.3GB, solid but same weight concern).

---

**[2026-10-14] Re-ranker: MiniLM cross-encoder (80MB)**
Use `cross-encoder/ms-marco-MiniLM-L-6-v2` for re-ranking retrieved chunks.

*Why:* 80MB is negligible on the edge node. Cross-encoders produce better re-ranking quality than bi-encoder similarity alone because they jointly encode the query and passage. The MiniLM variant is fast and accurate for short passages (Army manual sections). The large bge-reranker would add 1.3GB for marginal quality improvement on a bounded, structured corpus.

*Alternatives considered:* `BAAI/bge-reranker-large` (1.3GB, stronger — not worth the VRAM cost for this corpus).

---

**[2026-10-14] Chunking: semantic-first, 512-token cap**
Split documents on section boundaries first (numbered headings, paragraph breaks), then cap oversized sections at 512 tokens with 50-token overlap.

*Why:* Military manuals (FM 3-01, ATP 3-01.81) are heavily structured with numbered sections. Section-boundary chunks are semantically coherent — a chunk about "Section 3.2.1 Employment Considerations" contains related content that cites cleanly. Fixed-size chunking cuts across section boundaries and produces chunks that mix unrelated topics, degrading both retrieval precision and citation quality. The 512-token cap handles sections that are too long for a single chunk.

*Alternatives considered:* Fixed-size only (simpler, but poor coherence for structured military docs). Pure semantic (no token cap — some sections are very long and would exceed the LLM context budget per chunk).

---

## LLM

---

**[2026-10-14] LLM: Qwen2.5-7B-Instruct-AWQ**
Use Qwen/Qwen2.5-7B-Instruct-AWQ as the single LLM for all three agents.

*Why:* The reporting agent must produce Pydantic-validated JSON alerts reliably. Qwen2.5-7B consistently outperforms Llama-3.1-8B on structured output benchmarks (IFEval, structured generation). A failed JSON parse triggers a retry — that adds 30+ seconds per incident. Qwen2.5 reduces retry frequency. AWQ quantization at 4-bit uses ~4GB VRAM, fitting comfortably in 10GB alongside TensorRT.

*Alternatives considered:* Meta-Llama-3.1-8B-Instruct (larger community, more fine-tuning resources, slightly weaker structured output — retry risk is the deciding factor). Gemma-2-9B-it (strong reasoning, but 9B at 4-bit uses ~5.5GB vs ~4GB — tighter on 10GB GPU, fewer AWQ quantizations available).

---

**[2026-10-14] vLLM config: 8192 context, 0.75 GPU memory utilization**
Serve Qwen2.5-7B-AWQ with 8192 token context window and 0.75 GPU memory utilization fraction.

*Why:* 8192 tokens provides safe headroom for military manual sections + incident context + system prompt + agent response. 4096 risks hitting the limit for long manual passages. At 4-bit AWQ, Qwen2.5-7B uses ~4GB VRAM. At 0.75 utilization on a 10GB GPU, vLLM takes ~7.5GB total (model + KV cache), leaving ~2.5GB for TensorRT YOLO (~1.5GB) and system. Tight but workable. Prefix caching is enabled — the system prompt KV is cached across all agent calls, significantly reducing first-token latency for sequential requests.

*Alternatives considered:* 4096 context (insufficient headroom for long manual sections). 16384 context (requires 24GB+ VRAM). 0.85 utilization (leaves only ~1.5GB for TensorRT — too tight on 10GB).

---

## Observability

---

**[2026-10-14] Langfuse: dev compose profile only, audit_log in production**
Langfuse is available only on the dev Docker Compose profile (`LANGFUSE_ENABLED=true`). In production (docker-compose.edge.yml), all agent steps are logged as structured JSON to the `audit_log` PostgreSQL table.

*Why:* A DDIL edge node cannot depend on cloud observability. When the uplink is denied, Langfuse traces would silently queue or drop. The `audit_log` table is local, durable, always available, and already captures every agent step, tool call, and human decision. It serves all T&E reporting needs. Langfuse in dev provides the prompt playground and comparison UI for iteration — that's its appropriate scope.

*Alternatives considered:* Self-hosted Langfuse on edge node (adds ~500MB RAM and a second PostgreSQL instance or shared instance complexity — not worth it when audit_log covers the need). Cloud Langfuse always-on (fails silently under DDIL — unacceptable).

---

## Infrastructure

---

**[2026-10-15] Dev compose host ports: 5433 (postgres) and 6380 (redis), not 5432/6379**
`docker-compose.dev.yml` maps postgres to host port 5433 and redis to 6380 instead of the standard 5432/6379.

*Why:* Other Docker projects on the dev machine already occupy 5432 and 6379. Changing host-side ports (container-internal ports remain 5432 and 6379) avoids conflicts without affecting any inter-service communication inside the Docker network. All services that talk to postgres or redis inside Docker still use standard ports. Only `DATABASE_SYNC_URL` and `REDIS_URL` in `.env` (for tools running on the host like Alembic and seed.py) use the mapped ports.

*Alternatives considered:* Stop the other projects to free ports (too disruptive, port conflicts are a permanent dev reality). Use Docker network aliases (only helps for intra-container communication, not for host-side Alembic/seed.py).

---

**[2026-10-15] Alembic migration files location: migrations/versions/**
Migration files live in `infra/postgres/migrations/versions/`, not directly in `infra/postgres/migrations/`.

*Why:* Alembic's default layout places the script runner (`env.py`, `script.py.mako`) in the `script_location` directory and migration version files in a `versions/` subdirectory within it. Placing migration files directly in `migrations/` and setting `version_locations = %(script_location)s` causes Alembic to try loading `env.py` as a migration file, which runs the migration engine recursively until stack overflow. The `versions/` subdirectory is the correct and unambiguous location.

*Alternatives considered:* Override `version_locations` to point to `migrations/` directly (causes `env.py` to be scanned as a migration script — recursive failure). Using a separate `script_location` for the runner and migrations (unnecessarily complex).

---

**[2026-10-15] MLflow startup: server only, no explicit db upgrade**
`docker-compose.dev.yml` runs `mlflow server` directly; the explicit `mlflow db upgrade` call was removed.

*Why:* `mlflow server` initializes the database schema automatically on first start via its internal Alembic migrations. Adding an explicit `mlflow db upgrade` before the server runs causes a migration ordering failure on fresh databases in MLflow 2.16.x — it tries to `ALTER TABLE metrics ADD COLUMN step` before the `metrics` table exists. Removing the explicit upgrade step lets the server initialize in the correct sequence.

The `mlflow` PostgreSQL database itself must still be created manually before first startup (`CREATE DATABASE mlflow`) since PostgreSQL does not auto-create databases. This is documented in the README quick start.

*Alternatives considered:* Use an init container or entrypoint script to create the database before MLflow starts (adds container complexity; a one-line psql command in quick start is sufficient for dev). Pin MLflow to an older version without this bug (masks the issue; the server-only approach works correctly with the current version).

---

## Detector Training

---

**[2026-10-15] YOLO model size: YOLOv8s (small), not nano or medium**
Use YOLOv8s for both baseline evaluation and fine-tuning.

*Why small over nano:* the real deployment target is TensorRT INT8 on a resource-constrained GPU (simulated Jetson). YOLOv8n at INT8 is fast but accuracy is low enough to raise false alarm rates that make the dual-threshold design meaningless. YOLOv8s at INT8 hits acceptable mAP (typically 5-8 points above nano on drone datasets) at ~60+ FPS on RTX hardware. The ~20min additional training time is not a meaningful tradeoff against deployment accuracy.

*Why not medium:* at 4-bit AWQ quantization, the LLM already uses ~4GB VRAM. TensorRT INT8 for YOLOv8m would require ~2.5GB vs ~1.5GB for YOLOv8s — that's 1GB of VRAM pressure that risks OOM on 10GB GPUs during joint operation.

*Alternatives considered:* YOLOv8n (fastest, but accuracy not worth the threshold calibration cost). YOLOv8m (better accuracy, unacceptable VRAM cost at full-stack deployment).

---

**[2026-10-15] Detector training: single-class (drone), multi-class deferred**
The drone detector is trained with `nc=1, names=["drone"]`. Bird and aircraft classes are treated as background (hard negatives) for the initial baseline.

*Why:* the baseline must answer "does fine-tuning on drone data beat COCO pretrained?" Adding multi-class complicates the mAP interpretation — a 2-class mAP of 0.72 doesn't directly compare to a 1-class mAP of 0.68. Single-class gives a clean comparison. Once the baseline and fine-tuned models are registered and the CI gate is running, expanding to multi-class is straightforward: update the class map, re-run data_prep.py, trigger the fine-tuning job.

*Tradeoff:* a single-class model cannot distinguish birds from drones at detection time — that discrimination happens at the decision model layer (which fuses EO + RF + audio signals). If the decision model is not yet running, false alarm rate on birds will be higher. Acceptable for Week 2; addressed in Week 3 when the full pipeline is integrated.

*Alternatives considered:* 3-class from the start (drone / bird / aircraft). Would require Drone-vs-Bird dataset to have all three classes annotated, which it doesn't. Also complicates the ablation study.

---

**[2026-10-15] Drone-vs-Bird source: Roboflow CC BY 4.0 version, not official challenge**
The Roboflow-hosted Drone-vs-Bird dataset (CC BY 4.0) is used instead of the official challenge dataset which requires a Data Usage Agreement (non-commercial, restricted).

*Why:* CC BY 4.0 allows unrestricted use including portfolio publishing. The DUA version is non-commercial — technically permissible for a portfolio project, but publishing the project (e.g., GitHub) with DUA-restricted data could be a licensing violation. CC BY 4.0 eliminates the ambiguity entirely. The Roboflow version is already in YOLO format, removing a conversion step.

*Tradeoff:* the Roboflow version has ~2,920 images, smaller than the full official challenge dataset. This is acceptable — it contributes hard negatives to training, not the majority of positive examples.

---

**[2026-10-16] Dataset split: 80/10/10 train/val/test with RANDOM_SEED=42**
`data_prep.py` splits the merged dataset 80% train, 10% val, 10% test. The random seed is a fixed constant (42) set at the top of the file.

*Why 80/10/10:* standard split for object detection benchmarks. The val set is used by YOLO during training to select the best checkpoint (early stopping). The test set is held out entirely — it is only touched by `eval.py` for the final mAP measurement and CI gate comparison. Using val for both early stopping and final eval (a common mistake) would overfit the checkpoint selection to val and report optimistic test metrics.

*Why RANDOM_SEED=42:* reproducibility. Every run of `data_prep.py` produces the same split. The CI gate compares a new model's mAP against the registered baseline — if the splits were random, a metric regression could be caused by bad luck in split assignment, not by a real model change. Fixed seed eliminates this confounder.

*Alternatives considered:* 70/15/15 (more evaluation data, but 309K samples means val/test sets are already large enough at 10%). No fixed seed (non-reproducible splits — wrong for a system with a CI eval gate).

---

**[2026-10-16] Anti-UAV300 loaded by extracting frames from mp4, not pre-extracted images**
`load_anti_uav300_rgb()` in `data_prep.py` extracts frames from `visible.mp4` per sequence using OpenCV (`cv2.VideoCapture`), rather than loading pre-extracted JPEGs.

*Why:* the Anti-UAV300 download provides mp4 sequences with a `visible.json` annotation file per sequence. There are no pre-extracted frames in the download. Extracting with `cv2.VideoCapture` is idempotent — if frames already exist on disk (from a previous run), the loader skips extraction. First run: ~20-30 minutes for 318 sequences. Subsequent runs: seconds.

*Tradeoff:* requires `opencv-python` as a dependency (added to `services/perception/requirements.txt`). This is the only data prep dependency that is not stdlib. The alternative — using ffmpeg via subprocess — avoids the Python package dependency but adds an OS-level dependency that must be documented and present in any environment that runs `data_prep.py`.

*Alternatives considered:* ffmpeg subprocess (no Python package dep, but requires ffmpeg installed on host — harder to document). Pre-extract manually before running data_prep.py (manual step that breaks the reproducible pipeline).

---

**[2026-10-16] Bird annotations dropped from Drone-vs-Bird, images retained as hard negatives**
In `load_drone_vs_bird()`, annotation rows where `class_id != 0` (i.e., bird annotations) are filtered out. The images are retained in the dataset.

*Why drop bird annotations:* the detector is single-class (drone only, `nc=1`). YOLO ignores label files that reference class IDs beyond `nc`. Keeping bird annotations would cause YOLO to emit a warning on every batch and could confuse the loss calculation. Dropping them is the correct single-class treatment.

*Why keep the images:* bird images without any drone present become background frames (no label file = no positive object). These are **hard negatives** — visually similar to drone-present frames (outdoor, similar lighting, moving objects) but without the target. Training on hard negatives explicitly teaches the model that "bird in frame" should not produce a drone detection. Discarding these images entirely would reduce the precision of the final model.

*Alternatives considered:* Drop images with only bird annotations (loses hard negatives — precision degrades). Keep bird as a second class (requires multi-class training, contradicts the single-class decision). Relabel birds as drones (incorrect ground truth — would hurt precision on real drone detections).

---

**[2026-10-16] Class imbalance: 93.8% positive rate — no resampling, monitor cls_loss weight**
The merged dataset has 290,632 drone-present samples and 19,189 background samples (93.8% positive). No resampling or oversampling is applied in `data_prep.py`.

*Why no resampling in data_prep.py:* YOLO handles class imbalance during training via the `cls` loss weight parameter, not via dataset resampling. Resampling in the data pipeline (e.g., duplicating background frames) would create artificial correlations between samples in the same split, and any oversampled frames that appear in both train and val sets would invalidate the validation mAP. The correct place to address imbalance is in the training config.

*Action for train.py:* the `cls` hyperparameter in the YOLO training config controls how much weight is applied to the classification loss vs. the box regression loss. A high positive rate means the model can achieve low loss by predicting "drone" everywhere — `cls` must be tuned to penalize false positives. This will be addressed when running `train.py --mode finetune`.

*Tradeoff:* not resampling means the training loop will see far more positive examples than negatives per epoch. This is generally acceptable for object detection (vs. image classification) because YOLO's loss function already handles positive/negative anchor assignment internally. The risk is degraded precision on true negatives — worth monitoring in eval metrics.

*Alternatives considered:* Undersample positives to 1:1 ratio (loses 270K samples — wasteful and unnecessary for YOLO). Oversample negatives (duplicates and data leakage risk). Use weighted sampler in DataLoader (YOLO's Ultralytics trainer does not expose a custom sampler without patching).

---

**[2026-10-16] MAV-VID excluded: Kaggle source corrupted, Bitbucket clone fails**
MAV-VID (40,232 images, originally planned as the largest dataset source) is excluded from training.

*Why:* The Kaggle download was removed. The official Bitbucket clone (`git clone https://bitbucket.org/VisDrone/mav-vid`) fails with pack corruption (object pack hash mismatch). No verified mirror was found. With 309,821 samples from the remaining three datasets, MAV-VID is not required to achieve sufficient training data volume for YOLOv8s.

*Impact:* the missing 40K samples represent about 11% of the originally planned dataset size. DUT Anti-UAV (10K) + Anti-UAV300 (296K) + Drone-vs-Bird (2.9K) provides more than enough positive examples. Anti-UAV300's 296K frames are the dominant contributor.

*Alternatives considered:* Wait for MAV-VID to become available (indefinite blocker — Kaggle removal is permanent). Find a third-party mirror (not found; using unverified data for a defense system is inappropriate). Proceed without it (chosen — documented in progress.md and data/README.md).

---

**[2026-10-17] Training batch size: increase from 16 → 32 on next run**
On the RTX 3080 (10GB VRAM), YOLOv8s at batch=16 and imgsz=640 uses approximately 5GB VRAM — leaving 5GB unused. Batch size is the primary lever for GPU utilization and training speed.

*Recommended for next training run:* `FINETUNE_BATCH = 32` in `train.py`. Expected VRAM usage ~8GB, leaving 2GB headroom for PyTorch overhead. Estimated epoch time drops from ~25 min to ~14 min — a 44% speedup with no accuracy cost.

*LR adjustment required:* the linear scaling rule states that when batch size doubles, learning rate should scale proportionally to maintain the same effective update magnitude. `FINETUNE_LR0 = 0.02` (was 0.01) when moving to batch=32.

*Why not batch=48 or higher:* estimated VRAM at batch=48 exceeds 10GB — OOM risk. At full-stack deployment (vLLM LLM at ~4GB + TensorRT detector at ~1.5GB), VRAM headroom matters. Training at batch=32 is the safe ceiling.

*Why this cannot be applied mid-run:* batch size and LR are baked into `last.pt` via `train_args`. Changing them mid-resume would produce an inconsistent LR schedule (the scheduler was built around the original batch size). Apply on the next full run or when patching `last.pt` before extending to 100 epochs.

*Alternatives considered:* Increase imgsz to 1280 at batch=8 (uses similar VRAM, improves mAP50-95 via better spatial resolution — worth testing as a separate experiment after batch=32 baseline). Keep batch=16 (leaves GPU underutilized, no benefit).

---

**[2026-10-15] ROS 2 detection messages: std_msgs/String (JSON), not custom message types**
The Week 2 stub nodes use JSON-encoded std_msgs/String for detection messages on /detections.

*Why:* custom ROS 2 message types require a colcon build step every time the schema changes. During Week 2, the detection schema is still evolving. Using JSON over String allows schema iteration without rebuilds. In Week 3 when the schema is stable, migrating to a custom .msg file is a clean refactor — the topic name and node interfaces stay the same, only the serialization changes.

*Alternatives considered:* custom message type from day one (correct long-term, but adds build friction during rapid iteration). geometry_msgs/BoundingBox2D (doesn't include confidence or classification fields without combining multiple message types).

---

**[2026-10-15] ROS 2 stack: separate docker-compose.perception.yml, not merged with dev compose**
The ROS 2 perception stack runs in its own Docker Compose file separate from docker-compose.dev.yml.

*Why:* ros:humble is based on Ubuntu 22.04 (~900MB). Other services use Python slim images (~150MB). Merging them into one compose file means all services would rebuild from the Ubuntu base, adding unnecessary image weight and build time. The separate compose file also makes it clear which services are ROS 2 nodes vs. application services, and allows the perception stack to be brought up/down independently.

*Alternatives considered:* one compose file with Docker network (possible, but mixes Ubuntu and slim images with no clear boundary). Multi-stage build that installs ROS 2 into a slim base (complex, not supported well by the official ROS 2 installation path).

---

**[2026-10-16] ROS 2 Docker image: no pip packages for stub nodes**
Remove `ultralytics` and `mlflow` from the ROS 2 Dockerfile. Stub nodes use only `rclpy` and `std_msgs`, both installed via apt. Training scripts (`train.py`, `eval.py`, `data_prep.py`) run on the host — they are never copied into the Docker image. Saves ~500MB image size and ~2-3 min build time. When `redis_bridge_node` is added (Week 3), a separate `ros2_nodes/requirements.txt` containing only `redis>=5.0.0` will be created and installed in the Dockerfile.

*Alternatives considered:* Keep the existing pip install block (wastes ~500MB for packages never used inside the container). Create a separate slim `ros2_nodes/requirements.txt` now with only `redis` (premature — redis is not needed until the bridge node is built).

---

**[2026-10-16] ROS 2 networking: named bridge network, not host mode**
Replace `network_mode: host` with a named `ros2_net` Docker bridge network in `docker-compose.perception.yml`. DDS multicast discovery works within a Docker bridge network — containers in the same compose project on the same L2 segment discover each other's topics without host networking. `network_mode: host` on Docker Desktop (Windows/Mac) shares the Hyper-V VM's network namespace, not the actual Windows host network, which behaves differently than documented and is misleading during development. The named network is portable and explicit. On bare-metal Linux deployment, either approach works.

*Alternatives considered:* Keep `network_mode: host` with a Windows caveat comment (works on Linux, misleading on Docker Desktop — the wrong behaviour is silent). Use Cyclone DDS with `ROS_DISCOVERY_SERVER` instead of multicast (unnecessary complexity for a single-host compose stack).

---

## CI/CD

---

**[2026-10-14] CI eval gate: path-filtered jobs with MLflow baseline comparison**
CI runs eval jobs only when the relevant service paths change. Each eval job compares new metrics against the current production baseline stored in MLflow. A PR is blocked if any metric regresses.

*Why:* Running all evals on every PR would require a GPU runner for every commit, which is expensive and slow. Path filtering means: detector mAP only runs if perception code changes, RAG faithfulness only runs if agent code or docs change. MLflow baseline comparison means pass/fail is always relative ("does this change make things worse?") rather than absolute ("does this beat 0.85?"), which correctly catches regressions without being brittle to legitimate model improvements.

*Alternatives considered:* Evals only on release (too late — catches regressions after merging). Absolute thresholds only (brittle — a model improvement that also slightly changes the baseline would incorrectly fail). All evals always (too slow and expensive for every PR).

---

## Sync & DDIL

---

**[2026-10-14] Sync direction: bidirectional with domain-based authority**
Sync is bidirectional. Edge node is authoritative for operational data (incidents, pending_actions, audit_log). GovCloud is authoritative for model artifacts and configuration.

*Why:* The edge node was there when the incident happened — it is the ground truth for operational data. GovCloud wasn't there and should accept the edge's version. Conversely, GovCloud manages the model registry and approved configurations — it should push approved model versions and threshold updates down to the edge on uplink recovery. These two domains do not conflict, so no complex conflict resolution protocol is needed.

*Alternatives considered:* Edge-to-cloud only (GovCloud becomes read-only archive — loses the ability to push model updates to the edge, which is required for the MLOps pipeline). Cloud-wins always (incorrect — GovCloud had no visibility into the incident).

---

**[2026-10-14] Uplink detection: asyncio health check loop at 30-second interval**
A background asyncio task pings the GovCloud health endpoint every 30 seconds. State transitions (down → up, up → down) trigger sync or SSE status updates.

*Why:* Simple, no OS privileges required, works in any environment (laptop, Jetson, g5g), no external dependency. The 30-second polling interval is acceptable — sync is not time-critical. An instant reaction to uplink recovery is not required; 30 seconds is fine for a store-and-forward sync operation.

*Alternatives considered:* OS-level network events via D-Bus (more immediate but Linux-specific and requires OS coupling). tc-triggered webhook (requires the DDIL simulator to know the FastAPI address — tight coupling). Shorter polling interval (more responsive but adds unnecessary load on the uplink health endpoint).
