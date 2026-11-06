"""
evals/metrics/rag_retrieval_eval.py — score retrieval against the golden set.

For every golden query it runs the real retrieval stages from services/agents/tools.py
and scores two orderings of the same candidate pool:
    vector  = pgvector HNSW cosine order (what you'd get with no re-ranker)
    rerank  = cross-encoder order (what search_knowledge_base returns)
Chunks are mapped to pages (stored metadata if present, else chunk_locator) and scored
by page-span overlap with the gold labels (see retrieval_metrics.py).

Usage (use the training venv):
    .venv-training/Scripts/python.exe -m evals.metrics.rag_retrieval_eval --label baseline-regex-v0
"""

import argparse
import csv
import hashlib
import json
import logging
import statistics
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from evals.metrics.chunk_locator import locate  # noqa: E402
from evals.metrics.retrieval_metrics import (  # noqa: E402
    Span, hit_at_k, mrr, ndcg_at_k, precision_at_k, recall_at_k,
)
from models.configs import mlflow_config as mcfg  # noqa: E402
from services.agents.tools import rerank, retrieve_candidates  # noqa: E402

logger = logging.getLogger("rag_eval")
KS = (1, 3, 5, 10)
FETCH_K = 30  # candidate pool for both orderings (production uses top_k*3 = 15 at k=5)
CUTOFFS = (-4, -3, -2, -1, 0, 1, 2, 3)
# vector = HNSW order; hybrid = vector+keyword RRF order; *rerank = cross-encoder over that pool
PIPES = ("vector", "hybrid", "rerank", "hybrid_rerank")


def load_golden(path: Path) -> tuple[list[dict], str]:
    raw = path.read_bytes()
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    return rows, hashlib.sha256(raw).hexdigest()[:12]


def to_span(chunk: dict, cache: dict) -> Span:
    key = (chunk["source"], chunk["content"])
    if key not in cache:
        cache[key] = locate(chunk["content"], chunk["source"], chunk.get("metadata"))
    loc = cache[key]
    return Span(chunk["source"], *loc) if loc else Span("<unlocated>", 0, 0)


def score(retrieved: list[Span], gold: list[Span]) -> dict:
    out = {"mrr": mrr(retrieved, gold)}
    for k in KS:
        out[f"recall_at_{k}"] = recall_at_k(retrieved, gold, k)
        out[f"precision_at_{k}"] = precision_at_k(retrieved, gold, k)
        out[f"hit_at_{k}"] = hit_at_k(retrieved, gold, k)
        out[f"ndcg_at_{k}"] = ndcg_at_k(retrieved, gold, k)
    return out


def evaluate(rows: list[dict], reranker: str | None = None) -> tuple[list[dict], dict]:
    cache: dict = {}
    per_query, unlocated = [], 0
    for r in rows:
        cands = retrieve_candidates(r["query"], FETCH_K)
        hyb = retrieve_candidates(r["query"], FETCH_K, mode="hybrid")
        ranked = rerank(r["query"], cands, reranker)
        pipes = {"vector": cands[:max(KS)], "hybrid": hyb[:max(KS)],
                 "rerank": ranked[:max(KS)], "hybrid_rerank": rerank(r["query"], hyb, reranker)[:max(KS)]}
        gold = [Span(g["doc"], g["page_start"], g["page_end"], g["grade"]) for g in r["relevant"]]
        rec = {"id": r["id"], "type": r["type"], "answerable": r["answerable"], "query": r["query"],
               "top_rerank_score": ranked[0]["score"] if ranked else None}
        for name, chunks in pipes.items():
            spans = [to_span(c, cache) for c in chunks]
            unlocated += sum(s.doc == "<unlocated>" for s in spans) if name == "rerank" else 0
            rec[name] = score(spans, gold) if gold else {}
            first = next((i + 1 for i, s in enumerate(spans) if any(s.doc == g.doc and s.page_start <= g.page_end
                          and g.page_start <= s.page_end for g in gold)), None)
            rec[f"{name}_first_hit_rank"] = first
            rec[f"{name}_top1"] = f"{spans[0].doc}:{spans[0].page_start}-{spans[0].page_end}" if spans else ""
        per_query.append(rec)
    return per_query, {"unlocated_rerank_slots": unlocated}


def aggregate(per_query: list[dict]) -> dict:
    ans = [q for q in per_query if q["answerable"]]
    agg: dict[str, float] = {}
    for pipe in PIPES:
        for m in ans[0][pipe]:
            agg[f"{pipe}_{m}"] = statistics.mean(q[pipe][m] for q in ans)
    return agg


def by_type(per_query: list[dict], pipe: str, metric: str) -> dict:
    out = {}
    for t in sorted({q["type"] for q in per_query if q["answerable"]}):
        qs = [q for q in per_query if q["answerable"] and q["type"] == t]
        out[t] = (statistics.mean(q[pipe][metric] for q in qs), len(qs))
    return out


def cutoff_table(per_query: list[dict]) -> list[tuple]:
    ans = [q["top_rerank_score"] for q in per_query if q["answerable"] and q["top_rerank_score"] is not None]
    una = [q["top_rerank_score"] for q in per_query if not q["answerable"] and q["top_rerank_score"] is not None]
    return [(c, sum(s >= c for s in ans), len(ans), sum(s >= c for s in una), len(una)) for c in CUTOFFS]


def write_report(label, sha, per_query, agg, extra, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    n_ans = sum(q["answerable"] for q in per_query)
    md = [f"# RAG retrieval eval — `{label}`", "",
          f"golden set `{sha}` · {len(per_query)} queries ({n_ans} answerable) · fetch_k={FETCH_K} · "
          f"unlocated rerank slots={extra['unlocated_rerank_slots']}", "",
          "## Aggregate (answerable queries only)", "",
          "| metric | " + " | ".join(PIPES) + " |", "|---|" + "---|" * len(PIPES)]
    for m in ["mrr"] + [f"{x}_at_{k}" for k in KS for x in ("recall", "hit", "ndcg", "precision")]:
        md.append(f"| {m} | " + " | ".join(f"{agg[f'{p}_{m}']:.3f}" for p in PIPES) + " |")
    for metric in ("recall_at_5", "hit_at_5"):
        md += ["", f"## {metric} by query type", "", "| type | n | " + " | ".join(PIPES) + " |",
               "|---|---|" + "---|" * len(PIPES)]
        tabs = {p: by_type(per_query, p, metric) for p in PIPES}
        md += [f"| {t} | {tabs['vector'][t][1]} | " + " | ".join(f"{tabs[p][t][0]:.2f}" for p in PIPES) + " |"
               for t in tabs["vector"]]
    md += ["", "## Top rerank score: answerable vs unanswerable (for the score cutoff)", "",
           "| cutoff | answerable kept | unanswerable kept (false answers) |", "|---|---|---|"]
    md += [f"| >= {c} | {a}/{na} | {u}/{nu} |" for c, a, na, u, nu in cutoff_table(per_query)]
    md += ["", "## Misses (rerank, answerable, no gold page in top 5)", ""]
    md += [f"- `{q['id']}` ({q['type']}) top1={q['rerank_top1']} — {q['query']}"
           for q in per_query if q["answerable"] and q["rerank"]["hit_at_5"] == 0.0]
    report = out_dir / f"rag_retrieval_{label}.md"
    report.write_text("\n".join(md) + "\n", encoding="utf-8")
    csv_path = out_dir / f"rag_retrieval_{label}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "type", "answerable", "top_rerank_score", "vector_first_hit_rank",
                    "rerank_first_hit_rank", "vector_top1", "rerank_top1", "query"])
        for q in per_query:
            w.writerow([q["id"], q["type"], q["answerable"], f"{q['top_rerank_score']:.3f}",
                        q["vector_first_hit_rank"], q["rerank_first_hit_rank"],
                        q["vector_top1"], q["rerank_top1"], q["query"]])
    return report, csv_path


def log_mlflow(label, sha, agg, report: Path, csv_path: Path, reranker: str) -> None:
    import mlflow
    mlflow.set_tracking_uri(mcfg.TRACKING_URI)
    mlflow.set_experiment(mcfg.EXPERIMENT_RAG)
    with mlflow.start_run(run_name=label):
        mlflow.log_params({"chunker_version": label, "fetch_k": FETCH_K, "golden_sha": sha,
                           "embed_model": "nomic-embed-text-v1.5", "tsconfig": "simple", "rrf_k": 60,
                           "reranker": reranker})
        mlflow.log_metrics({k: round(v, 4) for k, v in agg.items()})
        mlflow.set_tag(mcfg.TAG_STAGE, "development")
        mlflow.log_artifact(str(report))
        mlflow.log_artifact(str(csv_path))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, help="chunker/config version, e.g. baseline-regex-v0")
    ap.add_argument("--golden", default=str(ROOT / "evals" / "golden" / "rag_golden.jsonl"))
    ap.add_argument("--no-mlflow", action="store_true")
    ap.add_argument("--reranker", default=None, help="cross-encoder model name (default: tools.DEFAULT_RERANKER)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    rows, sha = load_golden(Path(args.golden))
    from services.agents.tools import DEFAULT_RERANKER
    reranker = args.reranker or DEFAULT_RERANKER
    per_query, extra = evaluate(rows, reranker)
    agg = aggregate(per_query)
    report, csv_path = write_report(args.label, sha, per_query, agg, extra, ROOT / "evals" / "reports")
    print(report.read_text(encoding="utf-8"))
    if not args.no_mlflow:
        try:
            log_mlflow(args.label, sha, agg, report, csv_path, reranker)
            print(f"MLflow: logged to {mcfg.TRACKING_URI} experiment '{mcfg.EXPERIMENT_RAG}'")
        except Exception as e:  # report files are the source of truth; MLflow is best-effort
            print(f"MLflow logging FAILED ({type(e).__name__}: {e}) — report/CSV were still written")
