# RAG retrieval eval — `regex-v0-hybrid`

golden set `8fd33e8f0b92` · 54 queries (43 answerable) · fetch_k=30 · unlocated rerank slots=0

## Aggregate (answerable queries only)

| metric | vector | hybrid | rerank | hybrid_rerank |
|---|---|---|---|---|
| mrr | 0.594 | 0.457 | 0.631 | 0.616 |
| recall_at_1 | 0.384 | 0.283 | 0.453 | 0.469 |
| hit_at_1 | 0.488 | 0.349 | 0.558 | 0.558 |
| ndcg_at_1 | 0.504 | 0.364 | 0.574 | 0.574 |
| precision_at_1 | 0.488 | 0.349 | 0.558 | 0.558 |
| recall_at_3 | 0.481 | 0.368 | 0.570 | 0.535 |
| hit_at_3 | 0.605 | 0.465 | 0.651 | 0.605 |
| ndcg_at_3 | 0.480 | 0.359 | 0.564 | 0.544 |
| precision_at_3 | 0.341 | 0.209 | 0.364 | 0.349 |
| recall_at_5 | 0.620 | 0.469 | 0.640 | 0.585 |
| hit_at_5 | 0.744 | 0.581 | 0.721 | 0.674 |
| ndcg_at_5 | 0.551 | 0.411 | 0.594 | 0.569 |
| precision_at_5 | 0.251 | 0.191 | 0.270 | 0.251 |
| recall_at_10 | 0.717 | 0.632 | 0.713 | 0.690 |
| hit_at_10 | 0.860 | 0.791 | 0.860 | 0.814 |
| ndcg_at_10 | 0.590 | 0.469 | 0.625 | 0.610 |
| precision_at_10 | 0.188 | 0.144 | 0.188 | 0.170 |

## recall_at_5 by query type

| type | n | vector | hybrid | rerank | hybrid_rerank |
|---|---|---|---|---|---|
| acronym | 8 | 1.00 | 0.88 | 1.00 | 1.00 |
| factoid | 6 | 0.42 | 0.17 | 0.58 | 0.58 |
| multihop | 15 | 0.48 | 0.38 | 0.43 | 0.34 |
| paraphrase | 14 | 0.64 | 0.46 | 0.68 | 0.61 |

## hit_at_5 by query type

| type | n | vector | hybrid | rerank | hybrid_rerank |
|---|---|---|---|---|---|
| acronym | 8 | 1.00 | 0.88 | 1.00 | 1.00 |
| factoid | 6 | 0.50 | 0.17 | 0.67 | 0.67 |
| multihop | 15 | 0.73 | 0.67 | 0.60 | 0.53 |
| paraphrase | 14 | 0.71 | 0.50 | 0.71 | 0.64 |

## Top rerank score: answerable vs unanswerable (for the score cutoff)

| cutoff | answerable kept | unanswerable kept (false answers) |
|---|---|---|
| >= -4 | 29/43 | 8/11 |
| >= -3 | 28/43 | 8/11 |
| >= -2 | 28/43 | 5/11 |
| >= -1 | 26/43 | 5/11 |
| >= 0 | 22/43 | 4/11 |
| >= 1 | 18/43 | 2/11 |
| >= 2 | 14/43 | 2/11 |
| >= 3 | 6/43 | 1/11 |

## Misses (rerank, answerable, no gold page in top 5)

- `x-01` (multihop) top1=M300_RTK_User_Manual_EN_v4.0.pdf:26-27 — A Matrice 300 RTK is hovering outside our fence. Could a handheld jammer reach it, and how far can the aircraft stay linked to its operator?
- `x-04` (multihop) top1=M300_RTK_User_Manual_EN_v4.0.pdf:98-100 — What jamming or GPS interference methods exist against a drone like the Matrice 300 RTK, and how does that aircraft react if its control link is lost?
- `ac-07` (paraphrase) top1=AC_107-2A.pdf:88-88 — If something goes badly wrong mid-flight, am I allowed to break the flight rules to deal with it, and do I owe the FAA anything afterward?
- `ac-12` (multihop) top1=AC_107-2A.pdf:88-89 — I'm launching right at dusk. What do I need on the aircraft and what do I need on me?
- `ac-14` (multihop) top1=AC_107-2A.pdf:77-78 — Can a Category 2 drone be flown over people after dark, and what do I need to do about lighting?
- `atp-05` (paraphrase) top1=AC_107-2A.pdf:28-28 — We found a crashed enemy quadcopter; what do we do before touching it?
- `atp-06` (paraphrase) top1=atp3-01-81.pdf:48-49 — What are the fire-permission levels for engaging unknown drones, and which one is the most restrictive?
- `atp-11` (multihop) top1=AC_107-2A.pdf:46-46 — What can a defender exploit to figure out where the person flying a drone is standing?
- `m300-01` (paraphrase) top1=M300_RTK_User_Manual_EN_v4.0.pdf:34-36 — How long can the Matrice 300 RTK stay airborne with no payload before the batteries run out?
- `m300-02` (factoid) top1=M300_RTK_User_Manual_EN_v4.0.pdf:68-69 — What wind speed is too strong to fly the Matrice 300 RTK safely?
- `m300-05` (factoid) top1=M300_RTK_User_Manual_EN_v4.0.pdf:50-51 — How far away can the operator still receive a live video feed from the Matrice 300 RTK?
- `m300-11` (multihop) top1=M300_RTK_User_Manual_EN_v4.0.pdf:108-111 — With the stock batteries installed, roughly how much payload can we add to the Matrice 300 RTK and still operate at 5,000 m above sea level?
