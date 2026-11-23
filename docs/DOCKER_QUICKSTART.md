# Run MatchLedger with Docker

Docker Desktop must be installed and its engine running. No local Python environment is needed. Run commands in the repository root.

## Sample demo: no API key

```bash
docker compose up --build -d --wait
```

Open http://127.0.0.1:8002 and follow [Dashboard checks](DASHBOARD_CHECKLIST.md). The default is **sample mode**, using bundled extracted records with real matching, reconciliation and review. It does not call an AI model.

## Live extraction

If you already have `.env`, edit it; do not overwrite your existing keys. Otherwise copy `.env.example` to `.env`. Set these values in the file:

```dotenv
MATCHLEDGER_UI_MODE=live
MATCHLEDGER_PROVIDER=openai
MATCHLEDGER_MODEL=gpt-4.1-mini
MATCHLEDGER_PROMPT=extract-v3
OPENAI_API_KEY=your-own-key
```

Keep the key private. `.env` is excluded from Git and the Docker build. It is supplied to the container at runtime. Live calls require internet access and incur provider usage charges.

```bash
docker compose up -d --force-recreate --wait
```

Confirm the sidebar says **Live extraction**, then run a new clean case and the scan review check. To return to sample mode, set `MATCHLEDGER_UI_MODE=sample` in `.env` and recreate the container.

## Stop, restart and check

```bash
docker compose ps
docker compose logs --tail=50 web
docker compose restart web
docker compose down
```

The named `cases` volume preserves uploaded sources and history across restarts, recreations and `down`. Do not use `down -v` unless you intend to delete the container's saved cases. Local Python cases are separate; do not copy your Mac database into the container because archived paths differ.

## Run automated tests in Linux

```bash
docker build --target test -t matchledger-ai:test .
docker run --rm matchledger-ai:test
```

## Teammate / grader handoff

Share the repository branch containing these files, this guide, and `docs/DASHBOARD_CHECKLIST.md`. Each person builds the image on their computer; they do not need your virtual environment or key. Until final integration is merged, use the team's integration branch, not main. Building on each machine supports both Apple Silicon and Intel without sharing a machine-specific image archive.

Port 8002 is bound to localhost for this single-operator app. If occupied, set `MATCHLEDGER_PORT=8003` in `.env`, recreate the container, and open that port instead. Docker health only establishes that the web server responds; the dashboard checks verify workflow behavior.

## Verification on November 23, 2026

- Runtime and test images built on Apple Silicon using Python 3.12/Linux.
- Container test suite: **395 passed**.
- Sample web API: clean case passed; discrepancy case progressed through $85, $34 and $0 unsupported. Original and replacement PDFs were served correctly.
- Stop/start and restart preserved revision 3, totals, history and archived source PDFs.
- Live extraction command inside Docker reached `ready_for_human_review`, with no issues, through all five stages. The web container is left in sample mode for the manual dashboard checklist.
- A teammate still needs to repeat the setup on another computer; Intel/Windows execution was not tested here.
