# README cases: plain run_sql agent vs Product-Injury Detective

Generated 2026-11-08 21:02 UTC by `build/run_readme_cases.py`, model `vertex_ai/gemini-3.5-flash-lite`.
Plain = minimal prompt, raw `neiss` view (original file columns),
`product_codes` titles, run_sql only, no guardrails. Each case isolates
one cause. Raw JSON traces are next to this file.

## under5_age_filter

**Question:** How many ER visits were there for children under 5 in 2025?

**Verified answer:** 1,469,070 estimated visits (57,638 sample cases); Age < 5 gives 856,149

### plain

**Answer:**

> There were **33,344** emergency room visits for children under 5 in 2025 recorded in the NEISS database.

**Tool calls:**

1. `run_sql` (Plain SQL agent)

   ```sql
   SELECT count(*) AS er_visits_under_5_2025 FROM neiss WHERE age < 5 AND strftime(treatment_date, '%Y') = '2025';
   ```
   Result: `{"columns": ["er_visits_under_5_2025"], "rows": [[33344]], "truncated": false}`


### detective

**Answer:**

> In 2025, there were an estimated 1,469,070 emergency room visits (based on 57,638 sample cases) for children under 5 across all products in the NEISS data.

**Tool calls:**

1. `run_sql` (Product-Injury Detective)

   ```sql
   SELECT COUNT(*) AS n_cases, ROUND(SUM(weight)) AS est_visits FROM neiss_cases WHERE year = 2025 AND age_years < 5
   ```
   Result: `{"columns": ["n_cases", "est_visits"], "rows": [[57638, 1469070.0]], "truncated": false}`


## product_ranking_weights

**Question:** Which products send the most people to the ER in 2025?

**Verified answer:** Floors 3,079,996; stairs 1,310,287; beds 1,133,481 (weighted, any slot)

### plain

**Answer:**

> Based on the 2025 NEISS emergency department injury data, here are the top 10 products/product categories sending the most people to the ER (ranked by estimated national injuries based on sample weights):
> 
> 1. **Floors or flooring materials** (Code 1807): ~2,199,379 injuries
> 2. **Stairs or steps** (Code 1842): ~1,228,377 injuries
> 3. **Beds or bedframes** (Code 4076): ~999,656 injuries
> 4. **Chairs** (Code 4074): ~433,183 injuries
> 5. **Bicycles or accessories** (Code 5040): ~412,756 injuries
> 6. **Bathtubs or showers** (Code 611): ~369,916 injuries
> 7. **Exercise (activity/apparel without equipment)** (Code 3299): ~361,889 injuries
> 8. **Basketball** (Code 1205): ~358,220 injuries
> 9. **Knives, nec** (Code 464): ~342,503 injuries
> 10. **Football** (Code 1211): ~307,911 injuries

**Tool calls:**

1. `run_sql` (Plain SQL agent)

   ```sql
   
   SELECT 
       p.title AS product_name,
       t.product_code,
       COUNT(*) AS injury_count,
       ROUND(SUM(t.weight), 1) AS estimated_total_injuries
   FROM neiss t
   JOIN product_codes p ON t.product_code = p.product_code
   WHERE t.year = 2025
   GROUP BY t.product_code, p.title
   ORDER BY estimated_total_injuries DESC
   LIMIT 10;
   ```
   Result: `{"error": "Binder Error: Values list \"t\" does not have a column named \"product_code\""}`

2. `run_sql` (Plain SQL agent)

   ```sql
   
   DESCRIBE neiss;
   ```
   Result: `{"columns": ["column_name", "column_type", "null", "key", "default", "extra"], "rows": [["CPSC_Case_Number", "BIGINT", "YES", null, null, null], ["Treatment_Date", "DATE", "YES", null, null, null], ["Age", "SMALLINT", "YES", null, null, null], ["Sex", "SMALLINT", "YES", null, null, null], ["Race", "SMALLINT", "YES", null, null, null], ["Other_Race", "VARCHAR", "YES", null, null, null], ["Hispanic" ...`

3. `run_sql` (Plain SQL agent)

   ```sql
   
   SELECT 
       p.title AS product_name,
       t.Product_1 AS product_code,
       COUNT(*) AS sample_count,
       ROUND(SUM(t.Weight), 1) AS estimated_total_injuries
   FROM neiss t
   JOIN product_codes p ON t.Product_1 = p.product_code
   WHERE EXTRACT(YEAR FROM t.Treatment_Date) = 2025
   GROUP BY t.Product_1, p.title
   ORDER BY estimated_total_injuries DESC
   LIMIT 10;
   ```
   Result: `{"error": "Binder Error: Values list \"p\" does not have a column named \"title\""}`

4. `run_sql` (Plain SQL agent)

   ```sql
   
   DESCRIBE product_codes;
   ```
   Result: `{"columns": ["column_name", "column_type", "null", "key", "default", "extra"], "rows": [["product_code", "INTEGER", "YES", null, null, null], ["product_title", "VARCHAR", "YES", null, null, null]], "truncated": false}`

5. `run_sql` (Plain SQL agent)

   ```sql
   
   SELECT 
       p.product_title AS product_name,
       t.Product_1 AS product_code,
       COUNT(*) AS sample_count,
       ROUND(SUM(t.Weight), 1) AS estimated_total_injuries
   FROM neiss t
   JOIN product_codes p ON t.Product_1 = p.product_code
   WHERE EXTRACT(YEAR FROM t.Treatment_Date) = 2025
   GROUP BY t.Product_1, p.product_title
   ORDER BY estimated_total_injuries DESC
   LIMIT 10;
   ```
   Result: `{"columns": ["product_name", "product_code", "sample_count", "estimated_total_injuries"], "rows": [["Floors or flooring materials", 1807, 47267, 2199378.8], ["Stairs or steps (excluding pull-down and folding stairs)", 1842, 29531, 1228377.1], ["Beds or bedframes, other or not specified", 4076, 25989, 999656.2], ["Chairs, other or not specified", 4074, 10818, 433183.1], ["Bicycles or accessories (e ...`

6. `run_sql` (Plain SQL agent)

   ```sql
   
   SELECT 
       MIN(Treatment_Date) AS min_date, 
       MAX(Treatment_Date) AS max_date,
       COUNT(*) AS total_records
   FROM neiss
   WHERE EXTRACT(YEAR FROM Treatment_Date) = 2025;
   ```
   Result: `{"columns": ["min_date", "max_date", "total_records"], "rows": [["2025-01-01", "2025-12-31", 410201]], "truncated": false}`


### detective

**Answer:**

> In 2025 (all ages), the everyday products associated with the highest estimated emergency room visits in the US are:
> 
> 1. **Floors or flooring materials**: About 3.08 million estimated ER visits (70,650 sample cases).
> 2. **Stairs or steps**: About 1.31 million estimated ER visits (31,688 sample cases).
> 3. **Beds or bedframes (other or not specified)**: About 1.13 million estimated ER visits (28,828 sample cases).
> 4. **Chairs (other or not specified)**: About 496,221 estimated ER visits (12,293 sample cases).
> 5. **Bicycles or accessories**: About 420,962 estimated ER visits (10,644 sample cases).
> 6. **Ceilings and walls (interior)**: About 419,266 estimated ER visits (10,683 sample cases).
> 7. **Bathtubs or showers**: About 418,075 estimated ER visits (9,767 sample cases).
> 8. **Tables (not elsewhere classified)**: About 382,897 estimated ER visits (10,370 sample cases).
> 9. **Exercise (activity or apparel without equipment)**: About 382,874 estimated ER visits (8,982 sample cases).
> 10. **Basketball (activity, apparel or equipment)**: About 365,706 estimated ER visits (10,522 sample cases).
> 
> *Note: These figures represent national estimates weighted from participating hospital emergency departments. They reflect the product categories involved in ER visits, not a direct measure of product risk or causation per use.*

**Tool calls:**

1. `run_sql` (Product-Injury Detective)

   ```sql
   SELECT rank, product_code, product_title, n_cases, ROUND(est_visits) AS est_visits, low_sample
   FROM v_top_products
   WHERE year = 2025 AND age_group = 'all' AND rank <= 10
   ORDER BY rank;
   ```
   Result: `{"columns": ["rank", "product_code", "product_title", "n_cases", "est_visits", "low_sample"], "rows": [[1, 1807, "Floors or flooring materials", 70650, 3079996.0, false], [2, 1842, "Stairs or steps (excluding pull-down and folding stairs)", 31688, 1310287.0, false], [3, 4076, "Beds or bedframes, other or not specified", 28828, 1133481.0, false], [4, 4074, "Chairs, other or not specified", 12293, 4 ...`

