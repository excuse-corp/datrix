# Ask-data configuration

The ask-data module uses two configuration layers:

1. A DB-GPT profile such as `dbgpt-proxy-siliconflow.toml` for the web server,
   metadata database, model provider, and RAG storage.
2. `ask-data.example.toml` for ask-data limits, Snapshot/Registry settings, and
   the bound business data source.

Copy `.env.ask-data.example` to `.env.ask-data` and fill in credentials locally.
The example env file is tracked; the copied file is ignored by Git.

The current development path is **Scheme A**: `dataman` connects read-only to
the SQL Server `ecology` database and queries
`dbo.vw_xmk_project_contract_report` directly.  No local business-data copy or
daily ETL is enabled yet.  Scheme B will add a separate sync configuration and
must not change the Scene/Snapshot query contract.

The ask-data package profile intentionally keeps only the capabilities required
by the plan: Datrix web/API, controlled agents, SQLAlchemy/data-source support,
RAG/Chroma, OpenAI-compatible model access, and the SQL Server driver.  Legacy
Excel/chat apps, free-form code execution, shell sandboxing, and unused Tongyi
or Zhipu provider extras are disabled.  The commented legacy app examples in
`dbgpt-app-config.example.toml` can be restored for unrelated DB-GPT use cases.

## React Agent integration

Set `DBGPT_REACT_ASK_DATA_TOOL_ENABLED=true` to expose the constrained
`ask_data_query` tool inside Datrix's original React chat page. The Agent may
select this tool for authorized business-data questions, while AskData still
enforces Scene access, readonly execution, query validation, and audit logs.
The Agent never receives SQL, physical table metadata, connection settings, or
Snapshot IDs from this tool. Set the variable to `false` for an immediate
rollback to the legacy generic-chat tool set.

`max_parallel_agents_per_query` limits both SceneQueryAgent planning and
controlled SQL execution for a single request. A multi-scene request is allowed
only for explicitly named authorized scenes; every Scene still receives only
its own Snapshot and two private Markdown documents.

Before starting the service, verify that the host running `dataman` can reach
`ECOLOGY_DB_HOST:ECOLOGY_DB_PORT` and that the account has only read access to
the configured view.

The non-secret prerequisite check is:

```bash
python scripts/ask_data_preflight.py \
  --config configs/ask-data.example.toml \
  --env-file configs/.env.ask-data
```

Use `--skip-network` when validating only environment variables and TOML
syntax. The command never prints database passwords or tokens.

## Validation commands

Run backend checks in the `dataman` Conda environment:

```bash
conda run -n dataman python -m pytest packages/dbgpt-app/src/dbgpt_app/tests/scene/ask_data packages/dbgpt-app/src/dbgpt_app/tests/test_ask_data_router_registration.py -q
conda run -n dataman python -m pytest packages/dbgpt-app/src/dbgpt_app/tests -q
conda run -n dataman python -m ruff check packages/dbgpt-app/src/dbgpt_app/scene/ask_data
```

Run frontend checks from `web/` without regenerating lock files:

```bash
npm install --package-lock=false --ignore-scripts
npx eslint pages/ask-data utils/ask-data.ts
npx tsc --noEmit --project tsconfig.ask-data.json
npm run build
```

The `tsconfig.ask-data.json` file is a local validation profile for the MVP
pages. The repository-wide TypeScript check remains affected by an existing
compiler failure in the legacy home page dependency graph.

The fixed UAT case set and metric targets are in
`configs/ask-data-uat.example.toml`. It covers success, clarification,
out-of-scope, empty results, truncation, injection rejection, and derived
multi-Scene comparison.

Run it against a deployed service and retain the release report:

```bash
python scripts/ask_data_uat.py \
  --config configs/ask-data-uat.example.toml \
  --base-url http://127.0.0.1:5670 \
  --report artifacts/ask-data-uat.json
```

The report records per-case pass/fail, query IDs, route accuracy, QuerySpec
validity, SQL rejection rate, and p95 latency.

`web/package.json` contains an npm override for `@antv/g2`'s CommonJS-compatible
`d3-array` version. This keeps npm installs aligned with the existing Yarn
`resolutions` without requiring lock-file updates.

For production authentication, initialize these application state hooks before
`configure_ask_data_runtime`:

- `app.state.ask_data_authorizer`: an `AskDataAuthorizer` policy instance.
- `app.state.ask_data_principal_resolver`: a callable receiving the FastAPI
  request and returning an `AskDataPrincipal` from the Datrix authenticated
  user. The resolver takes precedence over integration-only headers.

Without these hooks, `X-User-Id`/`X-AskData-Scenes` are only intended for local
integration testing and should not be used as production authentication.

For deployed latency and concurrency evidence, run the 2/5/10 request-level
profiles (the service must be reachable and the question must be safe):

```bash
python scripts/ask_data_perf.py \
  --question '查询本季度各部门合同金额' \
  --concurrency 2 5 10 \
  --runs 3 \
  --report artifacts/ask-data-perf.json
```

## Account and unified authentication

Set `DBGPT_AUTH_SECRET` to enable DataMan's self-managed account system. It
stores local credentials, external identity bindings, and revocable sessions in
`DBGPT_AUTH_DB`; it injects the verified internal user into
`request.state.user` for AskData authorization. Set `DBGPT_AUTH_REQUIRED=true`
to require authentication on all non-public Datrix routes.

The default `hybrid` mode permits local accounts and CAS. Configure
`DBGPT_AUTH_CAS_BASE_URL` and `DBGPT_AUTH_CAS_CALLBACK_URL` with HTTPS URLs to
enable CAS; use `DBGPT_AUTH_CAS_SUBJECT_CANDIDATES` to select the stable staff
identifier used for the external-identity binding. Keep
`DBGPT_AUTH_COOKIE_SECURE=true` outside local HTTP development.

The public endpoints are:

```bash
GET  /api/v1/auth/config
POST /api/v1/auth/local/login
GET  /api/v1/auth/sso/{provider_id}/login
POST /api/v1/auth/refresh
POST /api/v1/auth/logout
GET  /api/v1/auth/me
```

`POST /api/v1/auth/login` remains a compatibility alias for local login.

## Local Query Database

DataMan requires a dedicated PostgreSQL 16 query database for synchronized
business data. Deploy it once from the repository root:

```bash
docker compose --env-file docker/dataman-postgres/.env \
  -f docker/dataman-postgres/compose.yml up -d
```

The deployment creates an empty `dataman_data` database with these roles:

- `dataman_sync`: the third-party synchronization platform may write only to
  `sync.pcm_project_info`.
- `dataman_reader`: DataMan may read only
  `reporting.vw_information_project_contract_report`.

The default service binds to `127.0.0.1:54329`. Before connecting a third-party
platform on another server, set `DATAMAN_POSTGRES_BIND_ADDRESS` in
`docker/dataman-postgres/.env` to a trusted private-network address and allow
only that platform in the host firewall. Do not expose PostgreSQL directly to
the public internet.

Copy `DATAMAN_READER_PASSWORD` from `docker/dataman-postgres/.env` into the
untracked runtime `configs/.env.ask-data` as `DATAMAN_DATA_DB_PASSWORD`. The
source configuration in `configs/ask-data.example.toml` already binds the
`dataman_data` source to the read-only reporting view.

When adding a new third-party synchronization table and its AskData reporting
view, follow `configs/DATAMAN_DATA_SYNC_GUIDE.md`.

The information-project Scene definition is in
`dataman1111/scenes/information_project_contract_report.semantic.md`. After the
DataMan API is running, create it with:

```bash
DATAMAN_ADMIN_ACCESS_TOKEN='admin-access-token' \
python scripts/seed_information_project_scene.py
```

The seed does not activate the Scene because the synchronization table is empty.
After the third-party platform completes its first successful load, validate,
build, and activate the Scene through the AskData Scene API.

Create additional users without putting passwords in shell history:

```bash
python scripts/manage_dbgpt_auth.py analyst \
  --role normal --scene contracts
python scripts/manage_dbgpt_auth.py askdata-admin --role admin
```

Use a secret of at least 32 random characters. The bootstrap password is only
for initial setup and should be removed after the first administrator is
created. The legacy `X-User-Id`/`X-AskData-Role` headers are not a production
authentication mechanism.
