# Codex implementation plan — split admin and sales chat into two hosts

This document hands off the work to separate the meter-agent orchestrator into
two FastAPI host processes: one serving authenticated **admin** chat, one
serving public **sales** chat. It is written so a Codex (or any other) agent
can pick up without re-deriving context.

> **Read first:** [`../docs/architecture.md`](../docs/architecture.md) for the
> current single-process layout, [`../docs/developer-guide.md`](../docs/developer-guide.md)
> for module ownership, and [`../orchestrator/server/app.py`](../orchestrator/server/app.py)
> as the file most of this work touches.

---

## 1. Goal and scope

### Goal

Run admin chat and sales chat as **two independent FastAPI processes** built
from the **same Docker image**, selectable at start time. A single
`create_app()` factory drives all three deployment shapes:

| Mode | Mounts | Used for |
|------|--------|----------|
| `combined` | admin + sales + shared + SPA | current behavior; default; local dev |
| `admin` | admin + shared (+ SPA optional) | admin host in split mode |
| `sales` | sales + shared | sales host in split mode |

### Out of scope

- **No sales-agent code changes.** Sales chat behavior, prompts, tools, KB,
  verifier, and content sync stay exactly as they are. The split is a
  packaging concern, not a feature change.
- **No domain/proxy decisions.** This plan does not pick how traffic is
  routed to the two hosts in production. That is a deployment-layer concern
  and can be a reverse proxy, two services on different ports, two Railway
  services, or anything else. The plan only ensures that two processes are
  *runnable* and *correct*.
- **No DB split.** Both hosts continue to share one database. The `shares`
  table is the only cross-surface table and the existing read-by-token route
  works whichever host serves it.
- **No frontend rewrite.** The SPA continues to call `/api/conversations/...`
  and `/api/public/sales/...`. In split deployments a proxy handles routing.

### Non-goals (explicit, so these don't accidentally creep in)

- Do not move sales-only or admin-only modules out of the existing packages.
- Do not change `orchestrator/store.py`, `orchestrator/agent.py`, or
  `orchestrator/api.py` compatibility facades.
- Do not change stream-session shape, SSE event shape, or any client-facing
  HTTP path.
- Do not introduce a new auth model or rotate Auth0 config.

---

## 2. Starting state

Before this work begins:

- Branch base: `main` (or whatever the current trunk is at start time).
- All routes live as decorated handlers (`@app.<method>(...)`) directly in
  [`../orchestrator/server/app.py`](../orchestrator/server/app.py). Verified
  enumeration in §3 below.
- Stub router modules already exist under
  [`../orchestrator/server/routers/`](../orchestrator/server/routers/) but
  they only re-export handler functions from `server.app`. They are not yet
  real `APIRouter` instances.
- `admin_chat/` and `sales_chat/` packages do **not** import each other.
  Both depend down into `shared/` and `store.py`/`persistence/`.
- In-process state in [`../orchestrator/server/streams.py`](../orchestrator/server/streams.py)
  (`_streams`, `_cancel_events`, `_active_conversation_streams`,
  `_cancelled_conversations`, `_streams_lock`, `_gc_streams`) is per-process
  by design. After the split each host owns its own copy. No shared state is
  needed because admin and sales conversations are disjoint.

---

## 3. Route inventory (source of truth)

Enumerated from `orchestrator/server/app.py`. This is the canonical
admin/sales/shared classification for the rest of the plan.

### Admin-only routes (mount when `mode in ("combined", "admin")`)

| Path | Method | Handler |
|------|--------|---------|
| `/api/auth/login` | POST | `login` |
| `/api/auth/forgot-password` | POST | `forgot_password` |
| `/api/conversations` | GET | `list_conversations` |
| `/api/conversations` | POST | `create_conversation` |
| `/api/conversations/{conv_id}/messages` | GET | `get_messages` |
| `/api/conversations/{conv_id}` | DELETE | `delete_conversation` |
| `/api/conversations/{conv_id}` | PATCH | `patch_conversation` |
| `/api/conversations/{conv_id}/share` | POST | `create_conversation_share` |
| `/api/shares/{token}` | DELETE | `delete_share` |
| `/api/tickets` | GET | `list_tickets` |
| `/api/tickets` | POST | `create_ticket` |
| `/api/tickets/{ticket_id}` | PATCH | `update_ticket` |
| `/api/tickets/{ticket_id}/events` | POST | `create_ticket_event` |
| `/api/plots/{filename}` | GET | `get_plot` |
| `/api/analysis-artifacts/{filename}` | GET | `get_analysis_artifact` |
| `/api/conversations/{conv_id}/status` | GET | `conversation_status` |
| `/api/conversations/{conv_id}/cancel` | POST | `cancel_processing` |
| `/api/conversations/{conv_id}/chat` | POST | `chat_init` |
| `/api/streams/{stream_id}` | GET (SSE) | `chat_stream` |
| `/api/streams/{stream_id}/poll` | GET | `chat_stream_poll` |

### Sales-only routes (mount when `mode in ("combined", "sales")`)

| Path | Method | Handler |
|------|--------|---------|
| `/api/public/sales/conversations` | POST | `create_sales_conversation` |
| `/api/public/sales/conversations` | GET | `list_sales_conversations` |
| `/api/public/sales/conversations/{conv_id}` | GET | `get_sales_conversation` |
| `/api/public/sales/conversations/{conv_id}` | PATCH | `patch_sales_conversation` |
| `/api/public/sales/conversations/{conv_id}` | DELETE | `delete_sales_conversation` |
| `/api/public/sales/conversations/{conv_id}/status` | GET | `sales_conversation_status` |
| `/api/public/sales/conversations/{conv_id}/share` | POST | `create_sales_conversation_share` |
| `/api/public/sales/shares/{token}` | DELETE | `delete_sales_share` |
| `/api/public/sales/conversations/{conv_id}/cancel` | POST | `cancel_sales_processing` |
| `/api/public/sales/conversations/{conv_id}/chat` | POST | `sales_chat_init` |
| `/api/public/sales/streams/{stream_id}` | GET (SSE) | `sales_chat_stream` |
| `/api/public/sales/streams/{stream_id}/poll` | GET | `sales_chat_stream_poll` |

### Shared routes (mount in every mode)

| Path | Method | Why shared |
|------|--------|-----------|
| `/api/config` | GET | Public tuning values; no secrets; cheap. |
| `/api/public/shares/{token}` | GET | Token-keyed public read of either admin or sales transcripts. |
| `/api/logo` | GET | Static brand asset used by SPA in any mode. |

### SPA static fallback

`_mount_production_spa()` (the `_spa_index` and `_spa_fallback` handlers).
Mount only when `BLUEBOT_SERVE_SPA=1` (default `1` for `combined`, `1` for
`admin`, `0` for `sales`). The flag means a sales-only host returns 404 for
unknown non-`/api` paths, which is what we want behind a proxy.

---

## 4. Design decisions

### 4.1 Use `APIRouter`, not flag-gated decorators

Convert the existing decorated handlers in `app.py` into `APIRouter` blocks
in the existing stub files under `orchestrator/server/routers/`. The
factory then includes routers selectively. This is cleaner than wrapping
each `@app.<method>` in an `if mode == "admin": ...` because:

- It preserves OpenAPI grouping and tagging.
- It keeps `app.py` short — `app.py` becomes the wiring file, not the route file.
- It matches what the existing stub modules were obviously aiming at.

### 4.2 Keep all helper code in `app.py` (or co-locate with the router)

Helpers like `_gc_streams`, `_rewrite_plot_paths`, `_rewrite_artifact_urls`,
`_slim_turn_events_for_history`, `_sse_error_message`, `TurnCancelledByUser`,
and the per-process module-init blocks (TCP_NODELAY patch, secrets loader,
flow-tool stderr handler, lifespan) stay in `app.py` (or move into a
`server/_runtime.py` if `app.py` would otherwise still be too long). Routers
import what they need.

### 4.3 Mode is selected by env var, not import path

`BLUEBOT_HOST_MODE = combined | admin | sales` (default `combined`). The
factory reads this. Two thin entrypoint modules
(`server/app_admin.py`, `server/app_sales.py`) exist as a convenience for
deploy commands that prefer per-mode module paths, but the env var is the
authoritative switch.

### 4.4 Single Dockerfile, multiple `CMD`s

The Dockerfile produces one image. Two start commands diverge only by env:

```dockerfile
# default — backwards compatible
CMD ["python", "-m", "uvicorn", "orchestrator.server.app:app", "--host", "0.0.0.0", "--port", "8080"]
```

Deploy admin host with `BLUEBOT_HOST_MODE=admin`; deploy sales host with
`BLUEBOT_HOST_MODE=sales`. The image, the build, and the requirements stay
identical.

### 4.5 Streams stay per-process

No change to `orchestrator/server/streams.py`. Each host has its own
`_streams` dict. Admin streams and sales streams have always been disjoint
because their stream-id namespaces and conversation-id namespaces are
disjoint. After the split this becomes physical, not just logical.

### 4.6 Database stays shared

Both hosts read `DATABASE_URL` (or fall back to SQLite). The only table
both surfaces touch is `shares` — admin generates share tokens for admin
conversations, sales for sales conversations. The token namespace is
already disjoint. No schema change.

---

## 5. Phased plan

Each phase is a self-contained PR. Validation steps are listed per phase so
each PR can land independently.

### Phase 1 — Promote stub routers to real `APIRouter`s

**Status:** Done (2026-05-04).

**Goal:** Move the route decorators from `app.py` into the existing stub
files under `orchestrator/server/routers/`, with no behavior change.

**Files to edit:**

- `orchestrator/server/routers/auth.py`
- `orchestrator/server/routers/conversations.py`
- `orchestrator/server/routers/admin_chat.py`
- `orchestrator/server/routers/tickets.py`
- `orchestrator/server/routers/artifacts.py`
- `orchestrator/server/routers/sales_chat.py`
- `orchestrator/server/routers/shares.py`
- `orchestrator/server/app.py`

**Per router file pattern:**

```python
# orchestrator/server/routers/conversations.py
from fastapi import APIRouter, Header, Query

router = APIRouter(tags=["admin-conversations"])

@router.get("/api/conversations")
def list_conversations(user_id: str = Query(...)):
    ...

# ...other admin-conversations routes...
```

The handler bodies move verbatim from `app.py`. Imports follow the handler
to its new home. Each router file stops re-exporting handler functions; it
exports a single `router` symbol.

**`app.py` after this phase:**

- Keeps module-init blocks (TCP_NODELAY patch, secrets loader, stderr
  logger, lifespan), CORS middleware setup, helper functions, the
  `/api/config` route (or move it to a `routers/config.py`), and
  `_mount_production_spa`.
- Replaces every `@app.<method>(...)` block that moved with an
  `app.include_router(...)` call.
- The exported `app` and the path `orchestrator.server.app:app` remain
  valid, so Railway, Docker, `run_backend.sh`, and `uvicorn api:app` all
  keep working unchanged.

**Validation:**

1. `pytest -q` — all existing tests pass.
2. `./run_backend.sh --reload` — local backend boots; admin and sales
   conversations both work end to end.
3. `curl http://localhost:8000/openapi.json | jq '.paths | keys'` — every
   path in §3 above is present, exactly once.
4. No behavior diff in admin chat, sales chat, share viewer, ticket flow,
   or SPA serving.

**Acceptance:** Phase 1 ships before Phase 2 starts, even if Phase 2 is the
visible-value PR. This phase is pure refactor.

---

### Phase 2 — Add `create_app()` factory and `BLUEBOT_HOST_MODE`

**Goal:** Make router inclusion selective without changing default behavior.

**Files to edit:**

- `orchestrator/server/app.py`
- `orchestrator/.env.example` and `../.env.example` (whichever holds the
  canonical example)
- `docs/deployment.md` (add the new env var)

**Refactor sketch:**

```python
# orchestrator/server/app.py
from typing import Literal

HostMode = Literal["combined", "admin", "sales"]

def _resolve_host_mode() -> HostMode:
    raw = os.environ.get("BLUEBOT_HOST_MODE", "combined").strip().lower()
    if raw not in ("combined", "admin", "sales"):
        raise RuntimeError(f"BLUEBOT_HOST_MODE must be combined|admin|sales, got {raw!r}")
    return raw  # type: ignore[return-value]

def create_app(*, mode: HostMode | None = None, serve_spa: bool | None = None) -> FastAPI:
    resolved_mode = mode or _resolve_host_mode()
    fastapi_app = FastAPI(title="bluebot Orchestrator API", lifespan=_lifespan)
    _install_cors(fastapi_app)

    # Always-on
    fastapi_app.include_router(config_router)
    fastapi_app.include_router(public_shares_router)
    fastapi_app.include_router(logo_router)

    if resolved_mode in ("combined", "admin"):
        fastapi_app.include_router(auth_router)
        fastapi_app.include_router(admin_conversations_router)
        fastapi_app.include_router(admin_chat_router)
        fastapi_app.include_router(tickets_router)
        fastapi_app.include_router(artifacts_router)

    if resolved_mode in ("combined", "sales"):
        fastapi_app.include_router(sales_chat_router)

    if serve_spa is None:
        serve_spa = _default_serve_spa(resolved_mode)
    if serve_spa:
        _mount_production_spa(fastapi_app)

    return fastapi_app


def _default_serve_spa(mode: HostMode) -> bool:
    raw = os.environ.get("BLUEBOT_SERVE_SPA")
    if raw is not None:
        return raw.strip() == "1"
    return mode in ("combined", "admin")


app = create_app()  # backwards-compatible default
```

`_mount_production_spa()` becomes a pure function that takes a `FastAPI`
instance and registers the two SPA fallback routes on it.

**Behavioral guarantees after Phase 2:**

- `BLUEBOT_HOST_MODE` unset ⇒ identical behavior to today.
- `BLUEBOT_HOST_MODE=admin` ⇒ all admin + shared routes; no
  `/api/public/sales/...` routes; SPA served by default (set
  `BLUEBOT_SERVE_SPA=0` to disable).
- `BLUEBOT_HOST_MODE=sales` ⇒ all sales + shared routes; no admin routes;
  SPA not served by default.

**Validation:**

1. `pytest -q` — all existing tests pass with `BLUEBOT_HOST_MODE` unset.
2. New tests in `tests/orchestrator/test_host_modes.py`:
   - `combined` mode exposes every path in §3.
   - `admin` mode exposes admin + shared paths and 404s on
     `/api/public/sales/conversations`.
   - `sales` mode exposes sales + shared paths and 404s on
     `/api/conversations` and `/api/auth/login`.
   - `admin` mode SPA fallback returns 200 for `/`; `sales` mode SPA
     fallback returns 404 for `/` when `BLUEBOT_SERVE_SPA=0`.
3. `BLUEBOT_HOST_MODE=admin uvicorn orchestrator.server.app:app --port 8000`
   plus `BLUEBOT_HOST_MODE=sales uvicorn orchestrator.server.app:app --port 8001`
   run side by side against the same SQLite/Postgres without errors.

---

### Phase 3 — Convenience entrypoints and run scripts

**Goal:** Make split mode trivially launchable.

**New files:**

- `orchestrator/server/app_admin.py`
  ```python
  from orchestrator.server.app import create_app
  app = create_app(mode="admin")
  ```
- `orchestrator/server/app_sales.py`
  ```python
  from orchestrator.server.app import create_app
  app = create_app(mode="sales")
  ```

These are convenience aliases for deploy targets that prefer module paths
over env vars (e.g. `uvicorn orchestrator.server.app_admin:app`). The env
var path stays authoritative — both styles produce identical apps.

**Files to edit:**

- `run_backend.sh` — accept `--mode admin|sales|combined` (default
  `combined`). The flag sets `BLUEBOT_HOST_MODE` and optionally adjusts the
  default port (`8000` for combined and admin; `8001` for sales) if no
  `--port` is given.
- `run_project.sh` — leave unchanged; its job is local dev (combined).
- `Dockerfile` — no change to the default `CMD`. Deploys override `CMD` or
  set `BLUEBOT_HOST_MODE` via env.

**Validation:**

1. `./run_backend.sh --mode admin --reload` boots an admin-only host on
   `:8000`; `curl :8000/api/public/sales/conversations` ⇒ 404; admin chat
   works.
2. `./run_backend.sh --mode sales --reload --port 8001` boots a sales-only
   host on `:8001`; `curl :8001/api/conversations` ⇒ 404; sales chat works.
3. `./run_project.sh --reload` (combined) still works identically.

---

### Phase 4 — Tests and observability

**Goal:** Lock in the split contract.

**New tests:**

- `tests/orchestrator/test_host_modes.py` (mentioned in Phase 2; this is
  the formal home).
- A small smoke test that verifies the OpenAPI surface of each mode
  matches §3 exactly (lists the expected paths, asserts equality).

**Optional metrics hooks (recommended, not required):**

- Tag log lines emitted by `shared/observability.py` with the resolved
  host mode, so combined/admin/sales logs are distinguishable when both
  are scraped into one log destination.

**Validation:**

1. `pytest -q tests/orchestrator/test_host_modes.py` passes.
2. Manual: start `combined`, `admin`, and `sales` modes and confirm logs
   show the resolved mode at startup.

---

### Phase 5 — Documentation

**Goal:** Make the split a documented runtime mode.

**Files to edit:**

- `docs/architecture.md`
  - Add a short subsection under **Runtime shape** describing the three
    host modes and the `BLUEBOT_HOST_MODE` switch.
  - Update the **Backend architecture** table to mention that
    `orchestrator/server/` now selects routers via `create_app()`.
  - In **Scaling direction**, replace the speculative "split Sales and
    Admin into separately deployed services after the shared service has
    outgrown process-local coordination" line with a concrete pointer to
    the host-mode flag.
- `docs/deployment.md`
  - Document `BLUEBOT_HOST_MODE` and `BLUEBOT_SERVE_SPA`.
  - Add a "Split deployment" subsection: same image, two services, shared
    DB, routing handled by the deployment platform.
- `README.md`
  - Update the **Architecture** mermaid diagram only if it would
    otherwise misrepresent the split (the current diagram is accurate for
    `combined` mode, which is still the default; a one-line note is
    enough).
  - Add `BLUEBOT_HOST_MODE` to the file guide environment-variable
    pointer if there is one; otherwise leave file untouched.
- `docs/developer-guide.md`
  - Under **Mental model**, note that the FastAPI server is now a factory
    and the host mode determines which routers are mounted.

**Validation:**

1. `git diff --check` shows no whitespace damage.
2. Every link added in this phase resolves to a real file.

---

## 6. Risks and mitigations

| Risk | Mitigation |
|------|-----------|
| Phase 1 router move breaks an existing handler signature (e.g. dependency injection, header parsing) | Move handlers verbatim. Run the full pytest suite. Manual smoke test for chat, share, ticket, and Auth0 login flows. |
| `_mount_production_spa` ordering matters — SPA fallback must register **after** all `/api/*` routes | Keep the call order: `include_router(...)` then `_mount_production_spa(app)`. The `_spa_fallback` already 404s any unmatched `/api/*`. |
| A test reaches into `from server.app import some_handler` directly (the stub routers do today) | Phase 1 must update those imports as it deletes the re-exports. Search: `grep -rn "from server.app import\|from orchestrator.server.app import" tests/`. |
| Sales-only host accidentally serves admin SPA | Default `BLUEBOT_SERVE_SPA=0` for `sales` mode handles this. The SPA itself is harmless static; the protection is at the API layer. |
| Two hosts both write to `shares` and collide | Token namespace is already disjoint (admin tokens vs sales tokens). No code change required, but Phase 4 tests should include a smoke test that creates a share on each host. |
| Frontend uses one origin and now needs two | Out of scope for this plan. A reverse proxy is the standard answer. The plan changes nothing about how the SPA constructs URLs. |

---

## 7. Acceptance criteria for the full split

- `BLUEBOT_HOST_MODE` unset still produces today's behavior, byte-for-byte
  in the OpenAPI surface.
- `BLUEBOT_HOST_MODE=admin` produces a host that serves the admin route
  set in §3 plus shared routes, and 404s sales routes.
- `BLUEBOT_HOST_MODE=sales` produces a host that serves the sales route
  set in §3 plus shared routes, and 404s admin routes.
- Both split-mode hosts share one database and run side by side against
  it without contention or schema problems.
- All existing tests pass; new mode tests pass.
- `docs/architecture.md`, `docs/deployment.md`, and
  `docs/developer-guide.md` document the new host modes.
- `run_backend.sh --mode admin|sales|combined` works locally.

---

## 8. Suggested branch and PR layout

| Phase | Branch | Title | Blocking? |
|-------|--------|-------|-----------|
| 1 | `codex/split-routers` | refactor(server): promote stub routers to APIRouter | yes — Phase 2 depends on it |
| 2 | `codex/split-create-app` | feat(server): add create_app factory and BLUEBOT_HOST_MODE | yes — gates the split |
| 3 | `codex/split-entrypoints` | feat(server): add admin/sales entrypoints and run script flag | no — usable without it |
| 4 | `codex/split-tests` | test(server): host-mode contract tests | no — but ship before announcing |
| 5 | `codex/split-docs` | docs: document host-mode split | no |

Phase 1 and Phase 2 are the only phases that change runtime behavior in a
meaningful way; the rest is glue, tests, and docs. Phase 3 onward can be
landed independently or bundled.

---

## 9. Pointers for the implementing agent

- Keep all changes inside `orchestrator/server/`. Do not edit `admin_chat/`,
  `sales_chat/`, `shared/`, `persistence/`, or `store.py` for this plan.
- When in doubt about which router a route belongs to, consult §3 in this
  document. If §3 disagrees with the code, the code is the source of
  truth — update §3 in the same PR.
- The compatibility import `orchestrator.api:app` (via the `api.py` facade)
  must still work after every phase. Run `python -c "from api import app"`
  from `orchestrator/` as a smoke test.
- The TCP_NODELAY patch at module import time of `app.py` must run before
  uvicorn binds. Do not move it into `create_app`.
- The `lifespan=_lifespan` argument to `FastAPI(...)` must be preserved in
  `create_app`, otherwise `store._ensure_ready()` and the rate-limit log
  line at startup are lost.
