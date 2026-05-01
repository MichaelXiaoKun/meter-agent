# meter agent frontend

React + TypeScript + Vite SPA for the bluebot meter assistant. API calls go to `/api`, which the Vite dev server proxies to the orchestrator (default `http://127.0.0.1:8000` — see `vite.config.ts`).

## Commands

```bash
npm ci          # install (CI-friendly)
npm run dev     # dev server + HMR
npm run build   # production bundle → dist/
npm run lint    # ESLint
npm run preview # serve dist locally
```

## Source layout

```text
src/
  app/                 # top-level app shell
  api/                 # typed fetch/SSE boundary
  core/                # shared domain models, reducers, and pure helpers
  features/
    auth/              # login, password reset, entry choice
    branding/          # bluebot logos and welcome visuals
    chat/              # reusable chat transcript/composer UI
    conversations/     # sidebar and conversation list
    feedback/          # toast UI
    meter-workspace/   # admin meter context panel
    sales/             # public sales assistant surface
    share/             # read-only share/export surface
    theme/             # theme provider, storage, and toggle
  hooks/               # cross-feature React hooks
  utils/               # UI/export utilities
```

Full stack setup, Auth0, env vars, Docker, and architecture: **[`../README.md`](../README.md)**.
