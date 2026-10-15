"""
YOLO drone detector training script with MLflow integration.

What it does:
    Runs a YOLO training job and logs all metrics, hyperparameters,
    and the best model artifact to MLflow. Supports two modes:
      - baseline: no fine-tuning, COCO pretrained weights only (used to
                  establish the comparison floor for the CI gate)
      - finetune: full fine-tuning on the drone dataset

Why MLflow from the start:
    The CI eval gate (detector-eval) compares new runs against the registered
    baseline. If the baseline isn't logged to MLflow, there's nothing to gate on.
    Log both runs before writing any CI yaml.

Why YOLOv8s (small), not nano:
    YOLOv8n is faster to train but the real deployment target is INT8 TensorRT
    on a resource-constrained GPU. YOLOv8s at INT8 hits ~60+ FPS on RTX-class
    hardware with acceptable mAP. Nano saves ~10min of training; small is the
    honest target.

Usage:
    # Baseline (pretrained COCO, no training — establishes comparison floor):
    python services/perception/train.py --mode baseline --data models/configs/yolo_data.yaml

    # Fine-tuning on drone data:
    python services/perception/train.py --mode finetune --data models/configs/yolo_data.yaml

    Both commands require: MLFLOW_TRACKING_URI in .env (or set to localhost:5000 for dev)
"""

import argparse
import os
import time
from pathlib import Path

import mlflow
import mlflow.artifacts
from dotenv import load_dotenv
from ultralytics import YOLO


# ── Config ─────────────────────────────────────────────────────────────────────

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")
EXPERIMENT_NAME     = "drone-detector"
MODEL_NAME          = "drone-detector"          # MLflow registry name
BASE_MODEL          = "yolov8s.pt"              # YOLOv8 small, COCO pretrained

# Fine-tuning hyperparameters — document if you change these
FINETUNE_EPOCHS     = 100
FINETUNE_IMGSZ      = 640
FINETUNE_BATCH      = 16
FINETUNE_LR0        = 0.01
FINETUNE_LRF        = 0.001


# ── Baseline run ───────────────────────────────────────────────────────────────

def run_baseline(data_yaml: Path) -> None:
    """
    Evaluate the COCO-pretrained YOLOv8s on the drone val set without any
    fine-tuning. This is not a training run — it measures how well the
    COCO model generalizes to drone detection out of the box.

    The drone class is most closely aligned with COCO's 'airplane' (id=4)
    and 'kite' (id=33) — neither is drone-specific. Expect low mAP.
    That's the point: it shows what fine-tuning buys you.

    Why separate from finetune mode:
        The baseline run must use the original COCO weights with no parameter
        updates. If fine-tuning ran first and we evaluated the same model,
        it wouldn't be a fair baseline comparison.
    """
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_experiment(EXPERIMENT_NAME)

    with mlflow.start_run(run_name="baseline-coco-pretrained") as run:
        mlflow.set_tags({
            "stage": "baseline",
            "model": BASE_MODEL,
            "quantization": "fp32",
            "data_version": _get_data_version(data_yaml),
        })

        model = YOLO(BASE_MODEL)

        # Evaluate on drone val set — no training
        t0 = time.perf_counter()
        results = model.val(data=str(data_yaml))
        elapsed = time.perf_counter() - t0

        metrics = _extract_metrics(results)
        metrics["eval_time_s"] = elapsed

        mlflow.log_params({
            "model": BASE_MODEL,
            "imgsz": FINETUNE_IMGSZ,
            "mode": "baseline",
        })
        mlflow.log_metrics(metrics)

        # Log the unmodified pretrained weights as baseline artifact
        mlflow.log_artifact(BASE_MODEL, artifact_path="weights")

        print(f"\n[baseline] mAP50: {metrics.get('val/map50', 'N/A'):.4f}")
        print(f"[baseline] mAP50-95: {metrics.get('val/map50_95', 'N/A'):.4f}")
        print(f"[baseline] Eval time: {elapsed:.1f}s")
        print(f"[baseline] Run ID: {run.info.run_id}")


# ── Fine-tuning run ────────────────────────────────────────────────────────────

def run_finetune(data_yaml: Path) -> None:
    """
    Fine-tune YOLOv8s from COCO pretrained weights on the merged drone dataset.
    Logs training curves (loss per epoch), final metrics, and the best checkpoint
    to MLflow. Registers the best model in the MLflow model registry.

    Why start from pretrained (not random init):
        Transfer learning from COCO gives the model low-level feature detectors
        (edges, textures, shapes) for free. Training from scratch would need
        10-100x more data and time to reach the same mAP.

    What 'best' means in YOLO:
        Ultralytics saves best.pt as the checkpoint with the highest val mAP50
        across all epochs. This is what we register — not last.pt.
    """
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_experiment(EXPERIMENT_NAME)

    with mlflow.start_run(run_name="finetune-real-data-v1") as run:
        data_version = _get_data_version(data_yaml)
        mlflow.set_tags({
            "stage": "development",
            "model": BASE_MODEL,
            "quantization": "fp32",
            "data_version": data_version,
        })

        params = {
            "model":   BASE_MODEL,
            "epochs":  FINETUNE_EPOCHS,
            "imgsz":   FINETUNE_IMGSZ,
            "batch":   FINETUNE_BATCH,
            "lr0":     FINETUNE_LR0,
            "lrf":     FINETUNE_LRF,
            "mode":    "finetune",
        }
        mlflow.log_params(params)

        model = YOLO(BASE_MODEL)

        # Train — Ultralytics handles augmentation, checkpointing, and early stopping
        t0 = time.perf_counter()
        train_results = model.train(
            data=str(data_yaml),
            epochs=FINETUNE_EPOCHS,
            imgsz=FINETUNE_IMGSZ,
            batch=FINETUNE_BATCH,
            lr0=FINETUNE_LR0,
            lrf=FINETUNE_LRF,
            project="runs/train",
            name="drone-finetune",
            exist_ok=True,
            verbose=True,
        )
        train_time = time.perf_counter() - t0

        # Evaluate best checkpoint on val set
        best_weights = Path(train_results.save_dir) / "weights" / "best.pt"
        best_model = YOLO(str(best_weights))
        val_results = best_model.val(data=str(data_yaml))

        metrics = _extract_metrics(val_results)
        metrics["train_time_s"] = train_time
        mlflow.log_metrics(metrics)

        # Log best weights artifact
        mlflow.log_artifact(str(best_weights), artifact_path="weights")

        # Register in MLflow model registry as drone-detector v1
        # This is what the CI gate and sync worker reference
        model_uri = f"runs:/{run.info.run_id}/weights"
        mlflow.register_model(model_uri=model_uri, name=MODEL_NAME)

        print(f"\n[finetune] mAP50: {metrics.get('val/map50', 'N/A'):.4f}")
        print(f"[finetune] mAP50-95: {metrics.get('val/map50_95', 'N/A'):.4f}")
        print(f"[finetune] Training time: {train_time / 60:.1f}min")
        print(f"[finetune] Run ID: {run.info.run_id}")
        print(f"[finetune] Model registered as '{MODEL_NAME}'")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _extract_metrics(val_results) -> dict[str, float]:
    """
    Pull the metrics we care about from the Ultralytics results object.
    The results object has different attribute layouts depending on version —
    this helper centralizes the extraction so we only fix it in one place.
    """
    metrics = {}
    try:
        # Ultralytics >= 8.0: results.results_dict contains all metrics
        rd = val_results.results_dict
        metrics["val/map50"]    = float(rd.get("metrics/mAP50(B)",    0.0))
        metrics["val/map50_95"] = float(rd.get("metrics/mAP50-95(B)", 0.0))
        metrics["val/precision"]= float(rd.get("metrics/precision(B)", 0.0))
        metrics["val/recall"]   = float(rd.get("metrics/recall(B)",    0.0))
    except AttributeError:
        # Fallback for older Ultralytics builds
        metrics["val/map50"]    = float(getattr(val_results, "map50",    0.0))
        metrics["val/map50_95"] = float(getattr(val_results, "map",      0.0))
    return metrics


def _get_data_version(data_yaml: Path) -> str:
    """
    Return DVC hash of the dataset for MLflow tagging.
    If DVC is not initialized or the file is not tracked, returns 'untracked'.

    The data_version tag links an MLflow run to an exact DVC dataset version,
    enabling reproducibility: given run_id -> data_version -> dvc checkout.
    """
    dvc_lock = data_yaml.parent / (data_yaml.name + ".dvc")
    if dvc_lock.exists():
        # STUB — replace with real DVC API call when DVC is tracking merged/
        import subprocess
        try:
            result = subprocess.run(
                ["dvc", "status", str(data_yaml)],
                capture_output=True, text=True, timeout=5
            )
            return result.stdout.strip()[:40] or "tracked"
        except Exception:
            return "dvc-error"
    return "untracked"


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Train or evaluate drone detector")
    parser.add_argument("--mode", choices=["baseline", "finetune"], required=True,
                        help="baseline: eval pretrained COCO on drone val. finetune: train on drone data.")
    parser.add_argument("--data", type=Path, default=Path("models/configs/yolo_data.yaml"),
                        help="Path to YOLO dataset yaml (generated by data_prep.py)")
    args = parser.parse_args()

    if not args.data.exists():
        print(f"ERROR: {args.data} not found. Run data_prep.py first.")
        return

    if args.mode == "baseline":
        run_baseline(args.data)
    else:
        run_finetune(args.data)


if __name__ == "__main__":
    main()
