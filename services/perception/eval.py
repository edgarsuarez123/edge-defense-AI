"""
Detector evaluation script — measures mAP and FPS for any checkpoint.

What it does:
    Loads a YOLO checkpoint, runs inference on the val or test split,
    and logs mAP50, mAP50-95, precision, recall, and FPS to MLflow.
    Also outputs a local text report for CI.

Why separate from train.py:
    The CI gate needs to evaluate any checkpoint (registered model, candidate
    PR model, TensorRT INT8 export) without triggering a new training run.
    eval.py is the single evaluation entry point regardless of what created
    the weights.

Usage:
    # Evaluate a local checkpoint file
    python services/perception/eval.py --weights runs/train/drone-finetune/weights/best.pt

    # Evaluate the registered MLflow model (latest version)
    python services/perception/eval.py --mlflow-model drone-detector --mlflow-version latest

    # Compare against baseline (used by CI gate)
    python services/perception/eval.py --weights best.pt --compare-baseline
"""

import argparse
import os
import time
from pathlib import Path

import mlflow
from dotenv import load_dotenv
from ultralytics import YOLO


load_dotenv(Path(__file__).resolve().parents[2] / ".env")

MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")
EXPERIMENT_NAME     = "drone-detector"
MODEL_NAME          = "drone-detector"

# FPS measurement: warm up, then time N inference passes on the val set
FPS_WARMUP_BATCHES  = 3
FPS_MEASURE_BATCHES = 10


def evaluate(
    weights_path: Path | None,
    mlflow_model: str | None,
    mlflow_version: str | None,
    data_yaml: Path,
    split: str,
    compare_baseline: bool,
) -> dict[str, float]:
    """
    Load weights, evaluate on the specified split, log to MLflow, return metrics.

    Two weight sources are supported:
      - Local .pt file (weights_path)
      - MLflow registry (mlflow_model + mlflow_version)

    Only one source may be specified. MLflow registry lookup is used by CI.
    """
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_experiment(EXPERIMENT_NAME)

    # Resolve model path
    if mlflow_model:
        version_str = mlflow_version or "latest"
        model_uri = f"models:/{mlflow_model}/{version_str}"
        local_path = mlflow.artifacts.download_artifacts(model_uri)
        weights_path = Path(local_path)
        print(f"[eval] Loaded from MLflow registry: {model_uri} -> {weights_path}")
    elif weights_path:
        print(f"[eval] Loading checkpoint: {weights_path}")
    else:
        raise ValueError("Specify either --weights or --mlflow-model")

    model = YOLO(str(weights_path))

    with mlflow.start_run(run_name=f"eval-{weights_path.stem}") as run:
        mlflow.set_tags({
            "stage":   "eval",
            "split":   split,
            "weights": str(weights_path),
        })
        mlflow.log_param("split", split)

        # Accuracy metrics
        val_results = model.val(data=str(data_yaml), split=split)
        metrics = _extract_metrics(val_results)

        # FPS measurement
        fps = _measure_fps(model, data_yaml, split)
        metrics["fps"] = fps

        mlflow.log_metrics(metrics)

        print(f"\n[eval] mAP50:     {metrics.get('val/map50', 0):.4f}")
        print(f"[eval] mAP50-95:  {metrics.get('val/map50_95', 0):.4f}")
        print(f"[eval] Precision:  {metrics.get('val/precision', 0):.4f}")
        print(f"[eval] Recall:     {metrics.get('val/recall', 0):.4f}")
        print(f"[eval] FPS:        {fps:.1f}")

        # CI gate: compare against registered baseline
        if compare_baseline:
            passed = _check_baseline(metrics, run.info.run_id)
            if not passed:
                raise SystemExit(1)  # Non-zero exit blocks the CI job

    return metrics


def _measure_fps(model: YOLO, data_yaml: Path, split: str) -> float:
    """
    Measure inference FPS on a fixed 100-image sample from the split.

    Why predict() on a sample instead of val() on the full set:
        model.val() runs the complete validation loop (loss computation, metric
        accumulation, NMS post-processing) on all 31K val images. Calling it 13
        times for warmup + measurement would process ~400K images unnecessarily.
        model.predict() is pure inference — no loss, no metric tracking — and is
        what actually runs at deployment time. FPS measured on predict() is the
        number that goes in the T&E report.

    Why 100 images:
        Enough to amortize Python overhead and get a stable GPU throughput reading.
        The full val set takes minutes per pass; 100 images takes ~2 seconds.

    Why warmup passes:
        The first inference pass has CUDA JIT kernel compilation overhead.
        Warmup saturates the pipeline; measurement passes reflect true throughput.

    Returns: FPS as images per second (images * passes / elapsed).
    """
    import torch
    import yaml

    # Resolve the image directory from the data yaml
    with open(data_yaml) as f:
        data_cfg = yaml.safe_load(f)
    dataset_root = Path(data_cfg["path"])
    img_dir = dataset_root / data_cfg[split]

    # Collect a fixed sample of 100 images for FPS measurement
    img_extensions = {".jpg", ".jpeg", ".png", ".bmp"}
    all_images = [p for p in img_dir.iterdir() if p.suffix.lower() in img_extensions]
    sample = sorted(all_images)[:100]   # sorted for reproducibility
    sample_paths = [str(p) for p in sample]

    if not sample_paths:
        return 0.0

    # Warmup passes — results discarded
    for _ in range(FPS_WARMUP_BATCHES):
        model.predict(sample_paths, verbose=False)

    # Measurement passes
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    t0 = time.perf_counter()
    for _ in range(FPS_MEASURE_BATCHES):
        model.predict(sample_paths, verbose=False)
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    elapsed = time.perf_counter() - t0

    fps = (len(sample_paths) * FPS_MEASURE_BATCHES) / elapsed
    return fps


def _check_baseline(candidate_metrics: dict[str, float], candidate_run_id: str) -> bool:
    """
    Compare candidate metrics against the registered baseline run in MLflow.
    Returns True if candidate is at least as good as baseline on mAP50.

    The CI gate blocks on any mAP regression, not just absolute threshold.
    'At least as good' means within 0.5% to absorb measurement variance.

    Why relative not absolute:
        A model improved from 0.65 to 0.75 mAP should still pass even if the
        baseline was 0.74 — absolute thresholds don't capture legitimate progress.
        Relative comparison catches regressions without being brittle.
    """
    client = mlflow.tracking.MlflowClient()

    # Find the baseline run: tagged stage=baseline
    runs = client.search_runs(
        experiment_ids=[mlflow.get_experiment_by_name(EXPERIMENT_NAME).experiment_id],
        filter_string="tags.stage = 'baseline'",
        order_by=["start_time DESC"],
        max_results=1,
    )
    if not runs:
        print("[ci-gate] No baseline run found — skipping comparison")
        return True

    baseline_map50 = runs[0].data.metrics.get("val/map50", 0.0)
    candidate_map50 = candidate_metrics.get("val/map50", 0.0)

    tolerance = 0.005  # 0.5%
    passed = candidate_map50 >= (baseline_map50 - tolerance)

    print(f"\n[ci-gate] Baseline mAP50:  {baseline_map50:.4f}")
    print(f"[ci-gate] Candidate mAP50: {candidate_map50:.4f}")
    print(f"[ci-gate] {'PASS' if passed else 'FAIL'} (tolerance: {tolerance})")

    return passed


def _extract_metrics(val_results) -> dict[str, float]:
    """Extract metrics from Ultralytics results object (version-agnostic)."""
    metrics = {}
    try:
        rd = val_results.results_dict
        metrics["val/map50"]     = float(rd.get("metrics/mAP50(B)",    0.0))
        metrics["val/map50_95"]  = float(rd.get("metrics/mAP50-95(B)", 0.0))
        metrics["val/precision"] = float(rd.get("metrics/precision(B)", 0.0))
        metrics["val/recall"]    = float(rd.get("metrics/recall(B)",    0.0))
    except AttributeError:
        metrics["val/map50"]    = float(getattr(val_results, "map50", 0.0))
        metrics["val/map50_95"] = float(getattr(val_results, "map",   0.0))
    return metrics


def main():
    parser = argparse.ArgumentParser(description="Evaluate drone detector checkpoint")
    parser.add_argument("--weights",          type=Path,  help="Path to .pt checkpoint file")
    parser.add_argument("--mlflow-model",     type=str,   help="MLflow registry model name")
    parser.add_argument("--mlflow-version",   type=str,   default="latest", help="MLflow registry version")
    parser.add_argument("--data",             type=Path,  default=Path("models/configs/yolo_data.yaml"))
    parser.add_argument("--split",            type=str,   default="val", choices=["val", "test"])
    parser.add_argument("--compare-baseline", action="store_true", help="Run CI gate comparison")
    args = parser.parse_args()

    evaluate(
        weights_path=args.weights,
        mlflow_model=args.mlflow_model,
        mlflow_version=args.mlflow_version,
        data_yaml=args.data,
        split=args.split,
        compare_baseline=args.compare_baseline,
    )


if __name__ == "__main__":
    main()
