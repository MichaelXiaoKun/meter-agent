# Developer Guide and Reading Path

This guide is the handoff document for engineers and AI coding agents who are new
to the meter-agent project. It explains how to read the repository, where the
main responsibilities live, and which deeper docs or files to open for each kind
of change.

Use this as the first document after the root [`../README.md`](../README.md).

## What this project is

The meter-agent stack is a conversational product for bluebot flow-meter work.
It has one React/Vite frontend and one FastAPI orchestrator backend, with two
assistant surfaces:

| Surface | Audience | Purpose |
|---------|----------|---------|
| Sales assistant | Public prospects, buyers, installers | Explain public product fit, qualify the use case, guide the buyer toward the right Bluebot product context, and capture lead information. |
| Admin assistant | Authenticated internal users | Diagnose meters/accounts, analyze flow data, inspect status, handle pipe configuration workflows, and support operational work. |

The key architectural idea is capability separation. Sales and Admin may look and
feel similar in the UI, but they do not share the same permissions or tool set.
Sales is public and safe. Admin is authenticated and can touch protected systems.

## Mental model

Read the project as five layers:

1. **Frontend shell and shared chat UI** in [`../frontend/src/`](../frontend/src/).
   The UI owns routing, sidebars, chat rendering, stream recovery, and turn
   status display.
2. **FastAPI server** in [`../orchestrator/server/`](../orchestrator/server/).
   It owns routes, stream sessions, cancellation, request models, and production
   static-file serving.
3. **Assistant runtimes** in [`../orchestrator/admin_chat/`](../orchestrator/admin_chat/)
   and [`../orchestrator/sales_chat/`](../orchestrator/sales_chat/). These own
   prompts, tool routing, validation, and turn behavior.
4. **Shared backend helpers** in [`../orchestrator/shared/`](../orchestrator/shared/)
   and persistence in [`../orchestrator/persistence/`](../orchestrator/persistence/).
   These keep cross-cutting behavior from leaking into feature code.
5. **Specialist backend agents** in [`../data-processing-agent/`](../data-processing-agent/),
   [`../meter-status-agent/`](../meter-status-agent/), and
   [`../pipe-configuration-agent/`](../pipe-configuration-agent/). Admin tools use
   these for deeper operational workflows.

Top-level files such as [`../orchestrator/api.py`](../orchestrator/api.py),
[`../orchestrator/agent.py`](../orchestrator/agent.py), and
[`../orchestrator/store.py`](../orchestrator/store.py) are compatibility facades.
They should stay thin so old commands, imports, and tests keep working while the
real implementation remains organized in packages.

## Recommended reading order

For the first pass, read these in order:

1. [`../README.md`](../README.md): product overview, local run commands, and doc map.
2. [`architecture.md`](architecture.md): system map, repo layout, ownership
   boundaries, turn state, and scaling direction.
3. [`deployment.md`](deployment.md): local environment, ports, database choice,
   Docker, and Railway deployment assumptions.
4. [`testing.md`](testing.md): targeted test commands and coverage map.

Then branch by task:

| If your task is about... | Read next |
|--------------------------|-----------|
| Public Sales behavior | [`sales-agent.md`](sales-agent.md), [`../orchestrator/prompts/sales_system_v1.md`](../orchestrator/prompts/sales_system_v1.md), [`../orchestrator/sales_chat/agent.py`](../orchestrator/sales_chat/agent.py) |
| Sales tools, KB, or recommendations | [`../orchestrator/sales_chat/tools.py`](../orchestrator/sales_chat/tools.py), [`../orchestrator/sales_chat/verifier.py`](../orchestrator/sales_chat/verifier.py), [`../orchestrator/sales_kb/`](../orchestrator/sales_kb/) |
| Admin behavior | [`admin-agent.md`](admin-agent.md), [`../orchestrator/prompts/system_v1.md`](../orchestrator/prompts/system_v1.md), [`../orchestrator/admin_chat/`](../orchestrator/admin_chat/) |
| Frontend chat UX | [`architecture.md#frontend-architecture`](architecture.md#frontend-architecture), [`../frontend/src/features/chat/components/ChatView.tsx`](../frontend/src/features/chat/components/ChatView.tsx), [`../frontend/src/core/chatStreamReducer.ts`](../frontend/src/core/chatStreamReducer.ts), [`../frontend/src/core/turnActivity.ts`](../frontend/src/core/turnActivity.ts) |
| Public Sales UI | [`../frontend/src/features/sales/SalesChatPage.tsx`](../frontend/src/features/sales/SalesChatPage.tsx), [`../frontend/src/hooks/useSalesConversations.ts`](../frontend/src/hooks/useSalesConversations.ts) |
| Persistence | [`../orchestrator/store.py`](../orchestrator/store.py), [`../orchestrator/persistence/`](../orchestrator/persistence/) |
| Scaling or deployment | [`architecture.md#scaling-direction`](architecture.md#scaling-direction), [`deployment.md`](deployment.md), [`../Dockerfile`](../Dockerfile) |
| Failures during local work | [`troubleshooting.md`](troubleshooting.md) |

## Where to make changes

| Change area | Main files | Watch-outs |
|-------------|------------|------------|
| Sales response style or policy | [`../orchestrator/prompts/sales_system_v1.md`](../orchestrator/prompts/sales_system_v1.md), [`../orchestrator/sales_chat/agent.py`](../orchestrator/sales_chat/agent.py), [`../tests/orchestrator/test_sales_agent.py`](../tests/orchestrator/test_sales_agent.py) | Keep small talk claim-safe. Do not introduce product, compatibility, installation, connectivity, or support claims without evidence. |
| Sales tools and qualification | [`../orchestrator/sales_chat/tools.py`](../orchestrator/sales_chat/tools.py), [`../orchestrator/sales_chat/verifier.py`](../orchestrator/sales_chat/verifier.py) | Sales tools must stay public-safe and separate from Admin tools. |
| Sales content sync | [`../orchestrator/sales_chat/content_sync.py`](../orchestrator/sales_chat/content_sync.py), [`../orchestrator/sales_content_sync.py`](../orchestrator/sales_content_sync.py) | Runtime chat does not browse live websites; sync produces curated/runtime sales content. |
| Admin routing and tools | [`../orchestrator/admin_chat/`](../orchestrator/admin_chat/), [`../orchestrator/tools/`](../orchestrator/tools/), [`../tests/orchestrator/`](../tests/orchestrator/) | Admin can use protected tools, but must stay authenticated and grounded in tool outputs. |
| Shared stream/status behavior | [`../orchestrator/server/app.py`](../orchestrator/server/app.py), [`../frontend/src/core/chatStreamReducer.ts`](../frontend/src/core/chatStreamReducer.ts), [`../frontend/src/core/turnActivity.ts`](../frontend/src/core/turnActivity.ts) | Preserve the stream event shape unless you update both backend and frontend tests. |
| Shared chat UI | [`../frontend/src/features/chat/components/ChatView.tsx`](../frontend/src/features/chat/components/ChatView.tsx), [`../frontend/src/features/conversations/`](../frontend/src/features/conversations/) | Keep Sales and Admin visually aligned unless there is a product reason to diverge. |
| Database behavior | [`../orchestrator/persistence/`](../orchestrator/persistence/), [`../orchestrator/store.py`](../orchestrator/store.py) | Keep `store.py` as the stable facade. Test both behavior and migration assumptions. |
| Frontend API calls | [`../frontend/src/api/client.ts`](../frontend/src/api/client.ts), [`../orchestrator/server/`](../orchestrator/server/) | Public Sales routes live under `/api/public/sales/...`; Admin routes are protected chat routes. |

## Rules of the road

- Keep **Sales public-safe**. It can educate, qualify, and discuss Bluebot product
  fit, but it must not expose live account data, device data, MQTT workflows, or
  admin-only tools.
- Keep **Admin authenticated and tool-grounded**. Admin answers may use protected
  operational tools, but should make clear what is known from the tool output.
- Preserve **shared chat parity**. Sales and Admin should reuse the same transcript,
  composer, stream reducer, stop button behavior, and process-status timeline when
  the concept is shared.
- Put process state in **stream events and `turn_activity`**, not in final prose.
  The UI should show thinking, tool work, validation, completion, and errors.
  Assistant messages should stay user-facing.
- Do not leak internals to customers. Avoid prompt names, Python modules, tool
  names, event names, raw API details, filesystem paths, or implementation
  mechanics in assistant-facing text.
- Keep compatibility facades thin. New backend logic should usually live in
  `admin_chat`, `sales_chat`, `shared`, `server`, or `persistence`.
- Match tests to risk. Small prompt/template changes need targeted tests. Shared
  stream, persistence, route, or reducer changes need broader coverage.

## How a turn works

At a high level, both assistant surfaces use the same streaming shape:

1. The browser sends a user message and a client turn id.
2. FastAPI creates an in-memory stream session and starts the backend turn.
3. The browser consumes stream events through SSE or long-poll recovery.
4. The shared frontend reducer renders live assistant text and status updates.
5. The backend persists final messages and a compact `turn_activity` block.
6. If the user switches conversations or refreshes, the frontend reloads durable
   messages and recovers any in-flight status when possible.

This split is intentional: durable conversation state belongs in the database,
while in-flight stream sessions are currently process-local.

## Validation checklist

Use [`testing.md`](testing.md) for the complete test map. Common checks:

| Change type | Suggested validation |
|-------------|----------------------|
| Docs only | `git diff --check -- docs/README.md docs/developer-guide.md docs/architecture.md docs/sales-agent.md README.md` |
| Sales behavior | `.venv/bin/python -m pytest tests/orchestrator/test_sales_agent.py -q` |
| Backend routes/store | Target the relevant `tests/orchestrator/` tests, then broaden if persistence or stream shape changed. |
| Frontend chat reducer/UI logic | Run the relevant frontend tests from [`testing.md`](testing.md), then manually verify conversation switch, new chat, stop, refresh, and recovery behavior. |
| Deployment changes | Check [`deployment.md`](deployment.md), Docker assumptions, environment variables, and Railway-specific behavior. |

## Scaling direction

Scale the current architecture in this order:

1. Use PostgreSQL for hosted durable state.
2. Move generated artifacts to shared storage or object storage.
3. Add sticky routing or shared stream/cancellation state before horizontal API
   replicas.
4. Move heavy analysis and sales content sync into dedicated background workers.
5. Add shared rate limiting, queue-level backpressure, and observability.
6. Split Sales and Admin into separately deployed services only after traffic,
   security policy, or operations make that extra complexity worthwhile.

Until then, keeping one orchestrator, one shared stream protocol, and one shared
chat UI makes the product easier to change consistently.

## Handoff checklist

When handing this project to another developer, include:

- Current branch and latest commit.
- Whether the backend and frontend run together or through `run_backend.sh` and
  `run_frontend.sh`.
- Required `.env` values and whether the run uses SQLite or PostgreSQL.
- The exact product area being changed: Sales, Admin, shared chat UI,
  persistence, deployment, or specialist agents.
- Files already touched.
- Tests run, skipped tests, and any known failures.
- Any local ports that are already in use, usually `8000`, `5173`, or `5174`.
