# <img src="./web/public/datrix-mark.svg" alt="Datrix" style="vertical-align: middle; height: 30px;" /> Datrix

**由智能体矩阵驱动的数据分析工具**

Datrix is an open-source data analysis platform built around an agent matrix. Specialized agents coordinate across data discovery, SQL, analysis, code execution, and reporting so teams can move from a question to an auditable answer.

## What Datrix Does

- Connects databases, files, warehouses, and knowledge sources.
- Uses coordinated agents to plan, query, analyze, validate, and present results.
- Produces SQL, code, charts, reports, and reusable skills.
- Runs analysis in controlled environments with data access boundaries.

## Quick Start

The current compatibility CLI is still named `dbgpt` while package and command migration is completed.

```bash
uv run dbgpt start webserver --config configs/dataman-runtime.toml
```

For the managed local development stack, use:

```bash
scripts/start_dataman_services.sh
```

## Brand

The name combines **Data** and **Matrix**: data intelligence produced by a matrix of collaborating agents. The supplied mark uses a D-shaped matrix of nodes, with teal for data flow and orange for agent decisions.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
