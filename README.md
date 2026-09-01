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

The CLI name still follows DB-GPT for compatibility during the package and command migration.

If your local environment is managed by `uv`, run:

```bash
uv run dbgpt start webserver --config configs/dataman-runtime.toml
```

If you are using this repository's `dataman` conda environment directly, run:

```bash
conda run -n dataman dbgpt start webserver --config configs/dataman-runtime.toml --yes
```

For the managed local development stack, including database startup and frontend static asset build, use:

```bash
scripts/start_dataman_services.sh
```

## Positioning

Datrix focuses on business-facing data intelligence: it preserves DB-GPT's data and agent foundations, then adds scenario context, reusable capabilities, governed execution, and traceable outputs for enterprise analytics.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
