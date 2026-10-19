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
FINETUNE_EPOCHS     = 50
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


# ── Resume interrupted run ─────────────────────────────────────────────────────

def run_resume(last_pt: Path, data_yaml: Path) -> None:
    """
    Resume a finetune run that was interrupted mid-epoch.

    Why just last_pt and no other hyperparams:
        Ultralytics writes all training args to args.yaml inside the run dir
        at the start of training. resume=True reads that file — passing data=,
        epochs=, lr0= etc. here would override the checkpoint values and could
        produce an inconsistent continuation (e.g. different LR schedule phase).

    What resume=True does internally:
        1. Reads <run_dir>/args.yaml for all hyperparams
        2. Restores optimizer state and LR scheduler from last.pt
        3. Sets start_epoch = last completed epoch + 1
        4. Continues until the original total epoch count is reached
    """
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_experiment(EXPERIMENT_NAME)

    with mlflow.start_run(run_name="finetune-resumed") as run:
        mlflow.set_tags({
            "stage": "development",
            "model": BASE_MODEL,
            "quantization": "fp32",
            "resumed_from": str(last_pt),
            "data_version": _get_data_version(data_yaml),
        })
        mlflow.log_params({
            "model": BASE_MODEL,
            "epochs": FINETUNE_EPOCHS,
            "mode": "resume",
        })

        model = YOLO(str(last_pt))

        # Ultralytics registers its own MlflowCallback during model.train().
        # That callback reads MLFLOW_TRACKING_URI from os.environ, finds the
        # run ID stored in args.yaml from the original finetune session, and
        # tries to log params (mode=train, model=last.pt) to it. Those params
        # are already set (mode=resume, model=yolov8s.pt) on this run, so
        # MLflow raises INVALID_PARAMETER_VALUE and Ultralytics drops tracking.
        #
        # Fix: pop MLFLOW_TRACKING_URI from the environment for the duration
        # of model.train() so Ultralytics' callback no-ops. Our mlflow client
        # is unaffected — mlflow.set_tracking_uri() above set the URI on the
        # Python client directly, independent of the env var.
        saved_uri = os.environ.pop("MLFLOW_TRACKING_URI", None)
        t0 = time.perf_counter()
        try:
            train_results = model.train(resume=True)
        finally:
            if saved_uri is not None:
                os.environ["MLFLOW_TRACKING_URI"] = saved_uri
        train_time = time.perf_counter() - t0

        best_weights = Path(train_results.save_dir) / "weights" / "best.pt"
        best_model = YOLO(str(best_weights))
        val_results = best_model.val(data=str(data_yaml))

        metrics = _extract_metrics(val_results)
        metrics["train_time_s"] = train_time
        mlflow.log_metrics(metrics)

        mlflow.log_artifact(str(best_weights), artifact_path="weights")

        model_uri = f"runs:/{run.info.run_id}/weights"
        mlflow.register_model(model_uri=model_uri, name=MODEL_NAME)

        print(f"\n[resume] mAP50: {metrics.get('val/map50', 'N/A'):.4f}")
        print(f"[resume] mAP50-95: {metrics.get('val/map50_95', 'N/A'):.4f}")
        print(f"[resume] Training time: {train_time / 60:.1f}min")
        print(f"[resume] Run ID: {run.info.run_id}")
        print(f"[resume] Model registered as '{MODEL_NAME}'")


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
    Return the first 12 chars of the DVC md5 hash for the tracked dataset directory.

    How it works:
        Reads the dataset root path from the data YAML, then looks for a
        <dataset_root>.dvc file (e.g. data/raw/merged.dvc). DVC .dvc files
        store an outs[0].md5 field — the content hash of the directory tree.
        That hash is the data version: two runs with the same hash used the
        same exact dataset bytes.

    Why read the YAML instead of deriving from data_yaml path:
        The .dvc file is created by `dvc add data/raw/merged`, not by tracking
        the YAML config file. The YAML records where the data lives; DVC tracks
        the data itself. These are different paths.

    Why 12 chars: sufficient for uniqueness in MLflow UI, matches git short-SHA
    convention for readability.
    """
    import yaml as _yaml
    try:
        with open(data_yaml) as f:
            cfg = _yaml.safe_load(f)
        dataset_root = Path(cfg["path"])
        dvc_file = Path(str(dataset_root) + ".dvc")
        if dvc_file.exists():
            with open(dvc_file) as f:
                dvc_meta = _yaml.safe_load(f)
            return dvc_meta["outs"][0]["md5"][:12]
    except Exception:
        pass
    return "untracked"


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Train or evaluate drone detector")
    parser.add_argument("--mode", choices=["baseline", "finetune", "resume"], required=True,
                        help="baseline: eval pretrained COCO on drone val. finetune: train on drone data. resume: continue interrupted finetune from last.pt.")
    parser.add_argument("--last-pt", type=Path,
                        default=Path("runs/detect/runs/train/drone-finetune/weights/last.pt"),
                        help="Path to last.pt checkpoint (resume mode only)")
    parser.add_argument("--data", type=Path, default=Path("models/configs/yolo_data.yaml"),
                        help="Path to YOLO dataset yaml (generated by data_prep.py)")
    args = parser.parse_args()

    if not args.data.exists():
        print(f"ERROR: {args.data} not found. Run data_prep.py first.")
        return

    if args.mode == "baseline":
        run_baseline(args.data)
    elif args.mode == "finetune":
        run_finetune(args.data)
    else:
        if not args.last_pt.exists():
            print(f"ERROR: {args.last_pt} not found. Check the path to last.pt.")
            return
        run_resume(args.last_pt, args.data)


if __name__ == "__main__":
    main()
