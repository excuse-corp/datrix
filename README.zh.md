# <img src="./web/public/datrix-mark.svg" alt="Datrix" style="vertical-align: middle; height: 30px;" /> Datrix

**由智能体矩阵驱动的数据分析工具**

Datrix 由 Data 与 Matrix 组合而来，是一个以多智能体矩阵为核心的开源数据分析平台。不同角色的智能体协作完成数据发现、SQL 查询、分析、代码执行和报告生成，把一个问题推进到可追溯的结论。

## Datrix 能做什么

- 连接数据库、文件、数据仓库和知识库。
- 通过智能体协同完成规划、查询、分析、校验和呈现。
- 生成 SQL、代码、图表、报告和可复用技能。
- 在可控的数据访问边界内运行分析任务。

## 快速开始

兼容层中的 CLI 暂时仍使用 `dbgpt`，后续会完成包名和命令迁移。

```bash
uv run dbgpt start webserver --config configs/dataman-runtime.toml
```

本地托管开发环境可运行：

```bash
scripts/start_dataman_services.sh
```

## 品牌含义

名称由 **Data** 与 **Matrix** 组成，表达由智能体矩阵协作产生数据智能。随项目提供的图标以节点矩阵勾勒字母 D，青绿色代表数据流动，橙色代表智能体的决策节点。

## 许可证

本项目使用 MIT License，详见 [LICENSE](LICENSE)。
