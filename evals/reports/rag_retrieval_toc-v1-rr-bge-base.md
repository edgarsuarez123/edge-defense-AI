# RAG retrieval eval — `toc-v1-rr-bge-base`

golden set `8fd33e8f0b92` · 54 queries (43 answerable) · fetch_k=30 · unlocated rerank slots=0

## Aggregate (answerable queries only)

| metric | vector | hybrid | rerank | hybrid_rerank |
|---|---|---|---|---|
| mrr | 0.630 | 0.492 | 0.657 | 0.699 |
| recall_at_1 | 0.380 | 0.298 | 0.426 | 0.473 |
| hit_at_1 | 0.488 | 0.372 | 0.558 | 0.605 |
| ndcg_at_1 | 0.496 | 0.388 | 0.574 | 0.620 |
| precision_at_1 | 0.488 | 0.372 | 0.558 | 0.605 |
| recall_at_3 | 0.581 | 0.434 | 0.640 | 0.647 |
| hit_at_3 | 0.744 | 0.535 | 0.767 | 0.791 |
| ndcg_at_3 | 0.554 | 0.420 | 0.606 | 0.625 |
| precision_at_3 | 0.349 | 0.240 | 0.341 | 0.333 |
| recall_at_5 | 0.663 | 0.539 | 0.678 | 0.717 |
| hit_at_5 | 0.837 | 0.628 | 0.791 | 0.837 |
| ndcg_at_5 | 0.592 | 0.464 | 0.624 | 0.656 |
| precision_at_5 | 0.293 | 0.181 | 0.279 | 0.260 |
| recall_at_10 | 0.775 | 0.643 | 0.740 | 0.756 |
| hit_at_10 | 0.860 | 0.814 | 0.860 | 0.907 |
| ndcg_at_10 | 0.633 | 0.503 | 0.649 | 0.673 |
| precision_at_10 | 0.198 | 0.135 | 0.172 | 0.165 |

## recall_at_5 by query type

| type | n | vector | hybrid | rerank | hybrid_rerank |
|---|---|---|---|---|---|
| acronym | 8 | 0.94 | 1.00 | 1.00 | 1.00 |
| factoid | 6 | 0.58 | 0.17 | 0.83 | 0.83 |
| multihop | 15 | 0.47 | 0.38 | 0.41 | 0.39 |
| paraphrase | 14 | 0.75 | 0.61 | 0.71 | 0.86 |

## hit_at_5 by query type

| type | n | vector | hybrid | rerank | hybrid_rerank |
|---|---|---|---|---|---|
| acronym | 8 | 1.00 | 1.00 | 1.00 | 1.00 |
| factoid | 6 | 0.83 | 0.17 | 1.00 | 1.00 |
| multihop | 15 | 0.80 | 0.60 | 0.67 | 0.67 |
| paraphrase | 14 | 0.79 | 0.64 | 0.71 | 0.86 |

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

- `x-04` (multihop) top1=M300_RTK_User_Manual_EN_v4.0.pdf:7-7 — What jamming or GPS interference methods exist against a drone like the Matrice 300 RTK, and how does that aircraft react if its control link is lost?
- `ac-07` (paraphrase) top1=AC_107-2A.pdf:16-16 — If something goes badly wrong mid-flight, am I allowed to break the flight rules to deal with it, and do I owe the FAA anything afterward?
- `ac-11` (multihop) top1=AC_107-2A.pdf:80-81 — I built my own drone from parts and want to fly it at night over a crowded stadium event. What stands in my way?
- `ac-12` (multihop) top1=M300_RTK_User_Manual_EN_v4.0.pdf:98-98 — I'm launching right at dusk. What do I need on the aircraft and what do I need on me?
- `ac-13` (multihop) top1=AC_107-2A.pdf:61-61 — My 2 lb quadcopter can hit the ground at 28 ft/s. Is that low enough energy to fly over people, and in which category?
- `atp-04` (paraphrase) top1=M300_RTK_User_Manual_EN_v4.0.pdf:26-27 — A small tactical drone is crossing in front of my squad; how far ahead of it should we aim?
- `atp-05` (paraphrase) top1=AC_107-2A.pdf:94-95 — We found a crashed enemy quadcopter; what do we do before touching it?
- `atp-06` (paraphrase) top1=AC_107-2A.pdf:102-102 — What are the fire-permission levels for engaging unknown drones, and which one is the most restrictive?
- `atp-11` (multihop) top1=atp3-01-81.pdf:48-48 — What can a defender exploit to figure out where the person flying a drone is standing?
