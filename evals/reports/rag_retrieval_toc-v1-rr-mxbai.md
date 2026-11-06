# RAG retrieval eval — `toc-v1-rr-mxbai`

golden set `8fd33e8f0b92` · 54 queries (43 answerable) · fetch_k=30 · unlocated rerank slots=0

## Aggregate (answerable queries only)

| metric | vector | hybrid | rerank | hybrid_rerank |
|---|---|---|---|---|
| mrr | 0.630 | 0.492 | 0.772 | 0.818 |
| recall_at_1 | 0.380 | 0.298 | 0.566 | 0.597 |
| hit_at_1 | 0.488 | 0.372 | 0.698 | 0.744 |
| ndcg_at_1 | 0.496 | 0.388 | 0.698 | 0.744 |
| precision_at_1 | 0.488 | 0.372 | 0.698 | 0.744 |
| recall_at_3 | 0.581 | 0.434 | 0.682 | 0.713 |
| hit_at_3 | 0.744 | 0.535 | 0.791 | 0.860 |
| ndcg_at_3 | 0.554 | 0.420 | 0.688 | 0.719 |
| precision_at_3 | 0.349 | 0.240 | 0.442 | 0.434 |
| recall_at_5 | 0.663 | 0.539 | 0.752 | 0.795 |
| hit_at_5 | 0.837 | 0.628 | 0.884 | 0.953 |
| ndcg_at_5 | 0.592 | 0.464 | 0.722 | 0.761 |
| precision_at_5 | 0.293 | 0.181 | 0.326 | 0.335 |
| recall_at_10 | 0.775 | 0.643 | 0.845 | 0.857 |
| hit_at_10 | 0.860 | 0.814 | 0.953 | 1.000 |
| ndcg_at_10 | 0.633 | 0.503 | 0.761 | 0.786 |
| precision_at_10 | 0.198 | 0.135 | 0.219 | 0.200 |

## recall_at_5 by query type

| type | n | vector | hybrid | rerank | hybrid_rerank |
|---|---|---|---|---|---|
| acronym | 8 | 0.94 | 1.00 | 1.00 | 1.00 |
| factoid | 6 | 0.58 | 0.17 | 0.75 | 0.75 |
| multihop | 15 | 0.47 | 0.38 | 0.59 | 0.61 |
| paraphrase | 14 | 0.75 | 0.61 | 0.79 | 0.89 |

## hit_at_5 by query type

| type | n | vector | hybrid | rerank | hybrid_rerank |
|---|---|---|---|---|---|
| acronym | 8 | 1.00 | 1.00 | 1.00 | 1.00 |
| factoid | 6 | 0.83 | 0.17 | 1.00 | 1.00 |
| multihop | 15 | 0.80 | 0.60 | 0.87 | 0.93 |
| paraphrase | 14 | 0.79 | 0.64 | 0.79 | 0.93 |

## Top rerank score: answerable vs unanswerable (for the score cutoff)

| cutoff | answerable kept | unanswerable kept (false answers) |
|---|---|---|
| >= -4 | 43/43 | 11/11 |
| >= -3 | 43/43 | 11/11 |
| >= -2 | 43/43 | 11/11 |
| >= -1 | 43/43 | 11/11 |
| >= 0 | 43/43 | 11/11 |
| >= 1 | 0/43 | 0/11 |
| >= 2 | 0/43 | 0/11 |
| >= 3 | 0/43 | 0/11 |

## Misses (rerank, answerable, no gold page in top 5)

- `ac-01` (paraphrase) top1=AC_107-2A.pdf:70-70 — My drone clipped a parked car and the repair came to about $800. Do I have to tell the FAA, and by when?
- `ac-11` (multihop) top1=AC_107-2A.pdf:25-25 — I built my own drone from parts and want to fly it at night over a crowded stadium event. What stands in my way?
- `atp-04` (paraphrase) top1=atp3-01-81.pdf:35-35 — A small tactical drone is crossing in front of my squad; how far ahead of it should we aim?
- `atp-06` (paraphrase) top1=AC_107-2A.pdf:102-103 — What are the fire-permission levels for engaging unknown drones, and which one is the most restrictive?
- `m300-11` (multihop) top1=M300_RTK_User_Manual_EN_v4.0.pdf:9-11 — With the stock batteries installed, roughly how much payload can we add to the Matrice 300 RTK and still operate at 5,000 m above sea level?
