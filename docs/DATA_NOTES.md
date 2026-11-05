# Data Notes

Verified facts only. Each entry gives the source (URL or command) and the date checked.
Anything not verified is listed under **Unverified / open** at the bottom.

Tooling note (2026-11-05): direct `curl` to external hosts was blocked in the session
that wrote this file. Web checks used a fetch tool that returns a processed text view
of each page, not raw bytes. Field names below were confirmed from that view.

---

## Repo / secrets (2026-11-05)

- `.env` is gitignored. `git check-ignore -v .env` -> `.gitignore:153:.env`.
- `.env` has never been committed. `git log --all --oneline -- .env` -> 0 commits.
- `SAFERPRODUCTS_KEY` is present in `.env` and non-empty: 36 characters, GUID-shaped.
  The value was never printed.
- `docs/rubric.md` is **empty (0 bytes)** in the working tree and in commit c67a2bd.
  The rubric text still needs to be added.

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
    at an internal host (`rmsprodweb2az.int.cpsc:442`) and are unusable.** Use
    `$expand` or the lookup sets instead (untested).
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
  - The `ProductCategoryId` values seen (574, 575, 626, 781, 797) **look like NEISS
    product codes.** If they are, this is a real join key to NEISS `Product_1`.
    **UNVERIFIED:** confirm against the `ProductCategories` lookup and the coding manual.
- **Rate limits:** I sent 20 sequential `$top=1` requests, about 6.35 req/s achieved.
  - Results: **20/20 HTTP 200**, latency 0.10-0.29 s (mean 0.16 s).
  - The responses carry **no rate-limit headers** (no `X-RateLimit-*`, no `Retry-After`).
  - The documented 100 req/s limit was **not reached or tested**. Its behavior when
    exceeded (429? something else?) is unverified.
- **Unverified:** server-side page size and `__next` paging, `$inlinecount` total count,
  `$filter`/`$orderby`/`$expand` support, and the total record count.

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
- Reference documents linked from the FAQ (not yet downloaded or read):
  - Data dictionary (xlsx): https://www.cpsc.gov/s3fs-public/DataDictionary042022.xlsx?VersionId=ZudA.bM6W3epO1LHUp_XwQjL8q7zIgvD
  - Jan 2025 coding manual: https://www.cpsc.gov/s3fs-public/JANUARY-2025-NEISS-CPSC-only-Coding-Manual-Rev-1.pdf?VersionId=RxQ0jRb8mHK.z2CrX2iG385fkC8sZjnm
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
  `Product_1` has 767 distinct codes (range 102 to 984 as strings) and is never 0.
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
8. CRLF: DuckDB strips the line endings correctly. 0 values had a trailing `\r`.

---

## Unverified / open

- NEISS product-code names (they need the coding manual / comparability table PDFs).
- Column names in years other than 2025, especially pre-2019, including whether the
  narrative is `Narrative_1` + `Narrative_2`.
- Direct download URL pattern for the NEISS annual files (needed for a scripted fetch).
- NEISS reuse/license terms, both CPSC and DataLumos.
- NEISS minimum-sample or CV rules for stable estimates.
- SaferProducts API: entity sets, fields, auth behavior with the key, and rate limiting.
- Whether `RecallDateStart`/`RecallDateEnd` are inclusive, and the behavior of misspelled
  parameters. A third-party blog claims misspelled names return the full dataset. Not tested.
- The exact HTTP status code of the error pseudo-recall response.
- The NEISS product code -> recall `Products[].Name`/`CategoryID` crosswalk.
- Whether Census population denominators are needed.
