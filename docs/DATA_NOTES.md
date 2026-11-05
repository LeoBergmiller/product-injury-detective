# Data Notes

Verified facts only. Each entry gives the source (URL or command) and the date checked.
Anything not verified is listed under **Unverified / open** at the bottom.

Tooling note (2026-11-05): the CPSC web pages and the Recalls API were checked with a
fetch tool that returns a processed text view, not raw bytes, because direct `curl` was
blocked. Field names for those were confirmed from that view.
SaferProducts was queried directly with Python `urllib` (`build/fetch_saferproducts.py`),
and the raw responses were saved. The NEISS file was inspected locally with DuckDB.

---

## Repo / secrets (2026-11-05)

- `.env` is gitignored. `git check-ignore -v .env` -> `.gitignore:153:.env`.
- `.env` has never been committed. `git log --all --oneline -- .env` -> 0 commits.
- `SAFERPRODUCTS_KEY` is present in `.env` and non-empty: 36 characters, GUID-shaped.
  The value was never printed.
- Python env: uv project (`pyproject.toml`, `uv.lock`) pinned to Python 3.11 via
  `.python-version`. Dependencies: `duckdb==2.0.0`, `pyyaml==6.0.3`.
  Note: DuckDB 2.0 relations have no `fetchone()`; use `fetchall()[0]`.

---

## CPSC Recalls API (2026-11-05)

Endpoint: `https://www.saferproducts.gov/RestWebServices/Recall?format=json`

- **Date filter works:** `&RecallDateStart=2024-10-01&RecallDateEnd=2024-10-31` returned
  a JSON **array** of 19 recalls, with dates 2024-10-10, 10-17, 10-24, and 10-31.
  - `RecallDateStart=2024-10-01&RecallDateEnd=2024-10-03` returned `[]`: an empty array,
    not an error.
- **Top-level keys per recall (22):** `RecallID, RecallNumber, RecallDate, Description,
  URL, Title, ConsumerContact, LastPublishDate, Products, Inconjunctions, Images,
  Injuries, Manufacturers, Retailers, Importers, Distributors, SoldAtLabel,
  ManufacturerCountries, ProductUPCs, Hazards, Remedies, RemedyOptions`
- **Nested keys observed** (first record, RecallID 10075 / RecallNumber 25025):
  - `Products[]`: `Name, Description, Model, Type, CategoryID, NumberOfUnits`
  - `Hazards[]`: `Name, HazardType, HazardTypeID`
  - `Injuries[]`, `Remedies[]`: `Name` (free text, e.g. "four laceration injuries reported")
  - `Retailers[]`, `Importers[]`: `Name, CompanyID`
  - These keys come from a single record. Other records may carry more keys.
- **Malformed-date quirk confirmed:** `&RecallDateStart=2024-13-45` returned a 1-element
  array holding a pseudo-recall with `"RecallID":0`, all scalars `null` except
  `"Title":"Error retrieving Recalls: An error occurred while reading from the store
  provider's data reader. See the inner exception for details."`, and every list empty.
  The fetch tool reported no HTTP error, which is consistent with the documented
  200-on-error behavior. The status code itself was not captured.
  -> **Build rule:** treat `RecallID == 0` or a `Title` starting with `"Error retrieving"`
  as a failed request.
- Injury counts sit inside free text (`Injuries[].Name`), not in a numeric field.
- Docs: API info page https://cpsc.gov/Recalls/CPSC-Recalls-Application-Program-Interface-API-Information
  links a programmer's guide:
  https://www.cpsc.gov/s3fs-public/RecallRetrievalWebServicesProgrammersGuide20171031.pdf
  (A 2018-09-17 version also exists: https://www.cpsc.gov/s3fs-public/RecallRetrievalWebServicesProgrammersGuide20180917.pdf.)
  The page says the data is "publicly available to consumers and businesses as well as
  software and application developers". It gives **no explicit license, rate limit, or key**.

---

## SaferProducts.gov incident API (2026-11-05)

Endpoint: `https://www.SaferProducts.gov/WebApi/Cpsc.Cpsrms.Web.Api.svc/`

- `$metadata` **without** credentials -> **HTTP 401 Unauthorized**. Metadata is not public.
- **With the key** (2026-11-05, `uv run python build/fetch_saferproducts.py`): auth works
  with the key as the basic-auth username and a blank password.
  - `$metadata` returned HTTP 200 (34,238 bytes, XML).
  - Saved outputs (gitignored), all in `data/raw/saferproducts/`: `metadata.xml`,
    `sample_IncidentDetails.json`, and `probe_summary.json`.
  - I grep-checked all three saved files: none contains the key.
- **Protocol:** OData `DataServiceVersion: 1.0`, served by IIS 10 / ASP.NET 4.0.
  - `Accept: application/json` works.
  - The payload shape is `{"d": [ ... ]}`: an array directly under `d`, with no
    `results` wrapper.
  - Dates use the WCF format `"/Date(1299801600000)/"` (epoch ms, UTC).
  - Each record has `__metadata` plus `__deferred` navigation links. **Those links point
    at an internal host (`rmsprodweb2az.int.cpsc:442`) and are unusable.** Use the lookup
    sets instead. `$expand` is untested; see Unverified.
- **Entity sets (15):**
  - Data sets:
    - `IncidentDetails` (32 props)
    - `IncidentDocuments`
    - `CompanyDetails`
    - `CompanyComments`
    - `RecallDetails`, `RecallHazardDetails`, `RecallProductCategories`,
      `RecallProductManufacturers`
  - Lookup sets: `Locations`, `Sexes`, `HazardTypes`, `Locales`, `ProductCategories`,
    `RelationshipTypes`, `SeverityTypes`, `SourceTypes`.
  - Full property lists are in `metadata.xml`.
- **`IncidentDetails` properties:**
  `IncidentReportNumber, IncidentReportId, IncidentReportDate,
  IncidentReportPublicationDate, SourceTypeId, ManufacturerNotificationDate, IncidentDate,
  IncidentDescription, CompanyCommentsExpanded, LocaleId, IncidentProductDescription,
  ProductCategoryId, ProductManufacturerCompanyId, ProductManufacturerName,
  ProductManufacturedDate, ProductRetailCompanyId, ProductRetailCompanyName,
  ProductPurchasedDate, ProductBrandName, ProductModelName, ProductSerialNumber,
  ProductUPCCode, UserStillHasProduct, ProductDamagedBeforeIncident,
  ProductModifiedBeforeIncident, UserContactedManufacturer, AnswerExplanation,
  UserPlansToContactManufacturer, SeverityTypeId, RelationshipTypeId, VictimGenderId,
  VictimAgeInMonths`
  - Lookups: `ProductCategories(ProductCategoryId, ProductCategoryPublicName,
    ProductCategoryDescription)`. The same three-field pattern holds for SeverityTypes,
    Locales, Sexes, SourceTypes, and RelationshipTypes.
- **Sample observations ($top=5, default order):**
  - With no `$orderby`, the oldest reports came back first: report date 2011-03-11.
  - One incident has `IncidentDate` 1997-12-11, about 13 years before it was reported.
  - `VictimAgeInMonths` is **in months**, e.g. 468 = 39 y. It is **0 when unknown**, with
    `VictimGenderId` null on the same rows. Treat 0 as missing, not as newborn.
  - `LocaleId` is -1 on rows where it looks unknown. `SeverityTypeId` can be null.
  - The `ProductCategoryId` values seen (574, 575, 626, 781, 797) looked like NEISS codes
    at first. **Disproved 2026-11-05;** see "Join key probe" below.
- **Rate limits:** I sent 20 sequential `$top=1` requests, about 6.35 req/s achieved.
  - Results: **20/20 HTTP 200**, latency 0.10-0.29 s (mean 0.16 s).
  - The responses carry **no rate-limit headers** (no `X-RateLimit-*`, no `Retry-After`).
  - The documented 100 req/s limit was **not reached or tested**. See Unverified.

### Join key probe (2026-11-05, `uv run python build/probe_join_key.py`)

The run **stopped at request 4 of ~11**. Requests 1-3 returned 200. Request 4,
`RecallDetails?$top=20`, returned **HTTP 503** with a UTF-16 HTML error page.
- The body wasn't saved on that run. The script now saves error bodies.
- No auth challenge occurred, and no response contained the key.
- The cause is unknown: the service could be temporarily down, or `RecallDetails` could
  be disabled.
- **Not attempted:** `RecallProductCategories`, `RecallHazardDetails`, the filtered
  `IncidentDetails` request, and both Recalls API windows.

**ProductCategories lookup** (saved: `data/raw/saferproducts/join_probe/ProductCategories_p{0,1,2}.json`)
- 2,263 rows, paged with `$top=1000&$skip=N`. **`$skip` works**, and so does `$top=1000`.
  No `__next` appeared, because the v1 payload is a bare array.
- Fields: `ProductCategoryId, ProductCategoryPublicName, ProductCategoryDescription`,
  plus deferred links to `IncidentDetails` and `RecallProductCategories`.
- Ids are unique and range from -3 to 2260. Id -3 is "Recall Product SubCategory", a
  structural row, not a product.
- **Within SaferProducts, the ids are consistent.** The 5 incidents sampled earlier match
  their lookup names:
  - 626 = "Hair Curlers, Curling Irons, Clips & Hairpins" (incident: "rv050 curling iron")
  - 797 = "Nonportable Cribs" (incident: "ChildCraft drop side crib")
  - 781 = "Diapers" (incident: "Pampers Swaddlers")
  - 574 = "Refrigerators"
  - 575 = "Electric Ranges or Ovens"

**SaferProducts `ProductCategoryId` is NOT the NEISS product-code scheme.** The numbers
overlap by coincidence. 628 of the 767 NEISS 2025 `Product_1` codes exist as SP ids, but
the names disagree with the NEISS narratives for the same number:

| code | SaferProducts name | NEISS 2025 narratives for that `Product_1` |
|---|---|---|
| 1807 | Drywall Control - No Complaint | falls to the floor |
| 1842 | Basketball | "missing 2 steps", "fall downstairs" |
| 1205 | Power Saws, Stationary | "playing basketball" |
| 1211 | Power Hack Saws | "playing football" |
| 1267 | Irrigation Equipment | "playing soccer" |

- These are 5 of the top 8 NEISS codes. The other 3 (4076, 4074, 5040) don't exist in
  SP at all. All 139 NEISS codes missing from SP are listed in the probe output.
- **Consequence:** joining on `ProductCategoryId = Product_1` would be silently wrong.
  This is a strong README trap.
- **Hypothesis (UNVERIFIED):** SP category *names* resemble NEISS product titles, e.g.
  "Hair Curlers, Curling Irons, Clips & Hairpins". A name-based crosswalk may match
  many rows exactly. Test this after the NEISS code names are extracted (item 2).
- **Code 5555:** it exists in NEISS 2025 (52 `Product_1` rows) but not in SP. Sample
  narratives: "DRANK UNKNOWN SUBSTANCE", "INGESTION/OVERDOSE - NO INFO PROVIDED". It may
  be a placeholder; check the coding manual in item 2.
**Retry run (2026-11-05, same script; 4 requests at ~2 req/s; recall windows first):**
- Recalls API `2024-10-01..2024-10-31` and `2025-01-01..2025-03-31`: both HTTP 200.
  - 119 recalls with 119 products, RecallID 10061 to 10219, and no error pseudo-recall.
  - Saved: `data/raw/recalls/recalls_<start>_<end>.json`.
- **Recalls API `Products[].CategoryID` is the empty string `""` for 119/119 products.**
  - `Products[].Type` is also `""`.
  - `Hazards[].HazardType` and `Hazards[].HazardTypeID` are `""` for all 119.
  - **So the Recalls API carries no usable product or hazard code** in these windows.
  - Populated fields: the free text `Title`, `Products[].Name`, `Hazards[].Name`
    (a sentence), and `Injuries[].Name`; also `NumberOfUnits`, which is prose like
    "About 640 (In addition, about 11 were sold in Canada)".
  - `ProductUPCs` is present for only 5/119 recalls.
  - Older recalls were not checked and may differ (unverified).
- **SaferProducts `RecallDetails?$top=20` returned HTTP 503 again.** The body (UTF-16
  HTML, saved as `.../join_probe/RecallDetails_top.HTTP503.body`) is a SaferProducts.Gov
  "Page Unavailable" page: "The page you are trying to reach is currently unavailable."
  - Per instructions, there was no retry, and `RecallProductCategories`,
    `RecallHazardDetails`, and the matched-recall requests were skipped.
  - **UNVERIFIED:** whether the SP recall sets ever work, and whether they use the
    incident `ProductCategoryId` scheme.
- `IncidentDetails?$filter=ProductCategoryId eq 1807&$top=5&$select=...` returned HTTP 200
  with `{"d": []}`.
  - The server accepted `$filter` + `$select`. Had it ignored the filter, `$top=5` would
    have returned 5 rows.
  - 0 incidents is consistent with 1807 being an SP admin category ("Drywall Control -
    No Complaint").
- **Conclusion for the crosswalk:** none of the three sources shares a product-code
  scheme.
  - NEISS uses its own codes.
  - SP `ProductCategoryId` is a different numbering.
  - The Recalls API had empty codes in these windows. **Corrected 2026-11-05 by the
    full snapshot:** they are empty for 2017-2025 only, and filled (with SP category
    ids) for most older recalls. See "Offline build" below.
  - Any recall/incident <-> NEISS link must be **text-based**: recall title/product name
    or SP category name matched to NEISS product names, with confidence, and documented.

### Size and paging probe (2026-11-05, `uv run python build/probe_size.py`)

There were 3 requests, all HTTP 200, with no auth challenge and no key in any body.
Saved: `data/raw/saferproducts/size_probe/`.
- **Total `IncidentDetails` = 71,858**, from
  `$inlinecount=allpages&$top=1&$select=IncidentReportId,IncidentReportDate`.
  - The `$inlinecount` response wraps rows as `{"d": {"results": [...], "__count": "71858"}}`.
  - So the server speaks OData v2 when asked, even though it sends
    `DataServiceVersion: 1.0`.
- **The server caps an `IncidentDetails` page at 50 rows.** The cap is per entity set:
  `ProductCategories` returned 1,000 rows per page. `$top=5000` returned 50 rows plus
  `__next`:
  `https://rmsprodweb1az.int.cpsc:442/...IncidentDetails?$select=...&$top=4950&$skiptoken=1170640`.
  - That host is internal and unusable, the same as with the `__deferred` links.
  - **Paging rule (proposed; `$skiptoken` not yet called directly):** rebuild the URL
    on the public base and pass `$skiptoken=<last IncidentReportId>`. The first page
    of the real pull verifies this.
- Default order is ascending by `IncidentReportId`. The first page covers report dates
  2011-03-11 to 2011-03-12.
- `$skip=71855&$top=3` works, but took **4.66 s** (vs ~0.2 s for a normal page). Deep
  `$skip` is slow, which is another reason to use `$skiptoken`.
- The newest records are `IncidentReportId` 6,310,437-6,310,467, with report date
  **2026-10-20**. Coverage is 2011-03 to 2026-10.
- **Full-pull estimate (not run):**
  - 71,858 / 50 = **~1,438 requests**.
  - Time: ~6 min at 5 req/s, or ~12 min at 2 req/s, plus latency.
  - Size: the 5-row full-field sample was 17,830 bytes, or ~3.5 KB/row with
    `__metadata`, which gives **~250 MB of raw JSON** for all fields.
  - Mean `IncidentDescription` in the sample was ~650 chars, so about 47 MB of
    descriptions. Projected Parquet (zstd) with the selected columns is **~15-25 MB**.
  - **These are estimates from a 5-row sample.**

**Proposed pull scope (not executed; awaiting approval):**
- Pull **all 71,858 incidents** with a `$select` of the columns in `docs/SCHEMA.md`
  (`safer_incidents`).
- Page with `$skiptoken` at **≤5 req/s**. The pull is resumable from the last id, and
  pages are saved raw to `data/raw/saferproducts/incidents/`.
- Filter locally, e.g. report or incident date ≥ 2019 to match the NEISS years.
- Rationale: date `$filter` semantics on `/Date()` fields are untested. A full pull is
  only ~1.4k requests, and local filtering is reproducible.
- Alternative if time is short: pull only incidents whose `ProductCategoryId` is in the
  name-crosswalked set for the top-50 NEISS products (an `$filter` OR-list). That is
  fewer requests, but the request count is unknown.

---

## NEISS (2026-11-05)

Sources:
- https://www.cpsc.gov/Research--Statistics/NEISS-Injury-Data
- https://www.cpsc.gov/Research--Statistics/NEISS-Injury-Data/Neiss-Frequently-Asked-Questions
- https://www.cpsc.gov/cgibin/NEISSQuery/home.aspx

- **A 2025 file exists.** The NEISSQuery home page lists "Archived Annual NEISS Data" for
  **2025, 2024, ... 2006**, in "Excel Tab Delimited Text SAS" formats.
  The direct file URLs were not visible in the fetched text and still need capturing.
- That page also says: "Due to ongoing site maintenance, the ability to download a
  filtered file is not available. Users are able download the archived data and filter
  it themselves." -> The full annual file is the only download path.
- FAQ: "The most recent calendar year of treatment dates generally become available in
  April of the following year." and "The most recent 20 years of NEISS data are available
  online."
- FAQ, on product codes: "product codes have been added, deleted, combined, or split into
  two or more codes." -> This confirms the code-drift trap.
- The 2018 update added seven variables, effective for treatment dates from 2019-01-01
  (NEISS landing page).
- Reference documents linked from the FAQ:
  - Data dictionary (xlsx), **in repo** at `docs/DataDictionary042022.xlsx` (read; see
    below): https://www.cpsc.gov/s3fs-public/DataDictionary042022.xlsx?VersionId=ZudA.bM6W3epO1LHUp_XwQjL8q7zIgvD
  - Jan 2025 coding manual, **in repo** at
    `docs/JANUARY-2025-NEISS-CPSC-only-Coding-Manual-Rev-1.pdf` (4.6 MB; not yet parsed):
    https://www.cpsc.gov/s3fs-public/JANUARY-2025-NEISS-CPSC-only-Coding-Manual-Rev-1.pdf?VersionId=RxQ0jRb8mHK.z2CrX2iG385fkC8sZjnm
  - The documents below have not been downloaded:
  - 2025 comparability table: https://www.cpsc.gov/s3fs-public/2025-Comparability-Table-Final.pdf?VersionId=iMIGMb5nMx5kg1HJH27JJSsAAZeP7poY
  - 2024 comparability table: https://www.cpsc.gov/s3fs-public/2024-Comparability-Table-1.pdf?VersionId=wD8fIwfFIUNqqQsynJCWoJjCi18mO2uF
  - Jan 2024 coding manual: https://www.cpsc.gov/s3fs-public/January-2024-NEISS-CPSC-only-Coding-Manual_0.pdf?VersionId=NLhRZ_tVM1LszuXQLlTvDxj9.ub27ULd
- **Reuse terms:** none of the three CPSC pages above states a license or citation
  requirement. The only related text is the external-link disclaimer.
- DataLumos mirror: project 230963, "NEISS Archived Annual Data", DOI 10.3886/E230963V1
  (per search-result metadata). The project page returned **HTTP 403** to the fetch tool,
  so its contents and license are unverified.

---

## NEISS 2025 file: inspected (2026-11-05)

File: `data/raw/neiss/neiss2025.tsv` (gitignored). Downloaded by Leo from the NEISSQuery
archived-data section. It is 85,850,399 bytes: ASCII, tab-delimited, **CRLF** line
endings, with a quoted header row.
Dictionary: `docs/DataDictionary042022.xlsx`, which has two sheets, "2018 and prior" and
"2019 and beyond".
Commands: DuckDB 2.0.0 `read_csv(..., delim='\t', header=true, all_varchar=true)` via
`uv run python -I <scratch script>`, plus `head -1`, `wc -l`, and `file`.

**Columns (25, in file order):**
`CPSC_Case_Number, Treatment_Date, Age, Sex, Race, Other_Race, Hispanic, Body_Part,
Diagnosis, Other_Diagnosis, Body_Part_2, Diagnosis_2, Other_Diagnosis_2, Disposition,
Location, Fire_Involvement, Product_1, Product_2, Product_3, Alcohol, Drug, Narrative_1,
Stratum, PSU, Weight`

**Key fields:**
- **Weight:** `Weight`, the "Statistical Weight for National Estimates". It has no nulls.
  It ranges from 7.12 to 113.1208 and takes only **13 distinct values**. Weight is set by
  stratum, not by case: stratum C is about 7-8, V about 26-28, M about 46-52, L 51.82,
  and S about 101-113.
  - **Sum of weights = 16,485,413**, the estimated 2025 national ED visits in scope.
  - Design variables: `Stratum` takes the values C, L, M, S, V. `PSU` has 77 distinct
    hospitals in 2025.
- **Product:** `Product_1`, `Product_2`, `Product_3` are integer codes of up to 4 digits.
  `Product_1` has 767 distinct codes, numeric range 102 to 5555, and is never 0.
  `Product_2` is `0` (no second product) in 339,666 rows. `Product_3` is `0` in 401,955
  rows and only exists from 2019.
  - **The dictionary does not list product-code names.** It says "See NEISS Coding
    Manual". We need the coding manual or comparability table PDFs to build
    `product_codes`.
- **Narrative:** the column is **`Narrative_1`**, but the dictionary calls it `Narrative`.
  Lengths: max 400, mean 131, none empty. The text is uppercase and starts with
  age/sex shorthand, e.g. `5MOF ...`, `13YOM, ...`, then `DX:`.
- **Age coding** (dictionary; values confirmed in the file):
  - `0` means not recorded: 45 rows.
  - `2` to `120` is age in years. The 2025 max is 109.
  - **`201` to `223` is age 1-23 MONTHS: 24,339 rows.** The value `1` never occurs.
  - Treatment_Date uses the `MM/DD/YYYY` format. All 2025 dates parse, from 2025-01-01
    to 2025-12-31.

**Counts:**
- 410,201 data rows (`wc -l` = 410,202 including the header). `CPSC_Case_Number` is
  unique.
- No blanks in any required field. Optional text fields are mostly blank:
  - `Other_Race`: 395,149 blank. Top values: UNKNOWN, MULTI-RACIAL, HISPANIC, NS.
  - `Other_Diagnosis`: 308,721 blank.
  - `Body_Part_2` and `Diagnosis_2`: 313,426 blank.
- Every `Body_Part`, `Diagnosis`, `Body_Part_2`, and `Diagnosis_2` value appears in the
  dictionary.

**Surprises / traps observed:**
1. **Naive under-5 filter undercounts by ~42%.** `Age < 5` gives 33,344 rows and a
   weighted 856,149. The correct filter, `Age BETWEEN 2 AND 4 OR Age BETWEEN 201 AND 223`,
   gives 57,638 rows and a weighted **1,469,070**. The naive filter also includes the
   Age=0 "not recorded" rows. **This is a README example.**
2. **Rows ≠ estimates, and the ranks change.** Weights differ about 16x across strata
   (7.12 to 113). Two examples:
   - Product 1267 is #8 by row count but #14 by weighted estimate.
   - Product 464 is #13 by rows but #9 by weight.
   A plain `COUNT(*)` gives a different top-10. **This is a README example.**
3. "Unknown" values are coded `0`, not NULL, so `IS NULL` filters miss them:
   - Race 0: 57,714 rows
   - Location 0: 147,425 rows (36%)
   - Hispanic 0: 45,939 rows
   - Sex 0: 112 rows
   - Body_Part 0 means **"Internal"**, a real value, not unknown.
4. The dictionary/file name mismatch (`Narrative` vs `Narrative_1`) means we can't
   trust dictionary names blindly across years.
5. Code sets change across years. Body_Part 78, 86, 90, and 91 are "(Old)" codes, only
   in the 2018-and-prior sheet. Hispanic, Alcohol, Drug, Product_3, Body_Part_2,
   Diagnosis_2, and Other_Diagnosis_2 exist only from 2019. Sex=3 (Non-Binary/Other)
   exists from 2021 but has 0 rows in 2025.
6. Alcohol and Drug are `0 = No/No information`. A 0 doesn't mean "no".
7. 2025 monthly case counts range from 25,666 (Feb) to 38,147 (May), so there is
   seasonality.
8. CRLF: DuckDB strips the line endings correctly. No value in `Weight` or
   `Narrative_1` contains a stray `\r`.

---

## NEISS product-code names (2026-11-05)

Source: `docs/JANUARY-2025-NEISS-CPSC-only-Coding-Manual-Rev-1.pdf` (214 pages, Word
export, real text layer). The relevant part is **Appendix H, "Numerical Listing of NEISS
Product and Activity Codes"**, on pp. 198-214.
- Command: `uv run python build/build_product_codes.py`, using `pypdf` 6.21.4, a dev
  dependency.
- Output: `data/processed/product_codes.parquet` (~20 KB). Columns: see
  `docs/SCHEMA.md`.

**Format:**
- Each page has a header "Code Product Title", then one `NNNN Title` line per code.
- **Appendix H has no category or section headings**, so the table has none. Groupings
  by leading digit are NOT stated in the manual and were not invented.
- Code 0478 has two spaces after the number. My first regex missed it; it's fixed now.
  Runs of whitespace inside titles are collapsed.

**Result:**
- **806 codes**, 0102 to 5555. They are unique and ascending, with no empty titles.
  This equals the count of `NNNN <title>` lines on pp. 198-214.
- **Coverage vs `neiss2025.tsv`:** 767/767 distinct `Product_1` codes and 773/773 codes
  across all three slots have a title, so **0 codes lack a name**.
- Spot checks agree with the narratives:
  - 1807 Floors or flooring materials
  - 1842 Stairs or steps
  - 1205 Basketball
  - 1211 Football
  - 1267 Soccer
  - 4076 Beds or bedframes, other or not specified
  - 478 Drinking glasses, cups, and mugs ("glass shattered", "broken mug")
- 142 titles contain "other", "not specified", or "not elsewhere classified". These are
  catch-all buckets, so the agent should label them as such.

**Code 5555 is a placeholder, not a real product.**
- Appendix H: "Substance ingested or inhaled by a victim under age 5 which could lead to
  a poisoning and for which there is no current product code."
- Manual text, p. ~13: "If the product is not a consumer product, or if the product is
  unknown, and you cannot locate an appropriate product code in the alphabetic listing,
  report the case using product code 5555."
- The manual also mentions **9999** as a temporary "can't find a code" value that CPSC
  corrects. Code 9999 is not in Appendix H and not in the 2025 file.
- In 2025, 5555 appears on 60 rows across all slots, and **all 60 are under age 5**.
- -> Exclude 5555 from "top products" rankings, or label it as "unknown substance
  (poisoning, under 5)".

**Product slot order does not matter** (coding manual, "Up to three product codes…"
section, p. ~24-25):
- Quote: "When multiple products are involved, it does not matter in what order you
  enter them." So `Product_1` is **not** the primary product.
- Also: if the same code applies twice, it is entered once. If Product 2 is 0000,
  Product 3 is 0000 too.
- -> Product rankings must count all three slots (`docs/SCHEMA.md` Rule 2).

**Stability rule:** I searched the coding manual for "1,200", "unstable", "coefficient
of variation", "fewer than 20", and "reliab". **None were found.** The CPSC minimum-case
or CV rule is still unverified; `docs/SCHEMA.md` uses a configurable project threshold.

**Not captured:** the manual says codes **underlined** in the alphabetical section fall
"outside of CPSC" jurisdiction. Text extraction loses underlining, so that flag is
**unverified** and not in the table.
**Scope:** this is the January 2025 manual only. Codes used in earlier years but retired
before 2025 won't have titles. We need the comparability tables (2024, 2025) before
loading 2019-2024.

### SaferProducts category name <-> NEISS title (2026-11-05, local, no requests)
- Method: normalize each name to lowercase alphanumerics separated by single spaces.
  Then compare SP `ProductCategoryPublicName` (2,263 rows) with NEISS `product_title`
  (806 rows).
- **634/806 NEISS titles match an SP category name exactly. In 0 of those 634 is the SP
  id equal to the NEISS code.** This confirms the separate numbering and gives a
  name-based crosswalk.
  - Example: NEISS 115 "Vacuum cleaners" = SP 382.
  - Example: NEISS 278 "Electric ranges or ovens (excl. counter-top ovens)" = SP 575.
- 37 NEISS titles match more than one SP id: 35 match 2 ids and 2 match 3.
  - These are **duplicate SP categories with identical name and description**: an older
    id in 121-890 and a newer one in 1105-2248, e.g. Refrigerators 574/2096, Dishwashers
    547/1912, Heat Pumps 364/1990.
  - No two NEISS titles normalize to the same string.
  - Rule (`docs/SCHEMA.md`): map all duplicates to the one NEISS code.
- 172 NEISS titles have no exact match. "Nonportable Cribs" (SP 797) has no exact NEISS
  title match; fuzzy or manual matching is needed.
- **UNVERIFIED:** that equal names mean the same scope in both systems. This is likely,
  but the matches have not been audited.
- This links **SP incidents** to NEISS. **Recalls** still have no code, because the
  Recalls API `CategoryID` is empty, so the recall link remains text matching on titles.

---

## Offline build, 2019-2025 (2026-11-05)

Scripts are in `build/`, the contract is `docs/SCHEMA.md`, and all parameters live in
`build/config.yaml`.

### NEISS 2019-2025 (`build_neiss.py`)
**Inputs:** `data/raw/neiss/neiss2019.tsv` … `neiss2025.tsv`, added by Leo. All seven
have the **identical 25-column header**, ASCII text, and CRLF line endings.

**Quoting:**
- Fields containing commas are wrapped in `"`, with `""` escapes; ~4.6k narratives
  per year.
- Every year is balanced (0 unclosed quotes).
- 2024 and 2025 each have one quoted `Other_Diagnosis` containing a tab
  (`"NASAL SEPTAL DEVIATION<TAB>"`). A quote-aware reader handles it.

**Defects repaired** (logged in `data/processed/tmp/neiss_repairs.json`):

| Year | Case | Defect | Repair |
|---|---|---|---|
| 2019 | 190746091 | Unquoted CR LF inside the narrative | Lines joined with one space |
| 2021 | 210332827, 210760065 | Same | Same |
| 2023 | 230975940 | Same | Same |
| 2023 | 240131872, 240152467 | Lone LF inside a field (not CR LF) | LF replaced with one space |

- A split record arrives as 22 + 4 fields; joined, it is exactly 25.
- 2020, 2022, 2024, and 2025 needed no repair.

**Validation (all pass):**
- Case ids are unique across years.
- Every date parses, and its year equals the file year.
- `Age` is in {0, 2-120, 201-223}.
- Weights are > 0.
- All coded columns are integers, and every code is in the dictionary codebook.
- No case has the same code in two slots.

**Rows and weighted totals per year:**

| Year | Rows | Weighted visits |
|---|---|---|
| 2019 | 358,715 | 13,464,372 |
| 2020 | 309,370 | 10,994,077 |
| 2021 | 340,442 | 11,738,091 |
| 2022 | 323,343 | 12,663,028 |
| 2023 | 338,262 | 12,740,256 |
| 2024 | 361,672 | 15,068,979 |
| 2025 | 410,201 | 16,485,413 |
| **Total** | **2,442,005** | |

**Product codes:**
- 2,902,818 (case, product) rows. 17.2% of 2025 cases list more than one product.
- **7 codes in 2019-2024 are not in the January 2025 manual:** 1329 (2019 only),
  1543, 1545, 1710, 1903, 3215 (2019-2023; 5,936 case-products, ~254k visits), and
  5042 (2019 only).
  - Together they are 0.35% of weighted case-product mentions.
  - They are allowlisted, with no guessed titles.
- **Presence:** 710 codes appear in all seven years and 94 do not. Presence is not
  definitional stability.
- **Comparability PDFs (2024, 2025) are not in `docs/`**, so the definitional-stability
  check is blocked.

**Codebook:** 151 entries from the "2019 and beyond" sheet. The age range rows are
skipped; Rule 4 handles age.

**Sizes:**
- One file per year in `data/processed/neiss_cases/`: 14.0-19.9 MB each, 114 MB total.
- As a single file it is 114 MB, over the limit. The narratives are 92 MB of that.
- A DuckDB with the narratives stored natively measured 207 MB; without narratives,
  64 MB.

### SaferProducts incidents (`fetch_saferproducts_incidents.py`, `build_saferproducts.py`)
**Pull:**
- Test first: page 1 returned 50 rows, and its `__next` pointed at the internal host
  `rmsprodweb2az.int.cpsc:442`.
- `$skiptoken=1170640` equals the last id. Page 2, rebuilt on the public host, starts
  at id 1170643.
- **Full pull:** 1,438 pages, **71,858 rows** (equal to `$inlinecount`), and 1,446
  requests including 8 for lookups.
- About 4 req/s, **0 retries**, ~7 min.
- 109 MB of raw JSON in `data/raw/saferproducts/incidents/pages/`; the run is
  resumable via `checkpoint.json`.

**Lookups:**
- `-1` = "Unspecified" in SeverityTypes, Locales, SourceTypes, and RelationshipTypes.
- `VictimGenderId` has 16,883 rows with `-1`, which **is not defined** in Sexes.
- **SP Sexes: 1 = Female, 2 = Male, 3 = Unknown. NEISS uses the opposite (1 = Male,
  2 = Female).**

**Values:**
- All dates are midnight UTC. `IncidentDate` is null in 39 rows.
- `VictimAgeInMonths = 0` in 26,924 rows (37%), which become NULL. The maximum is
  1,440 (120 years).
- Reports per year range from 3,073 (2019) to 6,924 (2012). 2026 has 6,111 through
  Oct 20.

**Output:** `safer_incidents.parquet` is 23.0 MB. Stored natively in DuckDB, the same
table measured 97.5 MB.

**Name link to NEISS:**
- 673 SP category ids map to 634 NEISS codes, including 76 duplicate-name SP ids.
- **82.7% of incidents get a NEISS code.**
- The largest unlinked SP categories have no exact NEISS title: "Light Bulbs" (868),
  "Exercise Equipment" (574), "Bicycles and Accessories, (Excl.mountain or
  All-terrain)" (563), and "Nonportable Cribs" (469). Fuzzy or manual matching would
  pick these up.

### Recalls snapshot (`fetch_recalls.py`, `build_recalls.py`)
**Pull:** 57 yearly requests (1970-2026) at ≤2 req/s. All HTTP 200, with **no error
pseudo-recalls and no retries**.

**Result:** **10,082 recalls** (1973-2026) with 11,878 recall-product rows; no
duplicate RecallIDs.

Per-year counts (recalls):

| Years | Counts |
|---|---|
| 1973-1980 | 8, 14, 13, 27, 36, 43, 33, 22 |
| 1981-1990 | 12, 23, 23, 44, 30, 46, 49, 75, 80, 78 |
| 1991-2000 | 84, 82, 75, 109, 128, 138, 151, 133, 156, 191 |
| 2001-2010 | 245, 256, 215, 278, 323, 319, 452, 392, 383, 362 |
| 2011-2020 | 313, 310, 290, 296, 305, 330, 280, 258, 241, 257 |
| 2021-2026 | 219, 292, 324, 305, 420, 514 |

`data/raw/recalls/snapshot/counts.json` keys these by year.

**`Products[].CategoryID` / `Type` (corrects the earlier "always empty"):**
- Filled for most products through 2015 and 249/330 in 2016.
- **Empty for every recall from 2017 to 2025.** 116 of 514 are filled in 2026.
- Of the 7,151 filled, 5,538 ids exist in SP ProductCategories, and **5,501 have
  `Type` equal to the SP name**. So they are SP category ids, not NEISS codes.
- The others are ids not in the lookup, e.g. 4062, 68242.

**Other fields:**
- `Hazards[].HazardType` / `HazardTypeID` are empty for all 9,953 hazards.
- 358 product names are empty.

### Crosswalk draft (`build_xwalks.py`)
**Scope:** the top-50 lists (any slot, weighted, 2019-2025, placeholder and untitled
codes excluded).
- Rank 1 is 1807 floors for all ages, under 5, and 65+.
- The union is **92 codes**.

**Candidates:** 4,398 recall links plus 92 terms-only rows, in
`build/seeds/product_recall_xwalk.draft.csv` and the seed.

| Confidence | All candidates | Recalls dated 2019+ |
|---|---|---|
| high | 2,682 | 811 |
| medium | 1,333 | 463 |
| low | 383 | 153 |

- **Audited: 0.**
- The modifier rule (title word followed by another noun -> medium) removed most
  false "high" matches, e.g. "Floor Lamps", "Step Stools", "Bed Rails", "Bicycle
  Helmets".
- Precision is still unmeasured. Generic titles ("Toys, not specified", "Batteries",
  "Chairs") produce many candidates.

### Assembled DB (`build_db.py`)
- `data/processed/injuries.duckdb` is **0.5 MB**: views over Parquet plus a
  `data_dictionary` table (114 rows).
- All 18 files in `data/processed/` total **151.2 MB**. The largest is 23 MB.
- Typical queries take 0.01-0.05 s, e.g. the 2025 under-5 top 10, the 65+ trend for
  one code, a recall ILIKE search, and a narrative ILIKE search.
- Views use repo-relative paths. Opening from another directory fails with "No files
  found", which `SET file_search_path = '<repo root>'` fixes (verified).
- **The DuckDB `fts` extension is not installed locally.** Installing it needs a
  download that was not pre-approved, so there is no BM25 index; search uses ILIKE.
- **Sanity checks pass:**
  - 2025 weight total 16,485,413.
  - Under 5: 1,469,070 (vs 856,149 with the naive filter).
  - All 7 years present.
  - 2025 rank 1 is 1807.
  - `'all'` rows equal the sum of the age-group rows.
  - 0 audited links.

---

## Unverified / open

- Titles for the 7 retired codes (1329, 1543, 1545, 1710, 1903, 3215, 5042), and the
  definitional stability of all codes across 2019-2025. Both need the 2024/2025
  comparability tables, which are **not in `docs/`**.
- Recall crosswalk precision: 0 of 4,398 candidates audited.
- Whether the DuckDB `fts` extension can be installed in the Cloud Run image.
- Which product codes are outside CPSC jurisdiction (underlined in the manual; lost in
  text extraction).
- Column names before 2019, including whether the narrative is `Narrative_1` +
  `Narrative_2`. 2019-2025 is verified identical.
- Direct download URL pattern for the NEISS annual files (needed for a scripted fetch).
- NEISS reuse/license terms, both CPSC and DataLumos.
- NEISS minimum-sample or CV rules for stable estimates.
- SaferProducts API (entity sets, fields, auth, and JSON shape are verified above). Still
  open:
  - `$filter` on date fields, `$orderby`, and `$expand`. Verified so far: `$select`,
    `$top`, `$skip`, `$inlinecount`, and `$filter` with `eq` on an int. `$skiptoken`
    is only advertised in `__next` and has not been called directly yet;
  - behavior when the 100 req/s limit is exceeded (429 or otherwise);
  - SP recall sets: `RecallDetails` returned 503 "Page Unavailable" twice. Whether they
    work at all, and which id scheme they use, is unknown;
  - ~~the meaning of `LocaleId = -1`~~ resolved: "Unspecified", per the Locales
    lookup.
- Whether `RecallDateStart`/`RecallDateEnd` are inclusive, and the behavior of misspelled
  parameters. A third-party blog claims misspelled names return the full dataset. Not tested.
- The exact HTTP status code of the error pseudo-recall response.
- The NEISS product code -> recall crosswalk. It must be text-based, because
  `CategoryID` is empty, and its matching quality is unknown.
- Whether Census population denominators are needed.
