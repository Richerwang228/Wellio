# Wellio

Wellio is a personal fitness and nutrition companion that brings daily readiness, workouts, meals, and an AI assistant into one connected experience. Review your day, discuss adjustments with the assistant, and explicitly apply changes to your training plan.

## Features

- **Today:** training, recovery, sleep, and nutrition context in one daily view.
- **Workouts:** review exercises, apply proposed adjustments, track progress, and save completed sessions.
- **Nutrition:** record meals, revise portions, and undo supported changes.
- **AI assistant:** context-aware conversations and tool-backed actions through CopilotKit and OpenRouter.
- **Search and knowledge:** Exa web search and PostgreSQL/pgvector retrieval with source references.
- **Local persistence:** conversations, plans, records, and attachments stay in the local application stack.

Initial fitness and wearable inputs include sample scenarios. Live mode calls external services; it is not an offline model. Scripted scenarios remain available in the source for UI development.

## Architecture

| Component | Technology | Directory |
| --- | --- | --- |
| Web application | React, TypeScript, TanStack Start, Vite | `frontend/` |
| Business API | FastAPI, Python | `backend/wellio/` |
| Agent runtime | CopilotKit BuiltInAgent, Node.js | `backend/agent-runtime/` |
| Storage | PostgreSQL 18, pgvector, local attachments | Created locally under `data/` |
| Lifecycle management | Python and macOS command launchers | `scripts/` |

The frontend proxies business requests to FastAPI and conversations to the Node agent runtime. Model credentials are read on the server.

## Requirements

The bundled launcher targets **macOS on Apple Silicon**:

- Node.js 22.15 or newer and npm 11 or newer.
- Python 3.12 or newer.
- PostgreSQL 18 with the matching pgvector extension installed.
- OpenRouter and Exa API credentials, plus network access.
- A locally provisioned knowledge release, as described below.

PostgreSQL binaries default to `/opt/homebrew/opt/postgresql@18/bin`. Set `WELLIO_PG_BIN` in the private configuration if your installation differs. The launcher manages its own database instance; a separate global PostgreSQL service is not required.

## Installation

```bash
git clone https://github.com/Richerwang228/Wellio.git
cd Wellio
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.txt
npm --prefix backend/agent-runtime ci
npm --prefix backend/agent-runtime run build
npm --prefix frontend ci
cd frontend
VITE_WELLIO_MODE=live VITE_WELLIO_PREVIEW=0 npm run build
cd ..
cp config/runtime.env.example config/runtime.env
chmod 600 config/runtime.env
```

Edit `config/runtime.env` and fill in `OPENROUTER_API_KEY` and `EXA_API_KEY`. The default model is `deepseek/deepseek-v4.1-flash`. Never place provider keys in `VITE_*` variables or commit your private configuration.

Build before starting the application. For later rebuilds, stop this checkout's services first so the running server and its hashed assets remain consistent.

### Knowledge data prerequisite

This repository contains knowledge source metadata, collection scripts, and ingestion code. It excludes downloaded third-party articles and local vector caches, whose redistribution permissions have not been established individually.

On its first start, the standalone launcher expects `bundle.json` and `vectors.json` under:

```text
backend/.data/knowledge/nutrition-v1/releases/demo-6c1a7913caf33172c82b3482/
```

Provision that release locally from an authorized copy before using the standalone launcher. This internal release identifier is retained for compatibility. It is not the project name. The launcher imports this release when no knowledge version is active and preserves an existing active version thereafter. Without it, first startup cannot complete.

To collect and prepare your own knowledge dataset instead, see [knowledge sources and collection](backend/knowledge/README.md) and [ingestion and activation](backend/knowledge/INTEGRATION.md). A newly generated release also requires updating the release selection in `scripts/initialize_knowledge.py`; the launcher does not automatically select an arbitrary cache.

## Run Wellio

After installing dependencies, building both JavaScript components, configuring credentials, and provisioning knowledge data, double-click:

| File | Action |
| --- | --- |
| `启动.command` | Start the stack and open the application |
| `打开页面.command` | Open the running application |
| `停止.command` | Stop this checkout's services and retain data |

The application opens at **http://127.0.0.1:3120**. Closing the launcher terminal does not stop the background application.

Equivalent commands from the repository root:

```bash
backend/.venv/bin/python scripts/manage.py start --no-open
backend/.venv/bin/python scripts/manage.py status
backend/.venv/bin/python scripts/manage.py open
backend/.venv/bin/python scripts/manage.py stop
```

First startup initializes a private PostgreSQL instance under `data/pgdata/`. Runtime state, logs, database connection information, and attachments are created under `data/`. Restarts preserve records. Back up the database, its matching `data/database.json`, and attachments together.

If startup fails, inspect `data/logs/launcher.log` and `data/logs/postgres.log` locally. Port 3120 must be available.

## Development checks

```bash
npm --prefix frontend run typecheck
npm --prefix frontend test
npm --prefix backend/agent-runtime run typecheck
npm --prefix backend/agent-runtime test
```

Backend integration tests require isolated PostgreSQL resources; see [backend documentation](backend/README.md). Historical browser checks are recorded in [BROWSER-ACCEPTANCE.md](BROWSER-ACCEPTANCE.md). These records describe the original configured installation, not a fresh checkout or verification of your provider credentials.

## Repository contents and privacy

This is the source distribution of the standalone Wellio application. It includes the frontend, backend, agent runtime, assets, dependency manifests, and launch scripts. API keys, private environment files, user databases, uploads, logs, knowledge caches, installed dependencies, bundled runtimes, and generated builds are deliberately excluded. Use `config/runtime.env.example` as the configuration template.
