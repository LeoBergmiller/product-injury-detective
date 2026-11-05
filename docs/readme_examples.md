# README examples: where a plain `run_sql` agent goes wrong

Verified 2026-11-05. Each "plain" query was run against the **raw 2025 file**
(`data/raw/neiss/neiss2025.tsv`, 410,201 rows), loaded as a table `neiss2025` with
DuckDB `read_csv(..., delim='\t', header=true)`. That is what a plain agent sees. Each
"ours" query was run against `data/processed/injuries.duckdb`. All numbers below are
copied from those runs.

The agent traces for the README still need to be captured once the agent exists.

---

## Example 1: "How many ER visits were there for children under 5 in 2025?"

**Plain `run_sql`** (uses the raw `Age` column as if it were years):

```sql
SELECT COUNT(*) AS n_rows, ROUND(SUM(Weight)) AS est
FROM neiss2025
WHERE Age < 5;
-- 33,344 rows | 856,149 estimated visits
```

A plainer agent that skips the weights reports **33,344**. That is a sample count, not a
number of visits.

**Ours** (the `age_years` rule from `docs/SCHEMA.md` Rule 4):

```sql
SELECT COUNT(*) AS n_cases, ROUND(SUM(weight)) AS est_visits
FROM neiss_cases
WHERE year = 2025 AND age_years < 5;
-- 57,638 cases | 1,469,070 estimated visits
```

**Why the plain answer is wrong: it undercounts by 41.7%** (856,149 vs 1,469,070).
- NEISS codes ages 1-23 months as `201`-`223`, so `Age < 5` drops every child under 2:
  **24,339 cases, or 614,767 estimated visits.**
- It also *includes* `Age = 0`, which means "age not recorded": 45 cases, 1,845
  visits.
- Breakdown of the correct total:

  | Group | Cases | Visits |
  |---|---|---|
  | Ages 2-4 years | 33,299 | 854,303 |
  | Under 2 (coded 201-223) | 24,339 | 614,767 |
  | **Total** | **57,638** | **1,469,070** |

---

## Example 2: "Which products send the most people to the ER in 2025?"

**Plain `run_sql`** (row counts on `Product_1`):

```sql
SELECT Product_1, COUNT(*) AS n_rows
FROM neiss2025
GROUP BY Product_1
ORDER BY n_rows DESC
LIMIT 10;
```

**Ours** (weighted, any of the three product slots, placeholder 5555 excluded):

```sql
SELECT rank, product_code, product_title, n_cases, ROUND(est_visits) AS est_visits
FROM v_top_products
WHERE year = 2025 AND age_group = 'all' AND rank <= 10
ORDER BY rank;
```

There are two separate errors in the plain query:
1. **Rows instead of weights.** Weights range from 7.12 to 113.12 by hospital stratum,
   so one row can stand for 16 times as many visits as another.
2. **`Product_1` only.** The coding manual says "When multiple products are involved,
   it does not matter in what order you enter them", so slot 1 is not "the main
   product". 17.2% of 2025 cases (17.0% of weighted visits) list more than one
   product.

Ranks in 2025 under each method (union of the four top-10s):

| code | product (Jan 2025 manual) | rows, slot 1 (plain) | weighted, slot 1 | rows, any slot | **weighted, any slot (ours)** | est. visits, slot 1 | **est. visits, any slot** |
|---|---|---|---|---|---|---|---|
| 1807 | Floors or flooring materials | 1 | 1 | 1 | **1** | 2,199,379 | **3,079,996** |
| 1842 | Stairs or steps | 2 | 2 | 2 | **2** | 1,228,377 | **1,310,287** |
| 4076 | Beds or bedframes, other or not specified | 3 | 3 | 3 | **3** | 999,656 | **1,133,481** |
| 4074 | Chairs, other or not specified | 4 | 4 | 4 | **4** | 433,183 | **496,221** |
| 5040 | Bicycles or accessories | 5 | 5 | 6 | **5** | 412,756 | **420,962** |
| 1884 | Ceilings and walls | 11 | 11 | 5 | **6** | 305,552 | **419,266** |
| 611 | Bathtubs or showers | 9 | 6 | 10 | **7** | 369,916 | **418,075** |
| 4057 | Tables, not elsewhere classified | 12 | 12 | 8 | **8** | 262,746 | **382,897** |
| 3299 | Exercise (no equipment) | 10 | 7 | 11 | **9** | 361,889 | **382,874** |
| 1205 | Basketball | 6 | 8 | 7 | **10** | 358,220 | **365,706** |
| 464 | Knives, not elsewhere classified | 13 | 9 | 15 | **11** | 342,503 | **361,467** |
| 1211 | Football | 7 | 10 | 9 | **12** | 307,911 | **313,202** |
| 1267 | Soccer | 8 | 14 | 12 | **15** | 260,373 | **264,199** |

**What changes:**
- **The top-10 set differs.** The plain list includes Football (#7) and Soccer (#8).
  Ours drops them to #12 and #15 and brings in Ceilings and walls (#11 -> #6) and
  Tables (#12 -> #8).
- **Floors are undercounted by ~880,618 visits (-28.6%)** when only slot 1 is used
  (2,199,379 vs 3,079,996). In 2025, floors appear in slot 2 or 3 in 23,383 cases.
  The most common slot-1 product in those cases is **beds**: 7,296 cases, 265,971
  visits. Next come chairs (2,146 / 87,636) and sofas (1,749 / 52,468).
- Placeholder code 5555 (unknown substance, poisoning under age 5) is excluded from
  rankings: 60 cases and 1,552 visits in 2025.
- **Caveat for any-slot numbers:** a case counts once for each product it mentions.
  Per-product estimates are correct, but **summing across products double-counts**.

---

## Trend example: not included (yet)

A trend example ("how has X changed 2019-2025?") needs product codes whose
**definition** stayed the same.
- `product_code_stability` shows 710 codes **present** in all seven years. Presence is
  not definitional stability: codes can be split, merged, or redefined while still
  appearing.
- The 2024/2025 comparability tables that would show this are not in `docs/`. Seven
  codes used in 2019-2024 (e.g. 3215, ~254k visits over 2019-2023) are already absent
  from the 2025 manual.
- Per the brief, no trend example is claimed until the comparability tables are parsed.

---

## Reproduce

```bash
uv run python build/build_db.py   # also re-checks 16,485,413 / 1,469,070 / 856,149
```

Plain side: load the raw file in DuckDB with
`CREATE VIEW neiss2025 AS SELECT * FROM read_csv('data/raw/neiss/neiss2025.tsv', delim='\t', header=true);`
and run the plain queries above. Open `data/processed/injuries.duckdb` from the repo
root, or run `SET file_search_path = '<repo root>'` first.
