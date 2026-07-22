# Running & deploying the web app

The web app is a FastAPI backend (`backend/`) that simulates a live iMet-X4
flight feed and serves it as JSON/PNG, and a React + Vite frontend
(`frontend/`) that polls it. Both reuse the plotting/analysis code in
`framework/` — the original `quickstart.py` / Dash dashboard / GitHub Pages
export are untouched.

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
`http://localhost:8000`, so no env vars are needed for local dev.

## Deploying to Render

`render.yaml` at the repo root defines two Render Web Services:

- **`atmos-lab-backend`** — Python runtime, runs the FastAPI app with
  `--workers 1` (required: the simulator's ring buffer lives in one
  process's memory).
- **`atmos-lab-frontend`** — static runtime, builds the Vite app and serves
  `frontend/dist`, with an SPA rewrite so client-side routing/tabs work.

To deploy:

1. Push this branch to GitHub.
2. In the Render dashboard: **New > Blueprint**, point it at this repo. Render
   will read `render.yaml` and create both services.
3. Render assigns each service a URL of the form
   `https://<service-name>.onrender.com`. `render.yaml` already wires the
   two services together assuming the default names
   (`atmos-lab-backend` / `atmos-lab-frontend`) are available on your
   account. **If Render had to rename either service** (e.g. the name was
   taken), the cross-referenced URLs will be wrong:
   - Update `FRONTEND_ORIGIN` on `atmos-lab-backend` to the frontend's
     actual URL.
   - Update `VITE_API_BASE_URL` on `atmos-lab-frontend` to the backend's
     actual URL, then trigger a manual redeploy of the frontend (Vite bakes
     env vars in at build time, so this won't take effect until rebuilt).
4. Both services are on the free plan — they spin down after 15 minutes of
   inactivity and take ~30-60s to wake back up on the next request.

No commits/pushes were made as part of this change; push to GitHub yourself
when you're ready to deploy.
