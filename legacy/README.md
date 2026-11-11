# Legacy archive

Upstream DB-GPT material that the Datrix/DataMan runtime, build, or scripts do
not use. It is kept on disk (not deleted) so it stays readable and recoverable.

| Path | What it is | Size | Tracked? |
|---|---|---|---|
| `docker/` | Upstream DB-GPT image builds, compose examples, datasource examples | 124K | yes |
| `docs/` | The upstream Docusaurus documentation site | 228M | no — gitignored |
| `examples/` | The upstream DB-GPT example scripts | 13M | no — gitignored |
| `scripts/` | The upstream `scripts/examples/` demo-data loaders | 12K | yes |

## `docker/`

Moved here from `docker/base/`, `docker/allinone/`, `docker/compose_examples/`,
`docker/datasources/`, and `docker/build_all_images.sh`. Small enough to keep
tracked, and git records the move as renames, so it can be put back with
`git mv` — see `docker/README.md`.

## `docs/`

The upstream DB-GPT Docusaurus site: `docs/docs/` (317 pages), `docs/i18n/zh-CN/`
(571 translated pages), `docs/static/img/` (312 images), plus the build config
(`docusaurus.config.js`, `sidebars.js`, `package.json`, `Dockerfile`,
`Dockerfile-deploy`, `nginx/`, `patchs/`, `src/`, `scripts/`).

It is **not tracked** (see the `/legacy/docs/` rule in the repository
`.gitignore`): 228M, almost all of it images, and nothing in this repository
builds or deploys it. Note that `docs/i18n/zh-CN/docusaurus-plugin-content-docs/static/img/`
(251 files, 90M) is a near-complete duplicate of `docs/static/img/`, so the site
carries roughly 90M of redundant images.

Only three files in the old `docs/` directory were written by Datrix rather than
inherited from upstream, and they stayed in the repository at `docs/`:

- `docs/agent_tool_integration_guide.md`
- `docs/design.md`
- `docs/react_agent_loop_optimization_plan.md`

Everything else moved here. The remaining upstream prose was rewritten by the
`v0.1（datrix）` commit, which renamed DB-GPT to Datrix across 369 documentation
pages; that is a text rebrand, not Datrix-specific content.

To bring the site back:

```bash
mv legacy/docs/* docs/ && mv legacy/docs/.gitignore docs/.gitignore
# then delete the `/legacy/docs/` rule from .gitignore and `git add docs/`
```

## Related

- `docs/` in the repository root is now Datrix's own documentation directory.
- `docker/examples/` was deleted rather than archived; restore it with
  `git checkout <commit> -- docker/examples`.

## `examples/`

The upstream DB-GPT sample scripts, moved here whole: `agents/` (20 files, 11M —
10.5M of that is `example_files/Nuclear_power.pdf` and `Taylor_Swift.pdf`),
`rag/` (15), `awel/` (8), `client/` (7), `test_files/` (8), `sdk/` (3), plus
`excel/`, `pdf/`, `__main__.py`, and `test_skills_middleware.py`. Nothing in the
Datrix runtime, scripts, CI, or Makefile referenced them; the last commit to
touch the directory was the upstream `fa7bc348 🎉 DB-GPT V0.8.0 - Beta Testing`.

**`examples/awel/` was not dead code.** It was the default AWEL DAG directory:

```python
# packages/dbgpt-core/src/dbgpt/configs/model_config.py:46
_DAG_DEFINITION_DIR = os.path.join(ROOT_PATH, "examples/awel")
```

`dbgpt_app/component_configs.py` loads that path at startup, and the eight files
in it registered nine `HttpTrigger` routes. Archiving the directory therefore
removes these endpoints from the API:

| Route |
|---|
| `/api/v1/awel/trigger/examples/hello` |
| `/api/v1/awel/trigger/examples/simple_chat` |
| `/api/v1/awel/trigger/examples/simple_history/multi_round/chat/completions` |
| `/api/v1/awel/trigger/examples/simple_client/chat/completions` |
| `/api/v1/awel/trigger/examples/simple_client/count_token` |
| `/api/v1/awel/trigger/examples/rag/schema_linking` |
| `/api/v1/awel/trigger/examples/rag/rewrite` |
| `/api/v1/awel/trigger/examples/rag/summary` |
| `/api/v1/awel/trigger/examples/data_analyst/copilot` |

`_DAG_DEFINITION_DIR` was deliberately left pointing at the now-missing path:
`LocalFileDAGLoader.load_dags()` skips a directory that does not exist
(`packages/dbgpt-core/src/dbgpt/core/awel/dag/loader.py:43`), so startup logs no
error and nothing crashes. Nothing consumed these endpoints — no frontend code,
no built static bundle, no script, no config.

To bring the examples back, move the directory to the repository root again:

```bash
mv legacy/examples examples
# then delete the `/legacy/examples/` rule from .gitignore and `git add examples/`
```

## `scripts/`

Holds the former `scripts/examples/` (`load_examples.sh`, `load_examples.bat`).
Those load the upstream demo SQLite databases from
`docker/examples/sqls/*_sqlite.sql`, a directory that was deleted, so they no
longer run. Nothing referenced them.
