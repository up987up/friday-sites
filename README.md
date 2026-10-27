# friday-sites

Public host for websites Friday shares (SHARE-to-Replit). Friday pushes each approved site's static export to
`sites/<slug>/`; `server.py` (Python stdlib) serves it at `/api/sites/<slug>/` and pulls this repo every 60 s.

Only static files live here: no source, no `.env`, no keys.

Replit setup: import this repo, add Secret `GITHUB_TOKEN` (fine-grained, Contents read-only on this repo),
deploy as Autoscale. Health: `/healthz`.
