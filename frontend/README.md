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

Full stack setup, Auth0, env vars, Docker, and architecture: **[`../README.md`](../README.md)**.
