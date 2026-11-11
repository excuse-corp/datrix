```text
██████╗  █████╗ ████████╗██████╗ ██╗██╗  ██╗
██╔══██╗██╔══██╗╚══██╔══╝██╔══██╗██║╚██╗██╔╝
██║  ██║███████║   ██║   ██████╔╝██║ ╚███╔╝
██║  ██║██╔══██║   ██║   ██╔══██╗██║ ██╔██╗
██████╔╝██║  ██║   ██║   ██║  ██║██║██╔╝ ██╗
╚═════╝ ╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═╝╚═╝╚═╝  ╚═╝

Agentic Data Intelligence Workbench
```

<p align="center">
  <strong>Datrix is a data intelligence workbench rebuilt from the DB-GPT open-source project.</strong><br />
  <strong>Slogan: Turn every question into an auditable data answer.</strong><br />
  It is built for enterprise analytics workflows where agents understand business scenarios, query governed data, run analysis, and deliver traceable insights.
</p>

## Project Origin

Datrix is a downstream transformation of the [DB-GPT](https://github.com/eosphoros-ai/DB-GPT) open-source project. It keeps DB-GPT's strengths in database connectivity, knowledge bases, agents, tool execution, code execution, and private deployment, then extends them into a scenario-oriented data intelligence platform for real business analysis.

The compatibility CLI is still named `dbgpt` while package and command migration is completed.

## What Datrix Does

- **Multi-agent data querying**: planning, scenario routing, SQL generation, SQL execution, validation, and summarization agents work together to turn natural-language questions into verifiable data answers.
- **Scenario-based analytics**: business scenarios package data dictionaries, semantic documents, metric definitions, dimensions, and query boundaries so AI works in the right business context.
- **Ontology-powered analysis**: Ontology models entities, metrics, dimensions, business rules, and cross-scenario relationships, helping agents understand data meaning, discover related paths, and explain results consistently.
- **Natural-language SQL**: users ask questions in natural language while AI interprets intent, selects tables or views, writes SQL, and executes it within controlled boundaries.
- **Code-driven analysis**: Python and code-based workflows support data cleaning, statistical analysis, modeling, file processing, and reproducible computation.
- **Reusable Skills and Plugins**: reusable Skills and Plugins capture common analysis methods, business templates, connectors, automation flows, and domain playbooks.
- **Automated visualization and reporting**: Datrix can generate charts, dashboards, HTML reports, and analysis summaries directly from query results.
- **Sandboxed execution**: analysis tasks run in a sandboxed environment to isolate code execution and reduce automation risk.

## Core Workflow

1. A user asks a business question in natural language.
2. The main agent plans the analysis path and routes the request to matching business scenarios, tools, Skills, or Plugins.
3. Scenario agents read semantic documents, data dictionaries, Ontology context, and workspace state to generate constrained actions.
4. Execution agents run SQL, Python, shell, connector, or tool-based analysis tasks inside controlled environments.
5. Summarization agents validate results, generate charts or reports, and return an auditable conclusion.

## Quick Start

`dbgpt start webserver` starts the Datrix backend WebServer. The WebServer runs the FastAPI / Uvicorn API service and serves the already-built static frontend from the backend package; it is not a standalone frontend dev server.

On a fresh clone, install the web dependencies and build the static frontend before starting the backend. The generated backend assets are ignored build output and are not included in a fresh checkout:

```bash
cd web
npm ci
cd ..
./scripts/build_web_static.sh
```

The CLI name still follows DB-GPT for compatibility during the package and command migration.

For a new Linux or macOS server, you can use the setup script from a terminal. It downloads the Datrix source, installs the Python and web dependencies, builds the frontend, and opens the model setup wizard. This is a shell script, not a separate desktop application. Git, curl, Node.js, and npm must already be installed; Docker is not required for this route.

```bash
curl -fsSL https://raw.githubusercontent.com/excuse-corp/datrix/main/scripts/install/install.sh | bash
```

The script prints the command to start Datrix when setup finishes.

For a source install with `uv`, install the backend dependencies from the repository root:

```bash
uv sync --all-packages --extra base --extra proxy_openai --extra rag --extra storage_chromadb --extra dbgpts
```

Copy the environment template, set a valid model provider API key, and load it before starting:

```bash
cp configs/.env.ask-data.example configs/.env.ask-data
# Edit configs/.env.ask-data, then load its values into this shell.
set -a
source configs/.env.ask-data
set +a
```

Then start the backend:

```bash
uv run dbgpt start webserver --config configs/dataman-runtime.toml
```

If you are using this repository's `dataman` conda environment directly, run:

```bash
conda run -n dataman dbgpt start webserver --config configs/dataman-runtime.toml --yes
```

For the managed local development stack, including database startup and frontend static asset build, use:

```bash
cp docker/dataman-postgres/.env.example docker/dataman-postgres/.env
cp configs/.env.ask-data.example configs/.env.ask-data
scripts/start_dataman_services.sh
```

Set the passwords and provider credentials in those local environment files before starting. This managed stack also requires the `dataman` conda environment; it builds the frontend automatically, but `web/node_modules` must already exist. Install it with `cd web && npm ci` first.

This stack uses Docker for PostgreSQL and the `dataman` conda environment for the Datrix backend. The sandbox can also use Docker, Podman, or Nerdctl to run isolated code sessions. The repository does not provide a Datrix application Docker image or an all-in-one Compose deployment; old upstream DB-GPT deployment files have been removed from the tracked project.

## Positioning

Datrix focuses on business-facing data intelligence: it preserves DB-GPT's data and agent foundations, then adds scenario context, reusable capabilities, governed execution, and traceable outputs for enterprise analytics.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
