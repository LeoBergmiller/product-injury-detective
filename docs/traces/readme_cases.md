# README cases: plain run_sql agent vs Product-Injury Detective

Generated 2026-11-08 21:43 UTC by `build/run_readme_cases.py`, model `vertex_ai/gemini-3.5-flash-lite`.
Each case was run 5 times in plain mode and 3 times in detective mode. **Every run is listed**; outcomes are classified automatically from the numbers in the answer.
Plain = minimal prompt, raw `neiss` view (original file columns) plus `product_codes` titles, run_sql only, no guardrails.

## under5_age_filter

**Question:** How many ER visits were there for children under 5 in 2025?

**Verified answer (SQL on the DB):** 1,469,070 estimated injury-related ER visits (57,638 sample cases); the raw filter Age < 5 gives 856,149 (33,344 rows)

| mode | runs | outcome | count |
|---|---|---|---|
| plain | 5 | wrong: raw Age < 5, unweighted rows (33,344) | 4 |
| plain | 5 | wrong: raw Age < 5, weighted (856,149) | 1 |
| detective | 3 | correct: age_years < 5, weighted (1,469,070) | 3 |

## product_ranking_slot1

**Question:** Which products send the most people to the ER in 2025?

**Verified answer (SQL on the DB):** Weighted, any slot: floors 3,079,996; stairs 1,310,287; beds 1,133,481. Slot 1 only: floors 2,199,379

| mode | runs | outcome | count |
|---|---|---|---|
| plain | 5 | wrong: Product_1 only, weighted (floors 2,199,379) | 5 |
| detective | 3 | correct: any slot, weighted (floors 3,079,996) | 3 |

## under5_age_filter: every run

### plain

- **Run 1: wrong: raw Age < 5, unweighted rows (33,344)**
  - Answer: Based on the NEISS emergency-department injury records, there were **33,344** ER visits recorded for children under 5 years old in the year 2025. *(Note: Depending on how "children under 5" is interpreted, this includes infants and toddlers where `age < 5`, i.e., ages 0 through 4).*
  - SQL: `SELECT count(*) FROM neiss WHERE age < 5 AND strftime(treatment_date, '%Y') = '2025'`
  - SQL: `SELECT min(treatment_date), max(treatment_date), count(*) FROM neiss WHERE strftime(treatment_date, '%Y') = '2025'`
  - SQL: `SELECT count(*) FROM neiss WHERE age < 5 AND strftime(treatment_date, '%Y') = '2025'`
- **Run 2: wrong: raw Age < 5, unweighted rows (33,344)**
  - Answer: There were **33,344** emergency room visits recorded in the NEISS database for children under 5 years old in 2025.
  - SQL: `SELECT count(*) AS er_visits_under_5_2025 FROM neiss WHERE age < 5 AND EXTRACT(YEAR FROM treatment_date) = 2025;`
- **Run 3: wrong: raw Age < 5, unweighted rows (33,344)**
  - Answer: Based on the NEISS data for 2025, there were 33,344 emergency department visits for children under 5 years old (using standard numeric age values `< 5`, or 35,922 if including infants encoded with NEISS age codes for months under 1 year old, where ages 201–204 represent ages under 5 months).
  - SQL: `SELECT count(*) FROM neiss WHERE age < 5 AND strftime(treatment_date, '%Y') = '2025'`
  - SQL: `SELECT min(treatment_date), max(treatment_date) FROM neiss WHERE strftime(treatment_date, '%Y') = '2025'`
  - SQL: `SELECT count(*) FROM neiss WHERE (age < 5 OR (age >= 200 AND age < 205)) AND strftime(treatment_date, '%Y') = '2025'`
  - SQL: `SELECT age, count(*) FROM neiss WHERE strftime(treatment_date, '%Y') = '2025' GROUP BY age ORDER BY age LIMIT 10`
  - SQL: `SELECT min(age), max(age) FROM neiss WHERE strftime(treatment_date, '%Y') = '2025' AND age < 10`
  - SQL: `SELECT age, count(*) FROM neiss WHERE strftime(treatment_date, '%Y') = '2025' AND age >= 200 ORDER BY age`
  - SQL: `SELECT age, count(*) FROM neiss WHERE strftime(treatment_date, '%Y') = '2025' AND age >= 200 GROUP BY age ORDER BY age`
  - SQL: `SELECT count(*) FROM neiss WHERE strftime(treatment_date, '%Y') = '2025' AND (age < 5 OR (age >= 200 AND age < 205))`
- **Run 4: wrong: raw Age < 5, unweighted rows (33,344)**
  - Answer: There were **33,344** emergency room visits recorded in the NEISS database for children under 5 years old in 2025. *(Note: In the NEISS dataset, age is recorded in months for infants under 1 year old, and in full years for children 1 and older; `Age < 5` correctly captures all children under 5, including infants.)*
  - SQL: `SELECT count(*) FROM neiss WHERE age < 5 AND year = 2025`
  - SQL: `SELECT column_name FROM information_schema.columns WHERE table_name = 'neiss'`
  - SQL: `SELECT count(*) FROM neiss WHERE Age < 5 AND EXTRACT(YEAR FROM CAST(Treatment_Date AS DATE)) = 2025`
  - SQL: `SELECT min(Treatment_Date), max(Treatment_Date) FROM neiss`
  - SQL: `SELECT count(*) FROM neiss WHERE Age < 5 AND Treatment_Date >= '2025-01-01' AND Treatment_Date <= '2025-12-31'`
- **Run 5: wrong: raw Age < 5, weighted (856,149)**
  - Answer: There were **33,344** actual sampled emergency room visits recorded in the NEISS database for children under 5 years old in 2025 (accounting for an estimated **856,149** weighted national ER visits).
  - SQL: `SELECT count(*) FROM neiss WHERE age < 5 AND strftime('%Y', treat_date) = '2025'`
  - SQL: `DESCRIBE neiss;`
  - SQL: `SELECT count(*) FROM neiss WHERE Age < 5 AND strftime('%Y', Treatment_Date) = '2025'`
  - SQL: `SELECT sum(Weight) FROM neiss WHERE Age < 5 AND strftime('%Y', Treatment_Date) = '2025'`

### detective

- **Run 1: correct: age_years < 5, weighted (1,469,070)**
  - Answer: In 2025, there were an estimated 1,469,070 injury-related ER visits (from a sample of 57,638 cases) for children under 5. These are counts of injury-related ER visits, not rates: the groups differ in size, and population denominators are not in the data.
  - SQL: `SELECT COUNT(*) AS n_cases, ROUND(SUM(weight)) AS est_visits FROM neiss_cases WHERE year = 2025 AND age_years < 5`
  - SQL: `SELECT age_group, COUNT(*) AS n_cases, ROUND(SUM(weight)) AS est_visits FROM neiss_cases WHERE year = 2025 AND age_group IN ('0-4', '5-14', '15-24', '25-44', '45-64', '65+') GROUP BY age_group ORDER BY est_visits DESC`
- **Run 2: correct: age_years < 5, weighted (1,469,070)**
  - Answer: In 2025, there were an estimated 1,469,070 injury-related ER visits for children under 5 (based on 57,638 sample cases). These are counts of injury-related ER visits, not rates: the groups differ in size, and population denominators are not in the data.
  - SQL: `SELECT COUNT(*) AS n_cases, ROUND(SUM(weight)) AS est_visits FROM neiss_cases WHERE year = 2025 AND age_years < 5`
  - SQL: `SELECT age_group, COUNT(*) AS n_cases, ROUND(SUM(weight)) AS est_visits FROM neiss_cases WHERE year = 2025 AND age_group IN ('0-4', '5-14', '15-24', '25-44', '45-64', '65+') GROUP BY 1 ORDER BY est_visits DESC`
- **Run 3: correct: age_years < 5, weighted (1,469,070)**
  - Answer: In 2025, there were an estimated 1,469,070 injury-related ER visits for children under 5 (based on 57,638 sample cases). For comparison across age groups in 2025: - 0-4: 57,638 sample cases, estimated 1,469,070 injury-related ER visits - 5-14: 81,375 sample cases, estimated 2,297,600 injury-related ER visits - 15-24: 51,117 sample cases, estimated 2,064,131 injury-related ER visits - 25-44: 62,219 ...
  - SQL: `SELECT COUNT(*) AS n_cases, ROUND(SUM(weight)) AS est_visits FROM neiss_cases WHERE year = 2025 AND age_years < 5`
  - SQL: `SELECT age_group, COUNT(*) AS n_cases, ROUND(SUM(weight)) AS est_visits FROM neiss_cases WHERE year = 2025 AND age_group IN ('0-4', '5-14', '15-24', '25-44', '45-64', '65+') GROUP BY age_group ORDER BY age_group`

## product_ranking_slot1: every run

### plain

- **Run 1: wrong: Product_1 only, weighted (floors 2,199,379)**
  - Answer: Based on NEISS emergency department data for **2025** (using national statistical weight estimates), the products and activities that send the most people to the ER are: 1. **Floors or flooring materials** (~2.20 million estimated injuries / 47,267 sample visits) 2. **Stairs or steps** (~1.23 million estimated injuries / 29,531 sample visits) 3. **Beds or bedframes** (~1.00 million estimated injur ...
  - SQL: `SELECT p.title AS product_name, COUNT(*) AS er_visits, SUM(n.weight) AS estimated_total_injuries FROM neiss n JOIN product_codes p ON n.product_code = p.code WHERE n.year = 2025 GROUP BY p.title ORDER BY estimated_total_injuries DESC LIMIT 10;`
  - SQL: `DESCRIBE neiss;`
  - SQL: `SELECT p.title AS product_name, COUNT(*) AS er_visits, SUM(n.Weight) AS estimated_total_injuries FROM neiss n JOIN product_codes p ON n.Product_1 = p.code WHERE EXTRACT(YEAR FROM n.Treatment_Date) = 2025 GROUP BY p.title ORDER BY estimated_total_injuries DESC LIMIT 10;`
  - SQL: `DESCRIBE product_codes;`
  - SQL: `SELECT p.product_title AS product_name, COUNT(*) AS er_visits, SUM(n.Weight) AS estimated_total_injuries FROM neiss n JOIN product_codes p ON n.Product_1 = p.product_code WHERE EXTRACT(YEAR FROM n.Treatment_Date) = 2025 GROUP BY p.product_title ORDER BY estimated_total_injuries DESC LIMIT 10;`
- **Run 2: wrong: Product_1 only, weighted (floors 2,199,379)**
  - Answer: Based on the 2025 NEISS emergency department injury data, here are the top products (primary associated products) sending the most people to the ER, ranked by national weighted injury estimates: 1. **Floors or flooring materials** (Code 1807): ~2,199,378 estimated injuries 2. **Stairs or steps** (Code 1842): ~1,228,377 estimated injuries 3. **Beds or bedframes** (Code 4076): ~999,656 estimated inj ...
  - SQL: `SELECT p.title AS product_name, t.product_code, COUNT(*) AS injury_count FROM neiss t JOIN product_codes p ON t.product_code = p.code WHERE EXTRACT(YEAR FROM t.treatment_date) = 2025 GROUP BY t.product_code, p.title ORDER BY injury_count DESC LIMIT 15;`
  - SQL: `DESCRIBE neiss;`
  - SQL: `SELECT p.title AS product_name, t.Product_1 AS product_code, COUNT(*) AS sample_count, SUM(t.Weight) AS estimated_injuries FROM neiss t JOIN product_codes p ON t.Product_1 = p.code WHERE EXTRACT(YEAR FROM t.Treatment_Date) = 2025 GROUP BY t.Product_1, p.title ORDER BY estimated_injuries DESC LIMIT 1`
  - SQL: `DESCRIBE product_codes;`
  - SQL: `SELECT p.product_title AS product_name, t.Product_1 AS product_code, COUNT(*) AS sample_count, ROUND(SUM(t.Weight), 1) AS estimated_injuries FROM neiss t JOIN product_codes p ON t.Product_1 = p.product_code WHERE EXTRACT(YEAR FROM t.Treatment_Date) = 2025 GROUP BY t.Product_1, p.product_title ORDER `
- **Run 3: wrong: Product_1 only, weighted (floors 2,199,379)**
  - Answer: Based on the 2025 NEISS emergency department data, here are the top products/consumer environments sending the most people to the ER (ranked by estimated national injury totals using NEISS statistical weights): 1. **Floors or flooring materials** (Code 1807): ~2,199,378 estimated injuries (47,267 reported cases) 2. **Stairs or steps** (Code 1842): ~1,228,377 estimated injuries (29,531 reported cas ...
  - SQL: `SELECT n.product_code, p.title AS product_name, COUNT(*) AS er_visits, ROUND(SUM(n.weight), 1) AS estimated_total_injuries FROM neiss n LEFT JOIN product_codes p ON n.product_code = p.code WHERE EXTRACT(YEAR FROM CAST(n.treatment_date AS DATE)) = 2025 GROUP BY n.product_code, p.title ORDER BY estima`
  - SQL: `SELECT column_name FROM information_schema.columns WHERE table_name = 'neiss';`
  - SQL: `SELECT n.Product_1 AS product_code, p.title AS product_name, COUNT(*) AS reported_cases, ROUND(SUM(n.Weight), 1) AS estimated_total_injuries FROM neiss n LEFT JOIN product_codes p ON CAST(n.Product_1 AS VARCHAR) = CAST(p.code AS VARCHAR) WHERE EXTRACT(YEAR FROM CAST(n.Treatment_Date AS DATE)) = 2025`
  - SQL: `SELECT column_name FROM information_schema.columns WHERE table_name = 'product_codes';`
  - SQL: `SELECT column_name, table_name FROM information_schema.columns WHERE column_name ILIKE '%code%';`
  - SQL: `SELECT * FROM product_codes LIMIT 2;`
  - SQL: `SELECT n.Product_1 AS product_code, p.product_title AS product_name, COUNT(*) AS reported_cases, ROUND(SUM(n.Weight), 1) AS estimated_total_injuries FROM neiss n LEFT JOIN product_codes p ON CAST(n.Product_1 AS VARCHAR) = CAST(p.product_code AS VARCHAR) WHERE EXTRACT(YEAR FROM CAST(n.Treatment_Date `
- **Run 4: wrong: Product_1 only, weighted (floors 2,199,379)**
  - Answer: Based on the NEISS data for 2025, the products (or associated categories) that sent the most people to the emergency room, measured by estimated total injuries (accounting for statistical weights), are: 1. **Floors or flooring materials** (Product Code 1807): ~2,199,378 estimated injuries (47,267 sample cases) 2. **Stairs or steps (excluding pull-down and folding stairs)** (Product Code 1842): ~1, ...
  - SQL: `SELECT min(t.treatment_date), max(t.treatment_date) FROM neiss t WHERE t.treatment_date >= '2025-01-01'`
  - SQL: `SELECT min(treatment_date), max(treatment_date) FROM neiss`
  - SQL: `SELECT extract(year from treatment_date) as yr, count(*) FROM neiss GROUP BY yr ORDER BY yr DESC`
  - SQL: `SELECT product_code, count(*) as injury_count FROM neiss WHERE extract(year from treatment_date) = 2025 GROUP BY product_code ORDER BY injury_count DESC LIMIT 10`
  - SQL: `PRAGMA table_info('neiss')`
  - SQL: `SELECT * FROM neiss LIMIT 1`
  - SQL: `SELECT * FROM product_codes LIMIT 1`
  - SQL: `SELECT n.Product_1 AS product_code, pc.product_title, COUNT(*) AS sample_count, ROUND(SUM(n.Weight), 1) AS estimated_injuries FROM neiss n LEFT JOIN product_codes pc ON CAST(n.Product_1 AS VARCHAR) = CAST(pc.product_code AS VARCHAR) WHERE EXTRACT(YEAR FROM n.Treatment_Date) = 2025 GROUP BY n.Product`
- **Run 5: wrong: Product_1 only, weighted (floors 2,199,379)**
  - Answer: Based on the 2025 NEISS emergency-department injury records, the products (and general categories/activities) sending the most people to the ER—measured by estimated national injuries (`estimated_injuries`) using CPSC sample weights—are: 1. **Floors or flooring materials** (approx. 2,199,378 estimated injuries / 47,267 reported cases) 2. **Stairs or steps (excluding pull-down and folding stairs)** ...
  - SQL: `SELECT p.title AS product_name, COUNT(*) AS er_visits FROM neiss n JOIN product_codes p ON n.product_code = p.code WHERE n.year = 2025 GROUP BY p.title ORDER BY er_visits DESC LIMIT 10;`
  - SQL: `PRAGMA table_info('neiss');`
  - SQL: `SELECT * FROM neiss LIMIT 1;`
  - SQL: `SELECT p.title AS product_name, COUNT(*) AS er_visits, SUM(n.Weight) AS estimated_injuries FROM neiss n JOIN product_codes p ON CAST(n.Product_1 AS VARCHAR) = CAST(p.code AS VARCHAR) WHERE n.Treatment_Date >= '2025-01-01' AND n.Treatment_Date < '2026-01-01' GROUP BY p.title ORDER BY estimated_injuri`
  - SQL: `PRAGMA table_info('product_codes');`
  - SQL: `SELECT * FROM product_codes LIMIT 1;`
  - SQL: `SELECT p.product_title AS product_name, COUNT(*) AS reported_cases, SUM(n.Weight) AS estimated_injuries FROM neiss n JOIN product_codes p ON n.Product_1 = p.product_code WHERE n.Treatment_Date >= '2025-01-01' AND n.Treatment_Date < '2026-01-01' GROUP BY p.product_title ORDER BY estimated_injuries DE`
  - SQL: `SELECT MIN(Treatment_Date) AS min_date, MAX(Treatment_Date) AS max_date, COUNT(*) AS total_records FROM neiss WHERE Treatment_Date >= '2025-01-01';`

### detective

- **Run 1: correct: any slot, weighted (floors 3,079,996)**
  - Answer: In 2025, the products associated with the highest estimated injury-related ER visits were floors or flooring materials, with an estimated 3.08 million visits (from 70,650 sample cases), followed by stairs or steps with about 1.31 million estimated visits (31,688 sample cases), and beds or bedframes (other or not specified) with about 1.13 million estimated visits (28,828 sample cases). Rounding ou ...
  - SQL: `SELECT rank, product_code, product_title, n_cases, ROUND(est_visits) AS est_visits, low_sample FROM v_top_products WHERE year = 2025 AND age_group = 'all' AND rank <= 10 ORDER BY rank;`
  - SQL: `SELECT product_code, product_title, n_cases, ROUND(est_visits) AS est_visits, low_sample FROM v_top_products WHERE year = 2025 AND age_group = 'all' ORDER BY rank LIMIT 10;`
- **Run 2: correct: any slot, weighted (floors 3,079,996)**
  - Answer: In 2025, the products associated with the highest estimated injury-related ER visits are: 1. Floors or flooring materials: about 3.08 million estimated injury-related ER visits (70,650 sample cases) 2. Stairs or steps: about 1.31 million estimated injury-related ER visits (31,688 sample cases) 3. Beds or bedframes, other or not specified: about 1.13 million estimated injury-related ER visits (28,8 ...
  - SQL: `SELECT rank, product_code, product_title, n_cases, ROUND(est_visits) AS est_visits, low_sample FROM v_top_products WHERE year = 2025 AND age_group = 'all' AND rank <= 10 ORDER BY rank;`
  - SQL: `SELECT product_code, product_title, n_cases, ROUND(est_visits) AS est_visits FROM v_top_products WHERE year = 2025 AND age_group = 'all' ORDER BY rank LIMIT 10;`
- **Run 3: correct: any slot, weighted (floors 3,079,996)**
  - Answer: In 2025, the products associated with the highest estimated injury-related ER visits are: 1. Floors or flooring materials: about 3.08 million estimated injury-related ER visits (70,650 sample cases) 2. Stairs or steps: about 1.31 million estimated injury-related ER visits (31,688 sample cases) 3. Beds or bedframes, other or not specified: about 1.13 million estimated injury-related ER visits (28,8 ...
  - SQL: `SELECT rank, product_code, product_title, n_cases, ROUND(est_visits) AS est_visits, low_sample FROM v_top_products WHERE year = 2025 AND age_group = 'all' AND rank <= 10 ORDER BY rank;`
  - SQL: `SELECT product_code, product_title, n_cases, ROUND(est_visits) AS est_visits FROM v_top_products WHERE year = 2025 AND age_group = 'all' ORDER BY rank LIMIT 10;`
