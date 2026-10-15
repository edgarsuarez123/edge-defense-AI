"""
Shared MLflow configuration — import this wherever you log a run.
Centralizing experiment names and metric keys prevents typos that
create duplicate experiments or make CI baseline comparisons fail.
"""
import os

# Tracking server — points to the MLflow Docker service in dev,
# or to the GovCloud MLflow instance when syncing via uplink.
TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")

# ── Experiment names ───────────────────────────────────────────────────────────
# One experiment per model type. All runs for a model go into one experiment.
EXPERIMENT_DETECTOR   = "drone-detector"       # YOLO fine-tuning runs
EXPERIMENT_DECISION   = "decision-model"       # triage model runs
EXPERIMENT_RF_AUDIO   = "rf-audio-classifier"  # RF + audio classifier runs
EXPERIMENT_RAG        = "rag-pipeline"         # embedding + retrieval eval runs

# ── Metric keys ───────────────────────────────────────────────────────────────
# Detector
METRIC_MAP50          = "mAP50"        # mAP at IoU=0.50
METRIC_MAP50_95       = "mAP50-95"     # mAP averaged over IoU 0.50:0.95
METRIC_FPS            = "fps"          # inference frames per second

# Decision model
METRIC_ECE            = "ece"          # Expected Calibration Error (lower = better)
METRIC_PRECISION       = "precision"
METRIC_RECALL         = "recall"
METRIC_F1             = "f1"

# RAG
METRIC_FAITHFULNESS   = "faithfulness"          # Ragas faithfulness score
METRIC_CITATION_ACC   = "citation_accuracy"     # fraction of answers that cite a retrieved chunk
METRIC_ROUTING_ACC    = "routing_accuracy"      # fraction of requests routed to correct agent

# ── Tag keys ──────────────────────────────────────────────────────────────────
TAG_STAGE             = "stage"        # values: 'development' | 'staging' | 'production'
TAG_QUANTIZATION      = "quantization" # values: 'fp32' | 'fp16' | 'int8' | 'awq'
TAG_DATA_VERSION      = "data_version" # DVC dataset hash for reproducibility
