# acme-lead-router

A demo service for a GoHighLevel + n8n speed-to-lead build for a made-up company, Acme Solar.
n8n calls it to score a lead with Claude and to map the lead to a buyer's format. It also
stands in for the buyer's CRM, with a switch that simulates an outage.

| Endpoint | Auth | What it does |
| --- | --- | --- |
| `GET /health` | none | Liveness check |
| `POST /qualify` | `X-Service-Token` | Cleans the lead, scores it with Claude, falls back to rules if Claude fails |
| `POST /map/{buyer_id}` | `X-Service-Token` | Maps a lead to that buyer's format; 422 if a required field is missing |
| `POST /mock-buyer/{buyer_id}/leads` | `Authorization: Bearer <buyer key>` | Fake installer CRM; 503 when `X-Demo-Outage: 1` |

How the Claude call is built:

- `claude-opus-5-5` at low effort, with server-side fallbacks on.
- Only the scoring facts go to Claude: no name, email or phone.
- Structured JSON output, validated with pydantic. Tier thresholds (hot from 75, warm from 45) live in code.
- Any failure falls back to a rules score marked `source: rules`, so the lead keeps moving.

## Run it

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

Environment variables: `ANTHROPIC_API_KEY`, `SERVICE_TOKEN`, `BUYER_KEY` (see `.env.example`).
Deploys to Vercel as a FastAPI app with zero config (`vercel deploy --prod`).

## Example

```bash
curl -s -X POST https://<your-app>.vercel.app/qualify \
  -H "X-Service-Token: $SERVICE_TOKEN" -H "Content-Type: application/json" \
  -d '{"contact_id":"t1","first_name":"Maria","email":"maria@example.com","phone":"5125550101","postal_code":"78704","monthly_bill":220,"homeowner":"Yes","roof_shade":"None","timeline":"ASAP","consent":"yes"}'
```
