# Sales Agent

The sales agent is a public, pre-login assistant for prospects, buyers, installers, and other users who are still deciding whether a bluebot ultrasonic flow meter fits their site.

It should educate clearly, ask discovery questions before recommending, and end with a structured lead summary rather than only a transcript.

## Contents

- [Purpose](#purpose)
- [UI preview](#ui-preview)
- [Conversation behavior](#conversation-behavior)
- [Tools and guardrails](#tools-and-guardrails)
- [Knowledge base and product links](#knowledge-base-and-product-links)
- [API routes](#api-routes)
- [Frontend behavior](#frontend-behavior)
- [Persistence and sharing](#persistence-and-sharing)
- [Test coverage](#test-coverage)

<a id="purpose"></a>

## Purpose

The public sales assistant lives inside the existing FastAPI orchestrator and frontend. It does not run as a separate service or on a separate port.

Responsibilities:

- Explain ultrasonic flow-meter fit, installation, pipe compatibility, and non-invasive pipe impact.
- Qualify industry/application, pipe details, flow expectations, liquid type, environment, power/network availability, reporting needs, timeline, and purchasing role.
- Recommend relevant product lines from the curated local catalog when enough information is available.
- Link users to relevant bluebot pages when the KB/catalog includes reviewed URLs.
- Capture a structured lead summary that can be shared internally.

Non-responsibilities:

- No pricing automation in V1.
- No calendar booking in V1.
- No live account/device lookup.
- No pipe configuration writes.
- No MQTT or protected admin operations.

<a id="ui-preview"></a>

## UI preview

<p>
  <img src="assets/sales_agent_entrance.gif" alt="Sales assistant entrance and chat UI preview" width="760">
</p>

Full sales chat workflow:

<p>
  <img src="assets/sales_agent_chat_workflow.gif" alt="Sales assistant conversation workflow preview" width="760">
</p>

<a id="conversation-behavior"></a>

## Conversation behavior

The sales prompt lives at [`../orchestrator/prompts/sales_system_v1.md`](../orchestrator/prompts/sales_system_v1.md).

The assistant should ask for discovery before recommending:

- Industry and application.
- Pipe material, pipe size, and accessibility.
- Expected flow range.
- Water/liquid type.
- Installation environment.
- Power and network availability.
- Accuracy, reporting, and integration needs.
- Timeline and purchasing role.

It should answer educational questions first, then steer back to qualification. When confidence is low, it should name what is missing rather than guessing.

<a id="tools-and-guardrails"></a>

## Tools and guardrails

Sales-only tools live in [`../orchestrator/sales_tools.py`](../orchestrator/sales_tools.py):

| Tool | Purpose |
|------|---------|
| `search_sales_kb` | Retrieve curated product/industry/installation context. |
| `qualify_meter_use_case` | Convert user context into a qualification snapshot. |
| `assess_pipe_fit` | Reason about pipe material, size, accessibility, and fit risks. |
| `explain_installation_impact` | Explain clamp-on/non-invasive installation and pipe impact. |
| `capture_lead_summary` | Persist a structured lead object. |
| `recommend_product_line` | Recommend product-line candidates from the curated catalog. |

Sales mode must not expose live Bluebot device/account tools, flow-analysis subprocesses, pipe configuration writes, or MQTT actions. The allowlist is enforced in [`../orchestrator/sales_agent.py`](../orchestrator/sales_agent.py) and covered by tests.

<a id="knowledge-base-and-product-links"></a>

## Knowledge base and product links

Sales content is curated locally:

- [`../orchestrator/sales_kb/articles.json`](../orchestrator/sales_kb/articles.json) contains reviewed educational and product-fit content.
- [`../orchestrator/sales_kb/product_catalog.json`](../orchestrator/sales_kb/product_catalog.json) contains product-line information and reviewed links.

V1 intentionally avoids live web browsing. If bluebot.com content changes, update the curated JSON files after review. This keeps public answers deterministic and prevents unreviewed website text from flowing straight into sales recommendations.

Useful content categories:

- Product fit.
- Installation requirements.
- Pipe compatibility.
- Flow-meter education.
- Effect on pipes and non-invasive positioning.
- Network, power, and environment constraints.
- Buyer qualification questions.
- Product-line recommendation hints.

<a id="api-routes"></a>

## API routes

Public sales routes live under `/api/public/sales/...` in [`../orchestrator/api.py`](../orchestrator/api.py):

| Route | Purpose |
|-------|---------|
| `POST /api/public/sales/conversations` | Create a public sales conversation. |
| `GET /api/public/sales/conversations?ids=...` | Load known sales conversations for sidebar history. |
| `GET /api/public/sales/conversations/{id}` | Load one sales conversation. |
| `PATCH /api/public/sales/conversations/{id}` | Rename/update sales conversation metadata. |
| `DELETE /api/public/sales/conversations/{id}` | Delete a sales conversation. |
| `POST /api/public/sales/conversations/{id}/chat` | Send a sales message and create a stream. |
| `GET /api/public/sales/conversations/{id}/status` | Recover in-flight status after switching conversations or refreshing. |
| `GET /api/public/sales/streams/{stream_id}` | Stream sales events. |
| `GET /api/public/sales/streams/{stream_id}/poll` | Poll missed stream events during recovery. |
| `POST /api/public/sales/conversations/{id}/cancel` | Cancel an in-flight sales response. |
| `POST /api/public/sales/conversations/{id}/share` | Create a read-only share snapshot. |
| `DELETE /api/public/sales/shares/{token}` | Revoke a sales share link with its revoke key. |

Frontend API helpers live in [`../frontend/src/api.ts`](../frontend/src/api.ts).

<a id="frontend-behavior"></a>

## Frontend behavior

The sales UI lives in [`../frontend/src/components/SalesChatPage.tsx`](../frontend/src/components/SalesChatPage.tsx) and should visually match the admin assistant:

- Same sidebar treatment.
- Conversation history.
- New chat behavior.
- Running status.
- Disabled input while the active or another sales conversation is processing.
- Stop button.
- Share link action.
- Compact lead-summary panel once qualification data exists.

Shared UI pieces:

- [`../frontend/src/components/ChatView.tsx`](../frontend/src/components/ChatView.tsx)
- [`../frontend/src/components/Sidebar.tsx`](../frontend/src/components/Sidebar.tsx)
- [`../frontend/src/components/SharePopover.tsx`](../frontend/src/components/SharePopover.tsx)
- [`../frontend/src/turnActivity.ts`](../frontend/src/turnActivity.ts)

<a id="persistence-and-sharing"></a>

## Persistence and sharing

Conversation IDs are tracked in browser storage for sidebar restoration, but conversation bodies and summaries are persisted server-side through [`../orchestrator/store.py`](../orchestrator/store.py).

Important behavior:

- Closing and reopening the browser should restore known sales conversations when the server database still has them.
- Switching conversations should not lose the in-flight status of another sales conversation.
- Refreshing during generation should recover stream status through the public status endpoint.
- Share links are read-only snapshots; revocation requires the generated revoke key.

<a id="test-coverage"></a>

## Test coverage

Sales tests live in [`../tests/orchestrator/test_sales_agent.py`](../tests/orchestrator/test_sales_agent.py).

Covered areas include:

- Sales routing cannot call status, flow, pipe configuration, or MQTT tools.
- KB retrieval for known product/pipe questions.
- Product-line recommendation output.
- Lead qualification and summary persistence.
- Public API does not require Auth0 and does not expose protected data.
- Conversation CRUD.
- Share snapshot creation/revocation.
- Cancel and status recovery endpoints.
- SQLite volume-directory handling for Railway.
