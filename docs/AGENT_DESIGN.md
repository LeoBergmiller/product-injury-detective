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
1. **Unweighted counts.** A query that reads `neiss_cases`, `neiss_case_products` or
   `v_case_products` and uses `COUNT` without `weight` is rejected.
2. **Raw age filters.** A filter such as `age_raw < N` is rejected and redirected to
   `age_years`.
3. **Slot-1 grouping.** A query that groups by `product_1` alone is rejected and
   redirected to any-slot counting.

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

`response` holds the prose. The `table` (columns and rows) is the last successful
`run_sql` result. The `chart` is a spec derived from that table deterministically:
- a line chart if there is a `year` column;
- otherwise a bar chart over the first label column and the first estimate column,
  when there are 25 rows or fewer.

`caveats` is filled from the tool results: low-sample rows, unaudited recall matches,
and SaferProducts not being representative.

## Plain mode (README comparison)

Set `AGENT_MODE=plain` to get a baseline agent:
- the same model and the same `run_sql`;
- a one-line prompt;
- no guardrails and no other tools;
- a single view `neiss` with the **raw file's column names** (`Age`, `Product_1`,
  `Weight`, ...), plus `recalls`.

`build/run_readme_cases.py` runs both README cases in both modes and writes traces to
`docs/traces/`. `build/baseline_no_tools.py` asks the same model the three sample
questions with no tools and no system prompt.
