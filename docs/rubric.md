# Project 2: A Data Exploration Agent

Due Nov 11 by 11:59pm. 25 points. Submit the GitHub repo URL on Courseworks (one person per group). The Courseworks header says "Submitting a website url", so confirm with the instructor which URL they want; the deploy URL goes in submission.json either way.

Build an agent that explores a dataset to answer questions people actually have, and deploy it to Google Cloud Run.

## What you're building
Pick a dataset and a question to explore. Either the dataset or the question has to be unique:

- A unique dataset is one no other team uses. Maybe you found it online, scraped it yourself, or set up an account to get access to it.
- A unique exploration problem can use a well-known dataset (NYC Open Data, SEC filings, NOAA weather) if the kinds of questions you're asking are unique.

Before you start, add your dataset, question(s) you're exploring, and path to the sign-up sheet. Entries are first-come and visible to everyone. You can reuse a listed dataset only if your question domain is clearly different (and vice-versa). If you're unsure, add your entry and flag it on the Ed Discussion board as a private question.

## Your agent must
1. Use data the model doesn't already know deeply.
2. Get at the data through tools: SQL, files, search, or an API.
3. Combine evidence into a supported claim.
4. State if the data can't answer a question.
5. Show its work. Keep the /chat response shape from Project 1. Sub-agent tool calls go in tool_calls too, with an agent field.
6. Have a front end that says what the agent explores, who it's for and how to use it.
7. Be deployed to GCP Cloud Run.

## Pick a path
| Path | What you build | Reference |
|---|---|---|
| Text2SQL | A Text2SQL agent over a database/datawarehouse, with context built offline | subway-survey-text2sql-improved.zip |
| Deep Research (recommended) | A Lead that plans, Researchers that explore in parallel and a Writer that reports | wikipedia-deep-research.zip |
| Evals + Simple RAG | A RAG agent with an eval suite for your agent and two rounds of hill-climbing against it | nyc-311-agent.zip |

### Text2SQL
- You must have an offline build step that supports your agent writing SQL.
- You should nicely present the results, either a formatted table or as a data visualization.
- In the README, show two questions the plain run_sql agent got wrong and your agent gets right, with both traces.
- You can use DuckDB and local datasets, but it would be more interesting to host data in BigQuery, CloudSQL, or GCS (queryable with DuckDB).

### Deep Research
- At least three agent shapes with different jobs, for example Lead, Researcher and Writer.
- The Lead fans out to several Researchers in one turn, and they run in parallel in fresh contexts.
- The Lead goes back for more when the findings leave a gap, up to a turn cap.
- Vague input questions require clarification.
- The output is accurately cited.

### Evals
- At least 50 samples, gold or silver, with where each came from written down. None of them can also appear in your prompt as a few-shot example.
- At least two kinds of scorer: deterministic checks, a judge with a golden reference, or a judge with a rubric.
- A way to run the evals documented in the README (e.g. uv run inspect eval task.py but you may use any eval framework).
- A hill-climbing log in the README: your baseline score; two or more changes you made with the new score; which gains are bigger than the margin of error (±1/√n).

You may notice that the exact requirements are vague. This is by design. The most important part of this assignment is that you're creative with it.

## README
Each agent should come with a README.md that:
- Describes the project and who the target user is.
- Says where your data came from and how someone could load it (it's not strictly necessary that the grader can load it, just that they understand where it came from).
- Lists three sample queries for the grader to test with. For each, give the answer you expect and the file, table or API endpoint it comes from.
- For each sample query, includes the answer from the same model with no tools and no system prompt, next to your agent's answer. This will help us measure the effectiveness of how your agent uses the information even if the model already knows the domain.

## How it's graded
| Section | Points | What we assess |
|---|---|---|
| Basics | 1 | A README and a valid submission.json. |
| Functionality | 9 | Deployed and reachable. Clear purpose. Answers its research questions correctly. |
| Data | 4 | A unique dataset or problem. Data the model doesn't know. Admits what the data can't answer. |
| Path | 7 | The items for your path. |
| Creativity | 4 | A frontend changed from the starter. The agent does something interesting. |

Larger groups come with larger expectations. Here, that means the agent uses at least one data source per team member.

## How to submit
Submit your GitHub repo URL on Courseworks. One person submits per group.

### Repo
At the root of your repo:
- app.py, pyproject.toml and uv.lock
- README.md
- submission.json, listing every team member's Columbia UNI or email in authors, including your own. Additionally, include a path field stating "text2sql", "research" or "evals".

    {
      "deploy_url": "https://your-agent.example.run.app",
      "authors": ["abc1234", "xyz9876"],
      "path": "text2sql"
    }

Private repos are fine. Just add these GitHub accounts as collaborators (Settings > Collaborators > Add people): codeboi07, bhuvighosh3, nniishhh, x.

### Deployment
- Deploy to Cloud Run with continuous deploy from GitHub, following "Deploying to Cloud Run from GitHub".
- Ship the data with the deploy. Commit it if it's under GitHub's 100 MB file limit. Otherwise, load it from a GCS bucket at startup.
- Graders wait up to 5 minutes per answer. Raise Cloud Run's request timeout if you need more, and cap your agent's max_turns.
- Keep it running until grades are released.

## Peer Review Rubric (non-credit, peer style critique only)
Total: 15 points, three criteria at 5 points each.

1. Experience & Usefulness. USE THE AGENT. As a user, did this agent actually work for you? Focus on your real experience, not what you imagine it could do with more polish.
   - 5 Wow: Immediately intuitive, and the results made me think or see something differently.
   - 4 Impressed: Smooth experience, results felt genuinely useful or interesting.
   - 3 Functional: Understood it, got it working, results were okay but not memorable.
   - 2 Bumpy: Got the general idea, but struggled to get useful results.
   - 1 Lost: Couldn't figure out what it does or how to use it.
2. Risk-Taking & Ambition. READ THE CODE. Did the creator push beyond what was comfortable or expected? Ambitious failures count more than safe successes here.
   - 5 Full send: Went for something genuinely bold, even if parts are rough around the edges.
   - 4 Real swing: Took on something clearly difficult or unfamiliar and made real progress.
   - 3 Solid push: Tried something meaningfully outside their comfort zone, with mixed results.
   - 2 Toe in the water: One small stretch beyond the requirements, but mostly played it safe.
   - 1 Safe bet: Did exactly what the assignment asked, nothing more.
3. Originality & Voice. Does this project feel like the creator made it their own?
   - 5 One of a kind: Feels like no one else in the class would have built this. The choices reflect real curiosity and a distinct perspective.
   - 4 Their own thing: Clear point of view. They explored the problem and made deliberate, personal choices.
   - 3 Getting there: Some creative choices hint at a perspective behind the project.
   - 2 Light touch: One or two personal choices, but mostly follows the obvious path.
   - 1 Off the shelf: Feels like a template. Could be anyone's project.