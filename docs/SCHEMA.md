# Data contract: offline build

This is the interface between the source builders and the final database. Each source is
built **independently** into its own Parquet file in `data/processed/`.
`build/build_db.py` assembles them into `data/processed/injuries.duckdb`, adds the views,
and fails if any table doesn't match this file.

- **Single source of truth.** `build_db.py` parses the column tables below to validate
  every Parquet file (names, types, order) and to fill the `data_dictionary` table.
  Keep the table format exactly `| column | type | null | notes |`.
- **Paths.**
  - `data/raw/`: downloads, gitignored, never shipped.
  - `data/processed/`: tracked outputs. Each file must be < 95 MB; `build_db.py`
    enforces this. A file that can't meet it goes to GCS (not planned yet).
- **Config.** All paths and parameters live in `build/config.yaml`. No hardcoded paths
  and no keys in code.
- **Evidence.** Facts behind these rules are in `docs/DATA_NOTES.md` (2026-11-05).

## Builders

| Parquet in `data/processed/` | Script | Owner | Inputs |
|---|---|---|---|
| `neiss_cases`, `neiss_case_products`, `neiss_codebook` | `build/build_neiss.py` | Leo | NEISS yearly TSVs, `docs/DataDictionary042022.xlsx` |
| `product_codes` | `build/build_product_codes.py` (done) | Leo | Coding manual, Appendix H |
| `recalls`, `recall_products` | `build/build_recalls.py` | **TBD** | Recalls API (one full pull) |
| `safer_incidents`, `safer_codebook` | `build/build_saferproducts.py` | **TBD** | SaferProducts OData (`$skiptoken` paging, ≤5 req/s) |
| `safer_category_xwalk`, `product_recall_xwalk` | `build/build_xwalks.py` | **TBD** | the above + `build/seeds/product_recall_xwalk.csv` |
| `injuries.duckdb` (all tables + views) | `build/build_db.py` | Leo | all Parquet above |

Owners marked TBD are for the team to assign (arapinan, rainliu19).

## Global rules

1. **Weights.** Every national number is `SUM(weight)` over NEISS cases. `COUNT(*)` is
   the *sample* size, never an estimate.
   - Every estimate output also carries `n_cases`.
   - If `n_cases < min_cases` (config, default 20), the row gets `low_sample = true`.
     This threshold is a **project rule**. CPSC's official stability rule is
     unverified; the coding manual doesn't state one.
2. **Products are counted in any slot.** The manual says product order "does not
   matter", so `product_1` is not "the main product".
   - A case counts once for **each distinct product code** in `product_1..3`.
   - So sums across products exceed total visits. Never add product estimates together.
3. **Codes vs. "unknown".**
   - Coded NEISS fields keep the raw integer. Labels and the unknown flag come from
     `neiss_codebook`.
   - `0` does **not** always mean unknown. For example, `body_part = 0` is "Internal".
   - `alcohol`, `drug`, and `fire_involvement = 0` mean "no OR not recorded". Never
     report them as a confirmed "no".
4. **Age (NEISS).** `age_raw` keeps the file value.

   | `age_raw` | meaning | `age_months` | `age_years` | `age_group` |
   |---|---|---|---|---|
   | 0 | not recorded | NULL | NULL | `unknown` |
   | 201-223 | 1-23 months | `age_raw - 200` | `(age_raw - 200) // 12` (0 or 1) | `0-4` |
   | 2-120 | years | NULL | `age_raw` | band |

   - Bands: `0-4, 5-14, 15-24, 25-44, 45-64, 65+, unknown`. These are project bands.
   - **"Under 5" = `age_years < 5`**, which automatically excludes unknown age. Never
     filter on `age_raw < 5`: that drops infants coded 201-223 and adds the
     unknown-age rows (see `docs/readme_examples.md`).
   - Any `age_raw` outside {0, 2-120, 201-223} fails the build. (1 and 200 never occur
     in 2025.)
5. **Code 5555 is a placeholder**: an unknown or uncoded substance in a poisoning, under
   age 5. `product_codes.is_placeholder = true`.
   - It is **excluded** from `v_top_products`.
   - It stays in `v_product_estimates`, labelled by its title, so totals are complete.
   - `is_catch_all` titles ("other / not specified / n.e.c.") are labelled, not
     excluded.
6. **No shared product IDs across sources.**
   - NEISS codes and SaferProducts `ProductCategoryId` are different numbering schemes.
     They overlap numerically by coincidence (e.g. 1807 = floors vs. SP "Drywall
     Control").
   - Recalls API `Products[].CategoryID` (and `Type`, `HazardTypeID`) were **empty in
     119/119 recalls** checked (Oct 2024 and Q1 2025).
   - So: incidents link to NEISS **by exact product name**, and recalls link **by text
     search only**. **Never join on numbers across sources.**
7. **Claims.** NEISS is a probability sample, so use weights. SaferProducts reports are
   voluntary and **not representative**. Recalls are descriptive context, **not
   evidence of cause**.

## NEISS tables

### neiss_cases
One row per ED case. Grain: `case_id`. Years: set in `config.yaml` (sizing in item 5).

| column | type | null | notes |
|---|---|---|---|
| case_id | BIGINT | no | PK. `CPSC_Case_Number` |
| treatment_date | DATE | no | `Treatment_Date` (`MM/DD/YYYY`) |
| year | SMALLINT | no | `year(treatment_date)` |
| age_raw | SMALLINT | no | `Age` as coded |
| age_months | SMALLINT | yes | Rule 4 |
| age_years | SMALLINT | yes | Rule 4. NULL means unknown |
| age_group | VARCHAR | no | Rule 4 |
| sex | SMALLINT | no | Codebook. 0 = not recorded; 3 only from 2021 |
| race | SMALLINT | no | Codebook. 0 = not stated |
| race_other | VARCHAR | yes | Blank -> NULL |
| hispanic | SMALLINT | yes | 2019+. 0 = unknown |
| body_part | SMALLINT | no | Codebook. 0 = Internal (a real value), 87 = not stated |
| body_part_2 | SMALLINT | yes | 2019+. Blank -> NULL |
| diagnosis | SMALLINT | no | Codebook. 71 = other/not stated |
| diagnosis_other | VARCHAR | yes | Blank -> NULL |
| diagnosis_2 | SMALLINT | yes | 2019+. Blank -> NULL |
| diagnosis_2_other | VARCHAR | yes | Blank -> NULL |
| disposition | SMALLINT | no | Codebook. 8 = fatality, 9 = unknown |
| location | SMALLINT | no | Codebook. 0 = not recorded (36% in 2025) |
| fire_involvement | SMALLINT | no | Rule 3 |
| product_1 | INTEGER | no | Never 0 |
| product_2 | INTEGER | yes | 0 -> NULL |
| product_3 | INTEGER | yes | 2019+. 0 -> NULL |
| alcohol | SMALLINT | yes | 2019+. Rule 3 |
| drug | SMALLINT | yes | 2019+. Rule 3 |
| narrative | VARCHAR | no | `Narrative_1` (the dictionary calls it `Narrative`) |
| narrative_era | VARCHAR | no | `from_2019_400` if treatment_date >= 2019-01-01, else `pre_2019_142` |
| stratum | VARCHAR | no | C, L, M, S, V |
| psu | SMALLINT | no | Hospital ID |
| weight | DOUBLE | no | `Weight`. Rule 1 |
| source_file | VARCHAR | no | e.g. `neiss2025.tsv` |

### neiss_case_products
Long form of the three product slots. Grain: (`case_id`, `product_code`).

| column | type | null | notes |
|---|---|---|---|
| case_id | BIGINT | no | FK `neiss_cases` |
| slot | TINYINT | no | 1-3, the first slot in which the code appears |
| product_code | INTEGER | no | FK `product_codes`. Zeros dropped; deduplicated per case |

### neiss_codebook
From the data dictionary, sheet "2019 and beyond". Grain: (`variable`, `code`).

| column | type | null | notes |
|---|---|---|---|
| variable | VARCHAR | no | `neiss_cases` column name, e.g. `body_part` |
| code | SMALLINT | no | |
| label | VARCHAR | no | Dictionary text |
| is_unknown | BOOLEAN | no | True only for pure unknown/not-recorded codes |

### product_codes
From the January 2025 coding manual, Appendix H. 806 codes; covers every 2025 code.

| column | type | null | notes |
|---|---|---|---|
| product_code | INTEGER | no | PK |
| product_title | VARCHAR | no | Whitespace-normalized |
| is_placeholder | BOOLEAN | no | True for 5555 (Rule 5) |
| is_catch_all | BOOLEAN | no | Title matches other / not specified / not elsewhere classified |
| source_page | INTEGER | no | PDF page |
| source | VARCHAR | no | Manual edition and appendix |

Codes retired before 2025 have no title until the comparability tables are added. The
build lists any `neiss_case_products.product_code` that has no title and fails if the
list is non-empty.

## Recalls tables (Recalls API)

### recalls
Grain: `recall_id`. A response containing `RecallID = 0` or a `Title` starting with
"Error retrieving" is the API's error pseudo-recall, and the **build fails**.

| column | type | null | notes |
|---|---|---|---|
| recall_id | INTEGER | no | PK. `RecallID` |
| recall_number | VARCHAR | no | `RecallNumber` |
| recall_date | DATE | no | `RecallDate` |
| last_publish_date | DATE | yes | `LastPublishDate` |
| title | VARCHAR | no | `Title` |
| description | VARCHAR | yes | `Description` |
| url | VARCHAR | yes | `URL` |
| hazards_text | VARCHAR | yes | `Hazards[].Name` joined with ` \| ` |
| injuries_text | VARCHAR | yes | `Injuries[].Name` joined. Free text, no counts |
| remedies_text | VARCHAR | yes | `Remedies[].Name` joined |
| companies_text | VARCHAR | yes | Manufacturer/retailer/importer/distributor `Name`s joined |
| search_text | VARCHAR | no | Lowercased title + product names + hazards + description, for search |

### recall_products
Grain: (`recall_id`, `product_seq`). `CategoryID` and `Type` are **not stored** because
they were empty in every record checked. The builder asserts they are still empty and
logs any non-empty value instead of dropping it silently.

| column | type | null | notes |
|---|---|---|---|
| recall_id | INTEGER | no | FK `recalls` |
| product_seq | SMALLINT | no | Position in `Products[]` |
| name | VARCHAR | no | `Name` |
| description | VARCHAR | yes | `Description` |
| model | VARCHAR | yes | `Model` |
| units_text | VARCHAR | yes | `NumberOfUnits`. Prose, e.g. "About 640 (…)"; not numeric |

## SaferProducts tables

### safer_incidents
Grain: `incident_report_id`. 71,858 reports, 2011-03 to 2026-10. `/Date(ms)/` values
are converted as UTC.

| column | type | null | notes |
|---|---|---|---|
| incident_report_id | INTEGER | no | PK |
| incident_report_number | VARCHAR | no | |
| report_date | DATE | no | `IncidentReportDate` |
| publication_date | DATE | yes | `IncidentReportPublicationDate` |
| incident_date | DATE | yes | Can be years before the report date |
| sp_category_id | INTEGER | yes | SP `ProductCategoryId`. **Not a NEISS code** |
| product_description | VARCHAR | yes | `IncidentProductDescription` |
| brand | VARCHAR | yes | `ProductBrandName` |
| model | VARCHAR | yes | `ProductModelName` |
| manufacturer_name | VARCHAR | yes | `ProductManufacturerName` |
| retailer_name | VARCHAR | yes | `ProductRetailCompanyName` |
| description | VARCHAR | yes | `IncidentDescription`. Free text, voluntary |
| severity_type_id | SMALLINT | yes | `safer_codebook` (SeverityTypes) |
| locale_id | SMALLINT | yes | -1 -> NULL. Meaning of -1 unverified |
| victim_sex_id | SMALLINT | yes | `safer_codebook` (Sexes) |
| victim_age_months | INTEGER | yes | `VictimAgeInMonths`. **0 -> NULL** (unknown) |
| victim_age_years | SMALLINT | yes | `victim_age_months // 12` |
| victim_age_group | VARCHAR | no | Same bands as NEISS; `unknown` if NULL |
| source_type_id | SMALLINT | yes | `safer_codebook` (SourceTypes) |

### safer_codebook
Grain: (`lookup`, `id`). Covers ProductCategories, SeverityTypes, Locales, Sexes,
SourceTypes, and RelationshipTypes.

| column | type | null | notes |
|---|---|---|---|
| lookup | VARCHAR | no | Entity set name |
| id | INTEGER | no | |
| public_name | VARCHAR | no | `*PublicName` |
| description | VARCHAR | yes | `*Description` |

## Crosswalks

### safer_category_xwalk
SP category -> NEISS code **by exact normalized name**. Normalization: lowercase, with
every run of non-alphanumeric characters replaced by one space, then trimmed.
Grain: `sp_category_id`.

- **Duplicate SP names (37 NEISS titles as of 2026-11-05).** SP has two or three
  categories with an identical name and description: an older id (121-890) and a newer
  one (1105-2248), e.g. Refrigerators 574/2096.
  - **All of them map to the same NEISS code** (many-to-one), flagged
    `sp_duplicate_name = true`.
  - This is safe, because no two NEISS titles normalize to the same string.
- If any SP name ever matches more than one NEISS code, the **build fails**.
- SP categories with no exact match get no row. Their incidents keep `neiss_code`
  NULL. Fuzzy matching is out of scope unless added with `match_method = 'manual'`.

| column | type | null | notes |
|---|---|---|---|
| sp_category_id | INTEGER | no | PK. FK `safer_codebook` (ProductCategories) |
| neiss_code | INTEGER | no | FK `product_codes` |
| match_method | VARCHAR | no | `exact_name` or `manual` |
| sp_duplicate_name | BOOLEAN | no | True when another SP id has the same name |
| notes | VARCHAR | yes | |

### product_recall_xwalk
A **text-based** link from NEISS products to recalls. There is no code link (Rule 6).

- **Scope:** the union of the top 50 NEISS codes by weighted visits (any slot,
  placeholder excluded, across the shipped years) for three groups: **all ages,
  under 5, and 65+**.
- **Process:**
  1. `build_xwalks.py` writes candidate rows: search terms derived from the product
     title, plus the recalls whose `search_text` matches them.
  2. A person reviews them in the seed CSV `build/seeds/product_recall_xwalk.csv`.
  3. The Parquet is built from the seed.
- Grain: (`neiss_code`, `recall_id`). There is also one terms-only row per
  `neiss_code`, where `recall_id` is NULL.

| column | type | null | notes |
|---|---|---|---|
| neiss_code | INTEGER | no | FK `product_codes` |
| recall_id | INTEGER | yes | FK `recalls`. NULL on the terms-only row |
| search_terms | VARCHAR | no | The exact terms used, e.g. `bunk bed; bunk beds` |
| match_method | VARCHAR | no | `text_search` or `manual` |
| confidence | VARCHAR | no | `high`, `medium`, or `low` |
| audited | BOOLEAN | no | True only after human review of the recall text |
| scope_groups | VARCHAR | no | Subset of `all,under5,65plus` |
| notes | VARCHAR | yes | Why it matches, or why it is excluded |

## Views (created by `build_db.py`)

| view | definition |
|---|---|
| `v_case_products` | `neiss_cases` ⨝ `neiss_case_products` ⨝ `product_codes`: one row per (case, distinct product) |
| `v_product_estimates` | By `year, age_group, product_code`: `n_cases`, `est_visits = SUM(weight)`, `low_sample`, `is_placeholder`, `is_catch_all`, `product_title` (Rules 1, 2, 5) |
| `v_top_products` | `v_product_estimates` without placeholders, ranked by `est_visits` within year and age group |
| `v_recall_search` | `recall_id, recall_date, title, hazards_text, search_text` for keyword search (`ILIKE`). If the DuckDB `fts` extension loads at build time, `build_db.py` also creates a BM25 index on `recalls.search_text`. Its availability in the Cloud Run image is **unverified**, so the agent must work with `ILIKE` alone |
| `v_recalls_for_product` | `product_recall_xwalk` ⨝ `recalls` ⨝ `product_codes`, with `confidence` and `audited` exposed. The agent must state when `audited = false` |
| `v_safer_incidents_neiss` | `safer_incidents` ⟕ `safer_category_xwalk` ⟕ `product_codes`. `neiss_code` is NULL when unmatched |
| `data_dictionary` (table) | `table_name, column_name, type, nullable, notes`, parsed from this file |
