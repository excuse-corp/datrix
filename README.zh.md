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
  <strong>Datrix 是一个基于 DB-GPT 开源项目改造的数据智能工作台。</strong><br />
  <strong>Slogan：让每一次提问，都变成可审计的数据答案。</strong><br />
  它面向真实的数据分析流程，让 Agent 理解业务场景、查询受治理的数据、执行分析任务，并交付可追溯的洞察。
</p>

## 项目来源

Datrix 基于 [DB-GPT](https://github.com/eosphoros-ai/DB-GPT) 开源项目改造而来。它保留 DB-GPT 在Agent、工具调用、代码执行和私有化部署方面的基础能力，并在此之上扩展为面向业务场景的数据智能平台。

兼容层中的 CLI 暂时仍使用 `dbgpt`，后续会完成包名和命令迁移。

## Datrix 能做什么

- **多智能体协作查数**：由规划、场景路由、SQL 生成、SQL 执行、结果校验和总结智能体协同工作，把一个自然语言问题推进为可验证的数据答案。
- **场景化查数**：围绕业务场景沉淀数据字典、语义文档、指标口径、维度关系和查询边界，让 AI 在正确的业务上下文中选择场景、生成 SQL 和解释结果。
- **Ontology 数据分析能力**：通过本体 Ontology 建模实体、指标、维度、业务规则和跨场景关系，辅助智能体理解数据含义、发现可关联路径，并提升分析解释的一致性。
- **自然语言生成 SQL**：使用自然语言提问，让 AI 自主理解意图、选择表/视图、编写 SQL，并在受控范围内执行查询。
- **代码驱动分析**：执行 Python 与代码驱动的数据分析流程，支持数据清洗、统计分析、建模、文件处理和可复现计算。
- **可复用 Skills 和 Plugins**：把常见分析方法、业务模板、连接器、自动化流程和领域经验沉淀为可调用能力。
- **自动化可视化与报告**：自动生成图表、Dashboard、HTML 报告和分析总结，帮助用户从查询结果直接获得可交付洞察。
- **安全沙箱执行**：在沙箱环境中安全执行分析任务，隔离代码运行边界，降低数据分析自动化带来的执行风险。

## 核心工作流

1. 用户用自然语言提出业务问题。
2. 主智能体规划分析路径，并调用场景、工具、Skill 或 Plugin 路由能力选择合适执行路径。
3. 场景子智能体读取场景文档、数据字典、Ontology 上下文和工作区状态，生成受约束的执行动作。
4. 执行智能体在受控环境中运行 SQL、Python、Shell、连接器或工具型分析任务。
5. 汇总智能体校验结果、生成图表或报告，并输出可追溯的分析结论。

## 快速开始

`dbgpt start webserver` 启动的是 Datrix 后端 WebServer。这个服务会运行 FastAPI / Uvicorn API，并从后端包里托管已经构建好的静态前端；它不是单独的前端开发服务。

首次克隆仓库后，需要先安装前端依赖并构建静态文件，再启动后端。后端目录中的静态资源属于被忽略的构建产物，新克隆的仓库里不会自带：

```bash
cd web
npm ci
cd ..
./scripts/build_web_static.sh
```

CLI 名称目前仍沿用 DB-GPT 的 `dbgpt`，这是包名和命令迁移完成前的兼容命名。

在新的 Linux 或 macOS 服务器上，可以在终端运行下面的安装脚本。它会下载 Datrix 源码、安装 Python 和前端依赖、构建前端，并打开模型配置向导。它是一个命令行脚本，不是单独的桌面软件。运行前需要安装 Git、curl、Node.js 和 npm；使用这条安装路径不需要 Docker。

```bash
curl -fsSL https://raw.githubusercontent.com/excuse-corp/datrix/main/scripts/install/install.sh | bash
```

配置完成后，脚本会打印启动 Datrix 的命令。

使用 `uv` 从源码安装时，请先在仓库根目录安装后端依赖：

```bash
uv sync --all-packages --extra base --extra proxy_openai --extra rag --extra storage_chromadb --extra dbgpts
```

复制环境变量模板，设置有效的模型服务 API Key，并在启动前加载：

```bash
cp configs/.env.ask-data.example configs/.env.ask-data
# 编辑 configs/.env.ask-data，然后将其中变量加载到当前 Shell。
set -a
source configs/.env.ask-data
set +a
```

然后启动后端：

```bash
uv run dbgpt start webserver --config configs/dataman-runtime.toml
```

如果直接使用本仓库的 `dataman` conda 环境，可运行：

```bash
conda run -n dataman dbgpt start webserver --config configs/dataman-runtime.toml --yes
```

如果需要启动本地开发所依赖的一组服务，包括数据库启动和前端静态资源构建，可运行：

```bash
cp docker/dataman-postgres/.env.example docker/dataman-postgres/.env
cp configs/.env.ask-data.example configs/.env.ask-data
scripts/start_dataman_services.sh
```

启动前，请在本地环境文件中设置密码和模型服务凭据。该管理脚本还要求已创建 `dataman` conda 环境；它会自动构建前端，但要求 `web/node_modules` 已存在。首次运行前，先执行 `cd web && npm ci` 安装依赖。

这套开发服务用 Docker 运行 PostgreSQL，Datrix 后端则运行在 `dataman` conda 环境中。代码执行沙箱也可使用 Docker、Podman 或 Nerdctl 运行隔离的代码会话。仓库目前没有 Datrix 应用镜像或一键启动整套服务的 Compose 配置；旧版上游 DB-GPT 部署文件已从 Git 跟踪内容中移除。

## 产品定位

Datrix 聚焦业务侧数据智能：在继承 DB-GPT 数据与 Agent 基座的基础上，进一步补齐场景上下文、能力复用、受控执行和结果可追溯这些企业分析交付所需的产品能力。

## 许可证

本项目使用 MIT License，详见 [LICENSE](LICENSE)。
