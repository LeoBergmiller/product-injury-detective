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
  - The Recalls API has empty codes.
  - Any recall/incident <-> NEISS link must be **text-based**: recall title/product name
    or SP category name matched to NEISS product names, with confidence, and documented.

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
- Output: `data/build/product_codes.parquet` (19.8 KB). Columns: `product_code,
  product_title, source_page, source`.

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
- 37 NEISS titles match more than one SP id. These need a tie-break rule.
- 172 NEISS titles have no exact match. "Nonportable Cribs" (SP 797) has no exact NEISS
  title match; fuzzy or manual matching is needed.
- **UNVERIFIED:** that equal names mean the same scope in both systems. This is likely,
  but the matches have not been audited.
- This links **SP incidents** to NEISS. **Recalls** still have no code, because the
  Recalls API `CategoryID` is empty, so the recall link remains text matching on titles.

---

## Unverified / open

- NEISS product-code titles for **pre-2025 codes** that have since been retired or
  renumbered. These need the comparability tables, which haven't been downloaded.
- Which product codes are outside CPSC jurisdiction (underlined in the manual; lost in
  text extraction).
- Column names in years other than 2025, especially pre-2019, including whether the
  narrative is `Narrative_1` + `Narrative_2`.
- Direct download URL pattern for the NEISS annual files (needed for a scripted fetch).
- NEISS reuse/license terms, both CPSC and DataLumos.
- NEISS minimum-sample or CV rules for stable estimates.
- SaferProducts API (entity sets, fields, auth, and JSON shape are verified above). Still
  open:
  - total incident count, server page size, `__next` paging, and `$inlinecount`;
  - `$filter`/`$orderby`/`$expand`/`$skip` support;
  - behavior when the 100 req/s limit is exceeded (429 or otherwise);
  - SP recall sets: `RecallDetails` returned 503 "Page Unavailable" twice. Whether they
    work at all, and which id scheme they use, is unknown;
  - the meaning of `LocaleId = -1`.
- Whether `RecallDateStart`/`RecallDateEnd` are inclusive, and the behavior of misspelled
  parameters. A third-party blog claims misspelled names return the full dataset. Not tested.
- The exact HTTP status code of the error pseudo-recall response.
- The NEISS product code -> recall crosswalk. It must be text-based, because
  `CategoryID` is empty, and its matching quality is unknown.
- Whether Census population denominators are needed.
