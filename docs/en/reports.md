# Reports

> Leer en español: [reports.md](../es/reports.md)

**Reports** shows what your agents' replies cost: a total for the period, the split by client, agent and model, the cost per day, and every reply behind those figures. It reads the usage each reply records, so the numbers are what the provider charged, not an estimate.

## What a reply records

Every AI reply leaves a row in `usage_records` with the tokens it used, what it cost as OpenRouter reported it (`cost_usd`), the vendor that served it (`served_by`, for example Azure or Google AI Studio), cached and reasoning token counts, how long the model took (`duration_ms`), and the conversation and message it belongs to. Voice-note transcriptions and image descriptions do not leave a row; only chat replies do.

Rows from before version 0.4 carry tokens only. Reports values those at the catalog's list price for the model (`apps/api/app/services/model_catalog.py`) and marks them **estimated**, on the row and in the tile that counts them.

## The page

- **Period**: last 7, 30 or 90 days, or a custom range (up to a year). Days are grouped in your browser's timezone.
- **Filters**: client, agent, model. The per-reply table also searches by contact name or conversation id.
- **Tiles**: total cost and replies, average per reply and total tokens, conversations with at least one reply, estimated replies.
- **Tables**: by client, by agent and by model, each with replies, tokens, cost and its share of the total.
- **Every reply**: date, conversation, contact, client, agent, channel, model, who served it, duration, tokens and cost, 25 per page. **Export CSV** downloads the whole period (up to 5,000 rows).
- The page refreshes on its own every 30 seconds.

## API

| Route | Returns |
| --- | --- |
| `GET /api/reports/costs?from=&to=&tz=&client_id=&agent_id=&model=` | totals, `by_client`, `by_agent`, `by_model`, `by_day` |
| `GET /api/reports/replies?from=&to=&q=&limit=&offset=` | one page of replies, newest first, with `total` |
| `GET /api/reports/replies?...&format=csv` | the period as CSV |

Both need an agency session and only ever see that agency's rows.

## Next steps

- [AI providers](ai-providers.md) — where the cost of each reply comes from.
- [Agents](agents.md) — pick the model each agent answers with.
