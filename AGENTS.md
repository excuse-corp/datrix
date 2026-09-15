# Agent Guide

This file gives coding agents the project-specific context needed to work on
Datrix safely and quickly.

## Project Overview

Datrix is a data analysis platform built around coordinated agents. The current
repository still contains upstream DB-GPT package names and CLI entry points, but
the product runtime is branded and deployed as Datrix/DataMan.

## GitHub Repository

- Remote repository: `https://github.com/excuse-corp/datrix.git`.
- Preferred remote name: `datrix`.
- Do not store GitHub personal access tokens in repository files, scripts, docs,
  or command history. Use a credential helper, SSH key, or a temporary
  environment variable when authentication is required.
- Local GitHub credentials may be loaded from `.env.github.local`, which is
  ignored by `.gitignore` through the `.env*` rule. The expected variables are
  `GITHUB_REMOTE_URL`, `GITHUB_REMOTE_NAME`, and `GITHUB_TOKEN`.
- If using HTTPS with a personal access token, load the env file only for the
  shell session that needs it, and verify credentials are not present in tracked
  files before committing.
- To configure the remote from the env file:

```bash
set -a
source .env.github.local
set +a
git remote remove "$GITHUB_REMOTE_NAME" 2>/dev/null || true
git remote add "$GITHUB_REMOTE_NAME" "$GITHUB_REMOTE_URL"
```

## Deployment Shape

- Frontend: Next.js static export from `web/`.
- Static assets are copied into `packages/dbgpt-app/src/dbgpt_app/static/web`.
- Backend: DB-GPT/Datrix webserver serves the static frontend and API on port
  `7771`.
- Local service logs live under `logs/dataman-services/`.

## Common Commands

```bash
./scripts/build_web_static.sh
./scripts/start_dataman_services.sh
./scripts/stop_dataman_services.sh
./scripts/restart_dataman_services.sh
DATAMAN_SKIP_WEB_BUILD=1 ./scripts/restart_dataman_services.sh
```

Use `DATAMAN_SKIP_WEB_BUILD=1` when only Python/backend resources changed. Rebuild
static assets when editing frontend code under `web/`.

## Conda Environment

- Runtime environment name: `dataman`.
- The managed service scripts start the backend with
  `conda run --no-capture-output -n dataman dbgpt start webserver --config configs/dataman-runtime.toml --yes`.
- Prefer `conda run -n dataman ...` for backend checks so imports and package
  versions match the running service.
- Use the system shell for frontend commands under `web/`; Node dependencies live
  in the `web` project.

Useful checks:

```bash
conda run -n dataman python -V
conda run -n dataman python -m py_compile <changed-python-files>
conda run -n dataman python -m pytest packages/dbgpt-app/src/dbgpt_app/tests/scene/ask_data -q
```

## Key Areas

- Main chat / ReAct stream: `packages/dbgpt-app/src/dbgpt_app/openapi/api_v1/agentic_data_api.py`
- Built-in Agent tools: `packages/dbgpt-app/src/dbgpt_app/openapi/api_v1/tools/`
- AskData tool bridge: `packages/dbgpt-app/src/dbgpt_app/openapi/api_v1/tools/ask_data.py`
- AskData scene routing and SQL agent: `packages/dbgpt-app/src/dbgpt_app/scene/ask_data/agents/scene_query_agent.py`
- AskData API/service layer: `packages/dbgpt-app/src/dbgpt_app/scene/ask_data/`
- Main chat frontend: `web/pages/index.tsx`
- Chat execution panels: `web/new-components/chat/content/`
- Skills directory: `skills/`


## Validation Checklist

For backend-only changes:

```bash
conda run -n dataman python -m py_compile <changed-python-files>
git diff --check -- <changed-files>
DATAMAN_SKIP_WEB_BUILD=1 ./scripts/restart_dataman_services.sh
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:7771/
```

For frontend changes:

```bash
cd web
./node_modules/.bin/eslint <changed-ts-or-tsx-files>
cd ..
./scripts/build_web_static.sh
./scripts/restart_dataman_services.sh
```
