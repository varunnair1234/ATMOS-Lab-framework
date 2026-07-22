# Running & deploying the web app

The web app is a FastAPI backend (`backend/`) that simulates a live iMet-X4
flight feed and serves it as JSON/PNG, and a React + Vite frontend
(`frontend/`) that polls it. Both reuse the plotting/analysis code in
`framework/` — the original `quickstart.py` / Dash dashboard / GitHub Pages
export are untouched.

In production both are served from a **single service**: FastAPI serves the
`/api/*` routes and also serves the built React app (`frontend/dist`)
directly, so only one Render service is needed. In local development they
run as two separate dev servers for hot-reload convenience.

## Local development

**Backend** (from repo root):

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Runs at `http://localhost:8000`. Check `http://localhost:8000/api/health`.

**Frontend** (in a second terminal, from repo root):

```bash
cd frontend
npm install
npm run dev
```

Runs at `http://localhost:5173`. `vite.config.js` proxies `/api/*` to
`http://localhost:8000`, so no env vars are needed for local dev. (The
backend only serves the built frontend when `frontend/dist` exists, which
it won't in this local-dev flow — that's expected, use the Vite dev server
instead.)

## Deploying to Render

`render.yaml` at the repo root defines a single Docker-runtime Web Service
(`atmos-lab`), built from the root `Dockerfile`:

1. Stage 1 builds the React app with Node (`npm ci && npm run build`).
2. Stage 2 installs the Python backend deps and copies in `framework/`,
   `backend/`, and the built `frontend/dist` from stage 1.
3. The container runs `uvicorn backend.app.main:app --host 0.0.0.0 --port
   $PORT --workers 1` (single worker — the simulator's ring buffer lives in
   one process's memory).

To deploy:

1. Push this branch to GitHub.
2. In the Render dashboard: **New > Blueprint**, point it at this repo.
   Render will read `render.yaml`, detect the Dockerfile, and build/deploy
   the one service.
3. Once live, everything is same-origin (frontend and API share one URL),
   so no CORS/env-var wiring between services is needed.
4. Free plan spins down after 15 minutes of inactivity and takes ~30-60s to
   wake back up on the next request.

If you'd rather run frontend and backend as two separate Render services
(e.g. to scale/redeploy them independently once you're off the single-project
constraint), that's also possible — ask and I can restore that setup.

No commits/pushes were made as part of this change; push to GitHub yourself
when you're ready to deploy.
