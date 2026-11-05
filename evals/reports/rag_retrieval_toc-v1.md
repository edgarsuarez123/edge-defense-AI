# RAG retrieval eval — `toc-v1`

golden set `8fd33e8f0b92` · 54 queries (43 answerable) · fetch_k=30 · unlocated rerank slots=0

## Aggregate (answerable queries only)

| metric | vector | hybrid | rerank | hybrid_rerank |
|---|---|---|---|---|
| mrr | 0.630 | 0.492 | 0.637 | 0.636 |
| recall_at_1 | 0.380 | 0.298 | 0.411 | 0.411 |
| hit_at_1 | 0.488 | 0.372 | 0.512 | 0.512 |
| ndcg_at_1 | 0.496 | 0.388 | 0.543 | 0.543 |
| precision_at_1 | 0.488 | 0.372 | 0.512 | 0.512 |
| recall_at_3 | 0.581 | 0.434 | 0.616 | 0.620 |
| hit_at_3 | 0.744 | 0.535 | 0.721 | 0.721 |
| ndcg_at_3 | 0.554 | 0.420 | 0.593 | 0.589 |
| precision_at_3 | 0.349 | 0.240 | 0.341 | 0.326 |
| recall_at_5 | 0.663 | 0.539 | 0.671 | 0.663 |
| hit_at_5 | 0.837 | 0.628 | 0.791 | 0.791 |
| ndcg_at_5 | 0.592 | 0.464 | 0.619 | 0.608 |
| precision_at_5 | 0.293 | 0.181 | 0.237 | 0.219 |
| recall_at_10 | 0.775 | 0.643 | 0.721 | 0.736 |
| hit_at_10 | 0.860 | 0.814 | 0.884 | 0.884 |
| ndcg_at_10 | 0.633 | 0.503 | 0.640 | 0.640 |
| precision_at_10 | 0.198 | 0.135 | 0.158 | 0.149 |

## recall_at_5 by query type

| type | n | vector | hybrid | rerank | hybrid_rerank |
|---|---|---|---|---|---|
| acronym | 8 | 0.94 | 1.00 | 1.00 | 1.00 |
| factoid | 6 | 0.58 | 0.17 | 0.67 | 0.58 |
| multihop | 15 | 0.47 | 0.38 | 0.46 | 0.40 |
| paraphrase | 14 | 0.75 | 0.61 | 0.71 | 0.79 |

## hit_at_5 by query type

| type | n | vector | hybrid | rerank | hybrid_rerank |
|---|---|---|---|---|---|
| acronym | 8 | 1.00 | 1.00 | 1.00 | 1.00 |
| factoid | 6 | 0.83 | 0.17 | 0.83 | 0.83 |
| multihop | 15 | 0.80 | 0.60 | 0.67 | 0.60 |
| paraphrase | 14 | 0.79 | 0.64 | 0.79 | 0.86 |

## Top rerank score: answerable vs unanswerable (for the score cutoff)

| cutoff | answerable kept | unanswerable kept (false answers) |
|---|---|---|
| >= -4 | 29/43 | 8/11 |
| >= -3 | 29/43 | 7/11 |
| >= -2 | 26/43 | 7/11 |
| >= -1 | 25/43 | 5/11 |
| >= 0 | 20/43 | 4/11 |
| >= 1 | 15/43 | 4/11 |
| >= 2 | 10/43 | 3/11 |
| >= 3 | 7/43 | 2/11 |

## Misses (rerank, answerable, no gold page in top 5)

- `x-01` (multihop) top1=M300_RTK_User_Manual_EN_v4.0.pdf:26-27 — A Matrice 300 RTK is hovering outside our fence. Could a handheld jammer reach it, and how far can the aircraft stay linked to its operator?
- `x-04` (multihop) top1=M300_RTK_User_Manual_EN_v4.0.pdf:15-16 — What jamming or GPS interference methods exist against a drone like the Matrice 300 RTK, and how does that aircraft react if its control link is lost?
- `ac-11` (multihop) top1=AC_107-2A.pdf:80-81 — I built my own drone from parts and want to fly it at night over a crowded stadium event. What stands in my way?
- `atp-05` (paraphrase) top1=atp3-01-81.pdf:39-39 — We found a crashed enemy quadcopter; what do we do before touching it?
- `atp-06` (paraphrase) top1=AC_107-2A.pdf:45-46 — What are the fire-permission levels for engaging unknown drones, and which one is the most restrictive?
- `atp-11` (multihop) top1=AC_107-2A.pdf:46-46 — What can a defender exploit to figure out where the person flying a drone is standing?
- `m300-02` (factoid) top1=M300_RTK_User_Manual_EN_v4.0.pdf:27-28 — What wind speed is too strong to fly the Matrice 300 RTK safely?
- `m300-03` (paraphrase) top1=M300_RTK_User_Manual_EN_v4.0.pdf:26-27 — Can the Matrice 300 RTK fly in the rain, and under what conditions does its water protection stop applying?
- `m300-11` (multihop) top1=M300_RTK_User_Manual_EN_v4.0.pdf:108-110 — With the stock batteries installed, roughly how much payload can we add to the Matrice 300 RTK and still operate at 5,000 m above sea level?
