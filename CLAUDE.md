# Product-Injury Detective: Agentic AI Project 2 (Data Exploration Agent)

## Project context
Three-person team (UNIs avr2141, lab2309, rl3575) in Columbia's Agentic AI course. Deliverable: a deployed data-exploration agent on GCP Cloud Run. Due Wed 2026-11-11, 11:59 PM. The topic is DECIDED. Do not re-propose topics unless you find a blocking problem, and if you do, say so plainly.

Repo: LeoBergmiller/product-injury-detective (private). Contributors: LeoBergmiller, arapinan, rainliu19.

## Topic (Text2SQL path)
Product-related injuries (NEISS) plus recalls (CPSC).
Core question: "For a given age group, which everyday products cause the most emergency-room injuries nationally, how have those changed over time, and which of those products have been recalled?"
Framing: practical for parents and caregivers, with an older-adult angle (e.g. 65+ vs. under 5).

## Data sources (one per teammate)
1. NEISS annual injury files (CPSC). One file per treatment year. Archived 2005-2024 at ICPSR/DataLumos; newer years at cpsc.gov/Research--Statistics/NEISS-Injury-Data. Fields include age, sex, race, diagnosis, body part, locale, product codes, narrative, and a sample weight. It is a probability sample of ~100 hospitals, so national estimates REQUIRE the weights. Counting rows is wrong. The narrative limit grew from 142 to 400 characters starting 2019-01-01.
2. CPSC Recalls API: https://www.saferproducts.gov/RestWebServices/Recall?format=json. REST, JSON or XML, no key, public domain. Returns every match in one array (no paging). A malformed date returns HTTP 200 with an error pseudo-recall instead of an error status.
3. SaferProducts.gov incident-report API (OData): https://www.SaferProducts.gov/WebApi/Cpsc.Cpsrms.Web.Api.svc/ . Needs an application key. Auth: send the key as the basic-auth username with a BLANK password. Limit: 100 requests per second per key. Fields include incident description, product brand/model, victim age/sex.

## Verification status (details in docs/DATA_NOTES.md; open items under "Unverified / open")
- 2025 NEISS file: exists; 2019-2025 are loaded. NEISS reuse terms: still unverified (no CPSC page we checked states a license).
- NEISS column names: verified identical for 2019-2025 (pre-2019 unverified). Product codes: titles from the January 2025 coding manual; 7 retired codes have no title; the 2024/2025 comparability tables are in docs/ but not parsed.
- NEISS -> recall crosswalk: no shared IDs, so it is text-based (build/build_xwalks.py). None of it is audited; a 60-row spot check is in docs/DATA_NOTES.md.
- Census denominators: not used. The agent reports counts, not rates, and says so.

## Secrets
- The SaferProducts key lives in .env as SAFERPRODUCTS_KEY. .env is gitignored. Never print, log, or commit the key. .env.example holds only the placeholder.
- On Cloud Run, load the key from Secret Manager or Cloud Run secrets, never from the repo.

## Rubric essentials (full text in docs/rubric.md; total 25: Basics 1, Functionality 9, Data 4, Path 7, Creativity 4)
- Use data the model doesn't know deeply, reached through tools. Combine evidence into supported claims. State when the data can't answer.
- Show work: keep the Project 1 /chat response shape. Sub-agent tool calls go in tool_calls with an "agent" field.
- Front end must say what the agent explores, who it's for, and how to use it, and must clearly differ from the starter.
- Ship data with the deploy (under 100 MB committed; otherwise load from GCS). Graders wait at most 5 minutes per request.
- Text2SQL requires an OFFLINE BUILD step (e.g. DuckDB: union yearly files, apply weights, build a product-code lookup, snapshot recalls into a table, write a data dictionary and views) plus a good table or visualization. The README must show two questions where a plain run_sql agent is wrong and ours is right, with traces.
- README: project and user, data provenance, three sample queries with expected answers and sources, and the no-tools / no-system-prompt model answers alongside.
- submission.json: deploy_url, authors, path. Repo collaborators required by the course: codeboi07, bhuvighosh3, nniishhh, x (verify the handles in the handout).

## Known traps (use for README examples and honest limits)
- Unweighted row counts vs. weighted national estimates. Small-count estimates are unstable.
- Schema and code changes across years (2019 narrative change, shifting product codes).
- NEISS products and recall products share no ID, so recalls are descriptive context, not proof of cause.
- SaferProducts reports are voluntary and not representative.
- Recalls API quirks (single-array response, error pseudo-recall on a bad date).
- Be descriptive, not causal. Always state when the data can't answer.

## Timeline
- Nov 5: pull data, verify access, record findings.
- Nov 5-7: offline build and agent.
- Nov 8-9: front end.
- Nov 9-10: deploy, README, submission.json, add course staff as collaborators.
- Nov 11: buffer only.

## Working rules
- Cite only what you verified. Mark anything unverified as such.
- Never invent column names, URLs, or API fields. Check the real files

## Repo and deploy requirements (from the handout)
- Repo root must contain: app.py, pyproject.toml, uv.lock, README.md, submission.json ({"deploy_url": ..., "authors": [UNIs], "path": "text2sql"}).
- Use uv for dependencies. Deploy to Cloud Run with continuous deploy from GitHub. Keep the service running until grades are released.
- Set a Cloud Run request timeout that allows up to 5 minutes and cap the agent's max_turns.
- Optional ambition: host data in GCS/BigQuery (queryable with DuckDB) instead of only local files.
- Non-credit peer review scores Experience & Usefulness, Risk-Taking & Ambition, and Originality & Voice, so design for a smooth first-use experience.