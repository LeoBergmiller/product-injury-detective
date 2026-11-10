# Product-Injury Detective

A Text2SQL agent over U.S. Consumer Product Safety Commission (CPSC) data. It answers:
**"For a given age group, which everyday products send people to the emergency room,
and have any of them been recalled?"**

- **Who it's for:** parents, caregivers, and people looking after older adults. For
  example, compare children under 5 with adults 65+, find the products behind the
  visits, and see possible recalls.
- **What it is not:** a risk calculator. It counts estimated injury-related ER visits;
  it does not know how often products are used, so it can't say what is "most
  dangerous" (see [What the data can't answer](#what-the-data-cant-answer)).
- **Path:** Text2SQL (`submission.json`).
- **Team:** avr2141, lab2309, rl3575. Three sources, one per teammate: NEISS, CPSC
  Recalls, SaferProducts.gov.
- **Deployed:** https://REPLACE-WITH-CLOUD-RUN-URL.run.app (settings in
  [`docs/DEPLOY.md`](docs/DEPLOY.md)).

## How it works

```
browser -> POST /chat -> one agent (OpenAI Agents SDK + LitellmModel -> Vertex Gemini)
            tools: describe_data | lookup_codes | run_sql (+ guardrails) | search_recalls
            -> read-only DuckDB (data/processed/injuries.duckdb: views over Parquet)
<- {response, session_id, tool_calls[{name, args, result, agent}], table, chart, caveats}
```

- **One agent, no sub-agents.** It uses `vertex_ai/gemini-3.5-flash-lite` (the
  course starter's model) through gcloud application-default credentials. There is no
  API key. Each turn is capped at 8 steps.
- **Offline build** (`build/`): it unions the yearly NEISS files and applies the
  sample weights. It also builds:
  - a product-code table from the coding manual;
  - snapshots of recalls and SaferProducts reports;
  - name crosswalks;
  - a `data_dictionary` table and views that encode the rules (weighted estimates,
    any-slot product counting, the age coding, and excluding the 5555 placeholder).
- **Generated context.** `build/build_agent_context.py` writes the system prompt and a
  context file deterministically, from [`docs/SCHEMA.md`](docs/SCHEMA.md) and the
  database. The prompt's example SQL is executed during that build.
- **Safety and correctness in code, not just the prompt.**
  - The connection is DuckDB read-only. File access outside `data/processed` is
    blocked, and the settings are locked.
  - `run_sql` accepts exactly one SELECT, with a 200-row cap and a 20 s timeout.
- **Guardrails** reject SQL that would give a wrong number, with a message the model
  can act on:
  - SQL that reads no table (numbers typed into the query);
  - unweighted `COUNT(*)` over NEISS rows;
  - range filters on raw `age_raw`;
  - grouping by `product_1` alone;
  - `SUM(weight)` across products in the long product table (double counting);
  - summing several products' rows of `v_product_estimates` / `v_top_products`.
- **Grounding check.**
  - After each answer, every count (including bare numbers like 257), million and
    percentage must match a value in that turn's tool results.
  - If one doesn't, the agent is asked to run the queries that return them (it
    sometimes answers from memory), and if numbers are still unmatched, to rewrite
    the answer with tool calls forbidden.
  - Anything still unmatched is listed in `caveats`.
- **Recalls are "possible match (not reviewed)".** `search_recalls` keeps a recall only
  if the recalled product's head noun is the NEISS product. For example, it drops
  "Floor Lamps" for flooring and "LED Modules used with Floating Stairs" for stairs.
  Otherwise it says "no reviewed recall link found".
- **The page** shows the answer, caveats, a table, and a chart (one chart with both
  groups side by side for comparisons). "How I found this" lists every tool call,
  including guardrail rejections. The design is in
  [`docs/AGENT_DESIGN.md`](docs/AGENT_DESIGN.md).

## Data: where it came from and how to rebuild it

| Source | Owner | What | How we got it | Table(s) |
|---|---|---|---|---|
| **NEISS** (CPSC National Electronic Injury Surveillance System) | lab2309 | ER cases 2019-2025, a probability sample of ~100 hospitals with a national weight per case. 2,442,005 cases | Yearly TSVs from cpsc.gov/Research--Statistics/NEISS-Injury-Data, plus the data dictionary and the January 2025 coding manual | `neiss_cases` (one Parquet per year), `neiss_case_products`, `neiss_codebook`, `product_codes`, `product_code_stability` |
| **CPSC Recalls API** | rl3575 | 10,082 recalls, 1973-06-08 to 2026-11-05 | `saferproducts.gov/RestWebServices/Recall`, one request per year (no key) | `recalls`, `recall_products` |
| **SaferProducts.gov** (OData API) | avr2141 | 71,858 voluntary incident reports, 2011-03 to 2026-10 | Full paged pull with an application key (key only in `.env`, never committed) | `safer_incidents`, `safer_codebook`, `safer_category_xwalk` |

- **Reuse terms.** None of the CPSC pages we checked states a license; see
  [`docs/DATA_NOTES.md`](docs/DATA_NOTES.md). Recall-to-product links come from text
  matching and are not reviewed (`product_recall_xwalk.audited = false`); a 60-row
  spot check is under [Known limitations](#known-limitations).
- **Repaired records.** Six NEISS records were split by stray line breaks inside the
  narrative. They are documented in `docs/DATA_NOTES.md`, and `build/build_neiss.py`
  logs each fix to `data/processed/tmp/neiss_repairs.json` on every build. That
  folder is gitignored, so the log is not committed. The list below was re-checked
  against the raw files on 2026-11-08:
  - `neiss2019.tsv`: case 190746091 (CR LF inside the narrative, joined with a
    space).
  - `neiss2021.tsv`: cases 210332827 and 210760065 (same fix).
  - `neiss2023.tsv`: case 230975940 (same fix), and cases 240131872 and 240152467
    (lone LF replaced by a space). These two have 24- case numbers but are in the
    2023 file, with treatment dates 2023-12-19 and 2023-12-31.
  - 2020, 2022, 2024, and 2025 needed no repair.
  - After repair, every validation passes: unique case ids, valid dates and ages,
    weights > 0, and every code in the codebook.
- **Rebuild.** Raw files go in `data/raw/` (gitignored). The SaferProducts key goes in
  `.env` as `SAFERPRODUCTS_KEY` and is only needed to re-fetch. The fetch scripts run
  only when refreshing the raw pulls:

  ```bash
  uv sync
  uv run python build/fetch_recalls.py
  uv run python build/fetch_saferproducts_incidents.py
  uv run python build/build_product_codes.py
  uv run python build/build_neiss.py
  uv run python build/build_saferproducts.py
  uv run python build/build_recalls.py
  uv run python build/build_xwalks.py
  uv run python build/build_db.py
  uv run python build/build_agent_context.py
  ```

  `build_db.py` validates every Parquet against `docs/SCHEMA.md`, refuses any file over
  95 MB, and re-checks known numbers. Examples are the 2025 total of 16,485,413
  estimated injury-related ER visits and 1,469,070 for under 5. The built data
  (`data/processed/`, 144 MB, largest file 22 MB) is committed and ships with the
  deploy.

## Run it

```bash
gcloud auth application-default login     # Vertex AI (Gemini), as in the course setup
uv sync
uv run app.py                             # http://localhost:8000
uv run python tests/test_offline.py       # no model calls
uv run python tests/test_questions.py     # 13 live questions (uses Vertex AI)
```

## Three sample queries

Expected answers come from SQL on `data/processed/injuries.duckdb`. "No tools" is the
same model (`gemini-3.5-flash-lite`) with no tools and no system prompt
(`build/baseline_no_tools.py`, full text in
[`docs/traces/baseline_no_tools.md`](docs/traces/baseline_no_tools.md)).

### 1. "How many ER visits were there for children under 5 in 2025?"

- **Expected:** **1,469,070** estimated injury-related ER visits (57,638 sample
  cases).
  - Source: `neiss_cases` with `year = 2025 AND age_years < 5`.
  - NEISS codes infants as `201`-`223` (months), so the raw filter `Age < 5` gives
    only 856,149.
- **Agent** (final test run,
  [`docs/traces/questions_detective.json`](docs/traces/questions_detective.json)):
  > There were an estimated 1,469,070 injury-related ER visits (from a sample of
  > 57,638 cases) for children under 5 in 2025.
- **No tools, no system prompt:**
  > I do not have access to real-time data or statistics for the year 2025, as my
  > knowledge is current up to January 2025 ... please check reports from the CDC,
  > NCHS, or HCUP.

### 2. "Which products send the most adults 65+ to the ER in 2025, and have any been recalled?"

- **Expected** (source: `v_top_products`, `year = 2025`, `age_group = '65+'`):

  | Rank | Product | Estimated injury-related ER visits | Sample cases |
  |---|---|---|---|
  | 1 | Floors or flooring materials | 1,768,793 | 35,318 |
  | 2 | Beds or bedframes | 591,562 | 12,639 |
  | 3 | Stairs or steps | 423,006 | 8,865 |
  | 4 | Chairs | 257,725 | 5,366 |
  | 5 | Bathtubs or showers | 163,420 | 3,422 |

- **Recalls** (`search_recalls` over `recalls` and `recall_products`):
  - Floors and stairs: "no reviewed recall link found".
  - Beds: possible matches (not reviewed), e.g. the Merax Murphy Beds recall
    (2026-08-13) and the Dorel Wall Beds recall (2024-11-21).
- **Agent:**
  > In 2025, the everyday products and home structures associated with the highest
  > estimated injury-related ER visits for adults aged 65 and older are floors or
  > flooring materials (about 1.77 million estimated visits from 35,318 sample cases),
  > followed by beds or bedframes (about 592,000 ...), and stairs or steps (about
  > 423,000 ...). ... Regarding recalls, text-based searches identified possible
  > matches (not reviewed by a person) for beds or bedframes, such as recalls for
  > Murphy beds, platform beds, and upholstered beds ... (recall ID 10922, cpsc.gov
  > link). No reviewed recall links were found in the database for floors or stairs.
  > Recalls are provided as context and do not prove that a recalled product caused
  > any specific injury.
- **No tools, no system prompt:**
  > I do not have access to real-time data, specific statistics for the year 2025, or
  > live consumer product safety databases ... I recommend checking ... SaferProducts.gov
  > or CPSC.gov.

### 3. "How do injuries on stairs compare for children under 5 and adults 65+ in 2025?"

- **Expected:** stairs or steps (code 1842) in 2025 (source: `v_product_estimates`):
  - Adults 65+: **423,006** estimated injury-related ER visits (8,865 cases).
  - Under 5: **81,016** (3,701 cases).
  - These are counts, not rates. The page shows both groups in one table and chart.
- **Agent:**
  > In 2025, for stairs or steps (excluding pull-down and folding stairs, product code
  > 1842), children under 5 accounted for an estimated 81,016 injury-related ER visits
  > (from 3,701 sample cases). Adults aged 65 and older accounted for an estimated
  > 423,006 injury-related ER visits (from 8,865 sample cases). ... These are counts of
  > injury-related ER visits, not rates: the groups differ in size, and population
  > denominators are not in the data.
- **No tools, no system prompt:**
  > I do not have access to real-time data or specific public health statistics for
  > the year 2025 ... However, looking at historical trends ... (general,
  > unsourced description of fall patterns, with no numbers from the data).


## Two questions a plain `run_sql` agent gets wrong

"Plain" means the same model and the same `run_sql`, with no guardrails, a one-line
prompt, and the raw NEISS columns (`Age`, `Product_1`, `Weight`, ...) plus product
titles (`AGENT_MODE=plain`). Each case isolates one cause; the SQL-level comparison is
in [`docs/readme_examples.md`](docs/readme_examples.md).
- We ran each case **5 times in plain mode and 3 times in detective mode** and kept
  every run. Outcomes are classified automatically by which known number the answer
  contains.
- All traces: [`docs/traces/readme_cases.md`](docs/traces/readme_cases.md), and one JSON
  file per run in `docs/traces/`.

### Case 1: the age filter. "How many ER visits were there for children under 5 in 2025?"

- **Correct:** 1,469,070 estimated injury-related ER visits (`age_years < 5`,
  weighted).
- **Observed:**

  | Mode | Runs | Outcome |
  |---|---|---|
  | plain | 4 of 5 | **33,344**: raw `Age < 5` **and** unweighted `COUNT(*)` (two errors) |
  | plain | 1 of 5 | **856,149**: raw `Age < 5`, weighted (the isolated age error) |
  | detective | **3 of 3** | **1,469,070** |

- **Plain trace excerpt (run 2, `docs/traces/under5_age_filter.plain.run2.json`):**

  ```sql
  SELECT count(*) AS er_visits_under_5_2025 FROM neiss
  WHERE age < 5 AND EXTRACT(YEAR FROM treatment_date) = 2025;
  -- 33344 -> "There were 33,344 emergency room visits recorded in the NEISS database
  --           for children under 5 years old in 2025."
  ```

- **Detective trace excerpt:**

  ```sql
  SELECT COUNT(*) AS n_cases, ROUND(SUM(weight)) AS est_visits
  FROM neiss_cases WHERE year = 2025 AND age_years < 5
  -- 57,638 | 1,469,070
  ```

- **Why plain is wrong:** NEISS codes ages 1-23 months as `201`-`223`, so `Age < 5`
  drops every child under 2 (614,767 estimated visits) and includes `Age = 0`
  ("not recorded"). Most plain runs also skipped the weights, so this case usually
  shows both errors together; only 1 of 5 runs isolated the age error.

### Case 2: slot-1-only counting. "Which products send the most people to the ER in 2025?"

- **Correct:** weighted, any of the three product slots: floors **3,079,996**, stairs
  1,310,287, beds 1,133,481.
- **Observed:**

  | Mode | Runs | Outcome |
  |---|---|---|
  | plain | **5 of 5** | weighted but `Product_1` only: floors **2,199,379** |
  | detective | **3 of 3** | any slot, weighted: floors **3,079,996** |

- **This case cleanly isolates one cause.** Every plain run used the weights, and all
  of them counted only the first product slot.
  - The coding manual says product order "does not matter", and 17.2% of 2025 cases
    list more than one product.
  - So slot-1 counting undercounts floors by **28.6%**.
  - It also changes the top 10: Knives (#9) and Football (#10) replace Ceilings and
    walls (our #6) and Tables (our #8).
- **Plain trace excerpt:**

  ```sql
  -- plain run 2, third attempt (the first two hit binder errors)
  SELECT p.product_title AS product_name, t.Product_1 AS product_code,
         COUNT(*) AS sample_count, ROUND(SUM(t.Weight), 1) AS estimated_injuries
  FROM neiss t JOIN product_codes p ON t.Product_1 = p.product_code
  WHERE EXTRACT(YEAR FROM t.Treatment_Date) = 2025
  GROUP BY t.Product_1, p.product_title ORDER BY estimated_injuries DESC LIMIT 15;
  -- "Floors or flooring materials (Code 1807): ~2,199,378 estimated injuries"
  ```

- **Detective trace excerpt:**

  ```sql
  -- detective run 1
  SELECT rank, product_code, product_title, n_cases, ROUND(est_visits) AS est_visits,
         low_sample
  FROM v_top_products WHERE year = 2025 AND age_group = 'all' AND rank <= 10
  ORDER BY rank;
  -- 1 | 1807 | Floors or flooring materials | 70650 | 3079996
  ```


## What the data can't answer

The agent says so in its first sentence, then offers what the data can show:
- **Risk per use, per hour, or per owner, and "most dangerous" products.** There is
  no exposure data, only counts of injury-related ER visits.
- **Which age group or year is "riskier", and rates per person.** Comparisons are
  counts, not rates: the groups differ in size, and population denominators are not
  in the data.
- **Brands and models.** NEISS records product types only. SaferProducts reports name
  brands, but they are voluntary and not representative, so brands are never ranked
  by danger.
- **Causation.** For example, whether a recall reduced injuries, or whether a product
  caused one.
- **Places and years.** No states, cities or hospitals, and no years outside
  2019-2025.

## Known limitations

- **Product codes across years have not been checked against CPSC's comparability
  tables.** The 2024/2025 tables are in `docs/` but not parsed. Every multi-year
  answer carries this caveat. Seven retired codes used in 2019-2024 have no title,
  and are labelled "title unknown" instead of guessed.
- **The recall links are unreviewed.** Head-noun matching removes obvious mismatches
  but can still keep weak ones. For example, "Portable Camping Showers" counts as a
  shower recall for "Bathtubs or showers".
  - A spot check of 60 crosswalk candidates (20 per confidence level) found that
    16/20 high, 5/20 medium and 1/20 low candidates were the right product type.
  - The agent's runtime filter (`search_recalls` with a product code) kept 12 of
    the 60, and 11 of those 12 were right. It also dropped 11 of the 22 right ones,
    so a "no reviewed recall link found" answer can miss real recalls. Labels and
    script: [`docs/audit/recall_xwalk_sample.json`](docs/audit/recall_xwalk_sample.json),
    `build/audit_recall_sample.py`.
- **The low-sample flag is a project rule.** It is `n_cases < 20`; CPSC's official
  stability rule is not stated in the documents we have.
- **Keyword searches of the case narratives are approximate.**
- **The model's answers vary between runs** (see the observed rates above).
  - The last live run (2026-11-09) passed **13/13** checks (`tests/test_questions.py`).
    The two runs before it scored 12/13; both missed the per-hour risk question,
    giving visit counts without saying the data has no exposure measure. A saved
    13/13 run is in
    [`docs/traces/questions_detective.json`](docs/traces/questions_detective.json).
    Earlier runs scored 7/12, 11/12, 12/12, 8/13, 9/13, 11/13, 13/13, and 10/13.
  - The checks themselves are partly lenient regexes.
- **The grounding check tests that numbers exist, not that they're attributed
  correctly.** It catches invented numbers. It can't catch a real number attached
  to the wrong label; we saw one such error ("3,701 cases" given for a single
  diagnosis) before adding the rule that rejects SQL typing literal numbers.
- **"Injury-related ER visits" wording is enforced in code.** Any plain "ER visits" in
  an answer is rewritten to it, so the wording test passes partly by construction.
- **The counts-not-rates sentence can be over-applied.** The agent sometimes adds it
  to a single-group answer.
- **Sessions are kept in memory**, so the Cloud Run service must run with max
  instances = 1.
