# Agent design (one page)

**Path:** Text2SQL. **User:** parents, caregivers, and people looking after older
adults who ask "which everyday products send people like mine to the ER, and have any
of them been recalled?"

## Shape

One agent, no sub-agents. It's built on the course starter's framework: the OpenAI
Agents SDK with `LitellmModel("vertex_ai/gemini-3.5-flash-lite")`, authenticated by
gcloud application-default credentials (no API key). `max_turns = 8`.

```
browser ── POST /chat {message, session_id?} ──> app.py (FastAPI)
                                                   │ Runner.run(agent, max_turns=8)
                                                   │ RunHooks.on_tool_end -> tool_calls[]
                                                   ▼
            describe_data · lookup_codes · run_sql (guardrails) · search_recalls
                                                   │ read-only DuckDB
                                                   ▼
                                 data/processed/injuries.duckdb (views over Parquet)
<── {response, session_id, tool_calls[{name,args,result,agent}], table?, chart?, caveats?}
```

Endpoints: `POST /chat`, `POST /clear?session_id=`, `GET /health`, and `GET /`, which
serves `frontend/index.html`. Sessions live in memory, so Cloud Run must run with max
instances = 1.

## Context built offline (`build/build_agent_context.py`)

The script is deterministic and runs after `build_db.py`. It reads `docs/SCHEMA.md`
(through `build_db.parse_schema`) and the built DB, then writes two files:
- `data/processed/agent_context.json`: tables and views with columns and notes, the
  codebooks, the year range, row counts, and the age bands. The `describe_data` and
  `lookup_codes` tools serve it, the way the starter's `read_mappings` and
  `get_enum_values` serve theirs.
- `data/processed/system_prompt.md`: the rules template plus the generated table list.
  The app loads this file and never hand-edits it.

The rules baked into the prompt:
- Use weighted national estimates only, and report `n_cases` next to them.
- Under 5 means `age_years < 5`.
- Count products in any slot, and never sum across products or age groups.
- Exclude the placeholder code 5555 from rankings.
- Flag low-sample estimates (`n_cases < 20`).
- Call recall links "possible matches" unless `audited`.
- SaferProducts reports are voluntary and not representative.

## Tools

| tool | what it does |
|---|---|
| `describe_data(table?)` | Lists tables and views; given a table, its columns and notes |
| `lookup_codes(kind, query?)` | Keyword search of product codes, or codebook values (`diagnosis`, `body_part`, ...) |
| `run_sql(query)` | One SELECT on a read-only connection. 200-row cap, 20 s timeout, errors returned as JSON |
| `search_recalls(keywords, product_code?, since_year?)` | Recalls by the crosswalk (for a NEISS code) or by keywords. Returns cpsc.gov URLs, each marked `possible match` |

**Safety boundary (the connection, not the prompt).**
- DuckDB is opened read-only with `enable_external_access = false`.
- `allowed_directories` is limited to `data/processed`.
- `lock_configuration = true`.
- Statements are parsed with `extract_statements`, and anything other than a single
  SELECT is rejected.

**Guardrails** (`tool_input_guardrail`, like the starter's NA guardrail). Each one returns
an explanation the model can act on.
The checks are in `detective/db.py` (`guardrail_message`), in this order:
1. **No table read.** A query that reads no table or view (numbers typed into the SQL)
   is rejected: numbers must come from the data.
2. **Unweighted counts.** A query that reads `neiss_cases`, `neiss_case_products` or
   `v_case_products` and uses `COUNT` without `weight` (or `est_visits`) is rejected.
3. **Raw age filters.** A filter such as `age_raw < N` is rejected and redirected to
   `age_years`.
4. **Summing the long product table.** `SUM` over `v_case_products` /
   `neiss_case_products` without one `product_code` or a `GROUP BY product_code` is
   rejected, because a case with several products would be counted once per product.
5. **Slot-1 grouping.** A query that groups by `product_1` alone is rejected and
   redirected to any-slot counting.

**Grounding check** (`detective/grounding.py`, run after the answer). Every count,
"N million" and percentage in the answer must match a number in that turn's tool
results (exactly, or within 0.5% when visibly rounded). If any doesn't, the model is
asked once to rewrite the answer with tool calls forbidden (`tool_choice="none"`).
Numbers still unmatched are listed in `caveats`, and the check is logged in
`tool_calls` as `grounding_check`.

## Answers it must refuse or qualify

The prompt and the context state that the data cannot answer:
- **Per-use risk or "most dangerous":** there is no exposure data. Only counts of
  ER visits are possible.
- **Brands or models in NEISS:** NEISS has none. SaferProducts has brands, but its
  reports are voluntary.
- **Causation:** whether a recall reduced injuries, or whether a product caused an
  injury.
- **Anything else:** years outside 2019-2025, states or cities, rates per person, and
  trends for codes that haven't been checked against the comparability tables.

## Output

`response` holds the prose. The `table` (columns and rows) is the `run_sql` result
behind the answer: the latest result that holds the answer's first count (of the
first three it checks), otherwise the last result. The agent sometimes runs an extra
query after the one it answers from, so the last result alone can be the wrong
table. One row per age group in text order (`15-24` before `5-14`) is put in age
order. The `chart` is a spec derived from that table deterministically
(`detective/present.py`). The y column is an estimate-like column (`est_visits`
first, then names containing `est`, `weighted` or `visits`), otherwise the last
numeric column:
- with a `year` column, a line chart: one line per label (up to 6), or one line per
  measure column for a wide table such as `year, bicycles, e_bicycles`;
- otherwise, with 25 rows or fewer, a bar chart over the label column, grouped by
  column for a wide comparison (`est_visits_0_4, est_visits_65plus`) or by a second
  text column with 2-4 values (e.g. `age_group`).
- The `age_group = 'all'` total is left out of the chart when its age groups are
  shown (the table keeps it).

`caveats` is filled in code from the tool results and SQL: low-sample rows, counts not
rates (two or more age groups or years), unchecked code comparability (two or more
years), unaudited recall matches, SaferProducts not being representative, approximate
narrative keyword matches, and any numbers the grounding check could not match. A
caveat the answer already states is not repeated.

## Plain mode (README comparison)

Set `AGENT_MODE=plain` to get a baseline agent:
- the same model and the same `run_sql`;
- a one-line prompt;
- no guardrails and no other tools;
- a single view `neiss` with the **raw file's column names** (`Age`, `Product_1`,
  `Weight`, ...), plus `product_codes` (code and title only) and `recalls`.

`build/run_readme_cases.py` runs both README cases in both modes and writes traces to
`docs/traces/`. `build/baseline_no_tools.py` asks the same model the three sample
questions with no tools and no system prompt.
