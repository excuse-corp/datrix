# Datrix Agent 工具接入手册

本文档梳理当前 Datrix / DB-GPT 项目中主 Agent 接入工具的实际模式，并给出新增工具、Connector 工具和问数工具 `ask_data_query` 的标准接入规范。

> 结论先行：当前主 Agent 使用的是 ReAct 文本协议，不是原生 function calling。工具不仅要注册进 `ToolPack`，还必须把工具名、用途和精确参数 schema 写入主 Agent prompt。否则模型会猜参数名，典型问题是把 `ask_data_query` 的 `question` 误写成 `query`。

## 1. 关键代码位置

| 模块 | 位置 | 作用 |
|---|---|---|
| 主 Agent 流式入口 | `packages/dbgpt-app/src/dbgpt_app/openapi/api_v1/agentic_data_api.py` | 构造 ReAct prompt、工具实例、`ToolPack` 和 SSE 事件 |
| 工具定义目录 | `packages/dbgpt-app/src/dbgpt_app/openapi/api_v1/tools/` | 内置工具工厂，包含问数、SQL、知识库、脚本、HTML 等工具 |
| 工具基础类 | `packages/dbgpt-core/src/dbgpt/agent/resource/tool/base.py` | `@tool`、`FunctionTool`、参数 metadata 解析 |
| 工具包执行器 | `packages/dbgpt-core/src/dbgpt/agent/resource/tool/pack.py` | `ToolPack.async_execute()`，按工具 schema 过滤参数并执行 |
| ReAct 执行动作 | `packages/dbgpt-core/src/dbgpt/agent/expand/actions/react_action.py` | 解析 `Action` / `Action Input`，调用 `run_tool()` |
| 问数工具 | `packages/dbgpt-app/src/dbgpt_app/openapi/api_v1/tools/ask_data.py` | 暴露 `ask_data_query` / `ask_data_capabilities` 给主 Agent |
| 问数场景链路 | `packages/dbgpt-app/src/dbgpt_app/scene/ask_data/` | 场景、Snapshot、场景路由、单场景 SQL Agent、SQL 安全校验和执行 |

## 2. 当前工具接入模式

### 2.1 直接工具模式

直接工具是主 Agent 可以用 `Action: <tool_name>` 直接调用的工具。

典型工具：

- `ask_data_query`
- `ask_data_capabilities`
- `sql_query`
- `knowledge_retrieve`
- `html_interpreter`
- `shell_interpreter`
- `code_interpreter`
- `todowrite`
- `question`
- `terminate`

接入链路：

1. 在 `openapi/api_v1/tools/` 下用工厂函数创建工具。
2. 工具通常用 `@tool(...)` 包装为 `FunctionTool`。
3. `agentic_data_api.py` 在每次会话请求中实例化工具。
4. 所有直接工具被加入 `ToolPack([...])`。
5. prompt 的 “Available Tools Description” 或动态 schema 区块告诉模型工具怎么调用。
6. 模型输出 `Action` 和 `Action Input`。
7. `ReActAction` 解析后调用 `run_tool()`。
8. `ToolPack.async_execute()` 根据工具名执行对应工具。

注意：`ToolPack` 会删除 schema 里不存在的参数。例如工具只声明 `question`，模型传入 `query`，执行前 `query` 会被删除，最终变成空参数调用。

### 2.2 ResourceManager / 二级执行模式

`load_tools` 和 `execute_tool` 是旧的二级工具执行模式：

- `load_tools` 根据 skill metadata 解析 required tools。
- `execute_tool` 再根据 `tool_name` 和 `args` 去 ResourceManager 里找工具执行。

这个模式适合兼容旧 skill / 资源系统，但不推荐作为新业务工具的默认模式。

原因：

- 主 Agent 需要先知道 `execute_tool`，再知道二级工具名，提示词复杂。
- 二级工具的真实 schema 不一定进入主 prompt，模型仍然会猜参数。
- MCP Connector 这类动态工具如果通过 `execute_tool` 二次代理，容易出现找不到工具或参数不一致。

新增业务工具优先采用直接工具模式。

### 2.3 MCP Connector 动态工具模式

Connector 工具由用户在前端选择 `connector_ids` 后动态注入。

实际链路：

1. 前端把 `connector_ids` 放到请求上下文。
2. `agentic_data_api.py` 调用 `ConnectorManager.get_connector_tools(connector_id)`。
3. Connector 返回 MCP tool pack。
4. `_select_connector_tools()` 将嵌套 MCP tool pack 扁平化为多个 `BaseTool`。
5. 这些 `BaseTool` 直接加入主 `ToolPack`。
6. prompt 中动态追加 “Available MCP Connector Tools”，列出工具名、描述和参数。

接入规则：

- Connector 工具应直接调用：`Action: <connector_tool_name>`。
- 不应通过 `execute_tool` 二次调用。
- 写操作必须保留用户确认机制。
- prompt 的工具列表必须只包含用户显式选择且当前 active 的 connector。

### 2.4 Skill 模式工具

当用户预选 Skill 时，系统会走更窄的 skill prompt，核心工具包括：

- `execute_skill_script_file`
- `get_skill_resource`
- `execute_skill_script`
- `shell_interpreter`
- `html_interpreter`
- `sql_query`
- `todowrite`
- `question`
- `terminate`

如果某个业务工具允许在 Skill 模式使用，也必须加入 skill 模式 `ToolPack`，并在 skill prompt 中暴露清晰 schema。当前 `ask_data_tools` 已同时加入普通模式和 skill 模式。

## 3. 工具定义标准

### 3.1 推荐写法

```python
from dbgpt.agent.resource.tool.base import tool


@tool(
    description="查询已授权的业务数据场景。",
    args={
        "question": {
            "type": "string",
            "description": "自然语言业务问题。",
            "required": True,
        }
    },
)
async def ask_data_query(question: str) -> str:
    ...
```

要求：

- 工具名稳定，避免后续改名破坏 prompt 和历史调用。
- 参数名必须短、明确、不可混用。
- 参数描述必须说明输入边界，例如是否允许 SQL、表名、字段名、文件路径。
- 复杂参数优先显式写 `args` 或 `args_schema`，不要依赖模型猜结构。

### 3.2 参数 schema 来源

工具 schema 的权威来源是 `BaseTool.args`。

`@tool` 会把函数包装为 `FunctionTool`，并把参数解析到 `tool._tool.args`。`ToolPack.async_execute()` 执行前只保留 `BaseTool.args` 中声明的参数。

因此 prompt 中手写的参数说明必须与 `BaseTool.args` 一致。更推荐用运行时 metadata 渲染 prompt schema，而不是手写。

## 4. Prompt 暴露规范

当前主 Agent 是 ReAct 文本协议，必须在 system prompt 中显式告诉模型：

```text
Action: ask_data_query
Action Input: {"question": "任博寒有哪些信息化项目"}
```

推荐 schema 区块格式：

```text
## AskData Direct Tool Schema
These tools are directly callable with `Action: <tool_name>`.
Use the exact parameter names shown below.

- **ask_data_query**: 查询已授权的业务数据场景...
  Parameters: {"question": <string, required, 自然语言业务问题...>}
```

必须写清：

- 直接调用方式：`Action: <tool_name>`。
- 精确参数名：例如 `question`。
- 禁止参数：例如不要传 SQL、表名、字段名、数据源名、Snapshot ID。
- 出错后的处理策略：不可恢复错误不要无限重试。

## 5. 问数工具接入模式

### 5.1 工具定位

`ask_data_query` 是主 Agent 查询受控业务数据的唯一入口。

它负责：

- 根据用户自然语言问题匹配可用场景。
- 针对每个选中场景启动一个场景 SQL Agent。
- 场景 SQL Agent 只读取当前场景的数据字典和业务语义文档。
- 场景 SQL Agent 自由生成当前场景内的只读 SQL。
- 校验 SQL 是否只访问该场景绑定的表或视图。
- 执行查询并返回结构化结果。
- 按场景汇总结果为 JSON 返回给主 Agent。

它不负责：

- 执行用户传入的自由 SQL。
- 接收表名、字段名、视图名、数据源名或 Snapshot ID。
- 绕过场景启用状态和权限。
- 进行跨场景业务合并、最终回答撰写或可视化编排。

### 5.2 主 Agent 上下文

启用问数后，`agentic_data_api.py` 会构造：

- `AskDataPrincipal`：当前用户身份和角色。
- `SceneQueryAgent`：用于选择相关场景，并在每个场景内生成 SQL。
- `ask_data_query` / `ask_data_capabilities`：两个直接工具。
- `ask_data_capability_summary()`：把当前 active 场景的名称和说明注入主 Agent prompt。
- `AskData Direct Tool Schema`：从工具 metadata 渲染参数 schema。

主 Agent 只拿到场景级能力说明，不直接拿数据库连接、表结构、SQL 或同步账号凭据。

主 Agent 在调用 `ask_data_query` 前，应结合历史消息把省略、代词、追问改写成一个自包含的自然语言业务问题；`ask_data_query` 仍然只接收 `question` 一个参数。

### 5.3 正确调用协议

正确：

```text
Action: ask_data_query
Action Input: {"question": "任博寒有哪些信息化项目"}
```

错误：

```text
Action: ask_data_query
Action Input: {"query": "任博寒有哪些信息化项目"}
```

错误：

```text
Action: ask_data_query
Action Input: {"question": "select * from sync.pcm_project_info"}
```

### 5.4 本次优化后的兼容策略

正式 schema 仍然只有：

```json
{"question": "自然语言业务问题"}
```

同时，`ask_data_query` 增加了执行前参数归一化：

- 如果模型按标准传 `{"question": "..."}`，正常执行。
- 如果旧 prompt 或模型误传 `{"query": "..."}`，运行时转换为 `{"question": "..."}`。
- 如果模型直接传纯文本，也转换为 `{"question": "<raw text>"}`。
- prompt 不暴露 `query`，避免继续强化错误参数名。

这样解决的是“主 Agent 偶发传错参数名导致工具调用失败”的接入稳定性问题，不改变问数工具的对外契约。

### 5.5 问数执行与返回协议

问数工具当前采用以下执行模式：

1. 主 Agent 调用 `ask_data_query({"question": "..."})`。
2. AskData 使用 LLM 基于系统级场景摘要选择相关 active 场景。
3. 每个被选中场景单独调用一个 Scene SQL Agent。
4. Scene SQL Agent 只读取该场景的绑定对象、实际字段、数据字典和业务语义文档。
5. Scene SQL Agent 生成一条只读 SQL。
6. 系统校验 SQL 只访问当前场景绑定的表或视图。
7. 系统执行 SQL，并按场景返回结果。
8. AskData Orchestrator 只做结果收集和 JSON 汇总，不做业务合并、不生成最终回答、不调用可视化工具。
9. 主 Agent 基于 JSON 结果继续 ReAct：生成最终回答，或在用户需要时调用图表、报告等其他工具。

返回给主 Agent 的 JSON 以场景为单位组织：

```json
{
  "status": "succeeded",
  "query_id": "qry_xxx",
  "scenes": [
    {
      "scene_id": "scene_xxx",
      "status": "succeeded",
      "row_count": 384,
      "truncated": false,
      "columns": ["project_name", "project_manager"],
      "rows": []
    }
  ],
  "errors": []
}
```

如果返回内容超过系统配置上下文窗口的 50%，工具会截断传给主 Agent 的行数据，并在 JSON 中写入 `truncated_for_main_agent` 和 `truncation_message`。HTTP 查询结果和运行记录仍保留后端实际执行结果。

### 5.6 执行过程事件

问数工具通过 SSE 把关键过程写入聊天界面的任务执行节点：

- `routing`：正在匹配业务场景。
- `planning`：已选中哪些场景。
- `subagent-<scene_id>`：对应场景 SQL Agent 已启动并读取场景文档。
- `sql-gen-<scene_id>`：SQL 生成成功或失败。
- `sql-exec-<scene_id>`：SQL 执行成功或失败。
- `ask_data.result`：最终结构化结果。

这些节点用于让用户看到“选中了哪些场景、用了哪些子 Agent、SQL 是否成功执行”，但不会把数据库连接或敏感配置暴露给主 Agent。

## 6. 新工具接入流程

新增一个主 Agent 工具时按以下流程执行：

1. 定义工具函数
   - 放在 `packages/dbgpt-app/src/dbgpt_app/openapi/api_v1/tools/`。
   - 使用 `@tool` 或 `BaseTool`。
   - 参数简单时可用函数签名，生产业务工具建议显式 `args`。

2. 注册工具实例
   - 在 `agentic_data_api.py` 中调用工具工厂。
   - 加入普通模式 `ToolPack`。
   - 如 Skill 模式需要，也加入 Skill 模式 `ToolPack`。

3. 暴露 prompt schema
   - 优先从 `BaseTool.args` 动态渲染。
   - 不要只写“接受 JSON 参数”这类模糊描述。
   - 不要让 prompt 中的参数名和工具 metadata 不一致。

4. 接入流式事件
   - 长耗时工具应输出阶段事件。
   - 问数工具使用 `ask_data.stage`、`ask_data.result`、`ask_data.clarification`。

5. 处理错误
   - 返回稳定错误码和可读 message。
   - 不暴露数据库密码、连接串、同步账号、内部堆栈。
   - 对不可恢复错误提示 Agent 停止重试。

6. 增加测试
   - 工具 schema 是否包含正确参数名。
   - prompt 是否能渲染工具 schema。
   - 正确 Action Input 能执行。
   - 常见错误 Action Input 有明确失败或兼容策略。

## 7. 接入检查清单

- [ ] 工具已定义为 `@tool` / `BaseTool`。
- [ ] 参数名、类型、必填项清晰。
- [ ] 工具已加入目标模式的 `ToolPack`。
- [ ] prompt 中有精确 schema。
- [ ] prompt 中说明安全边界和禁止输入。
- [ ] 长耗时工具有阶段事件。
- [ ] 返回值是稳定 JSON 或稳定文本协议。
- [ ] 错误信息不会诱导 Agent 无限重试。
- [ ] Connector 工具已扁平化，不通过二级代理调用。
- [ ] 单测覆盖 schema 和至少一个端到端调用样例。

## 8. 常见问题

| 问题 | 直接原因 | 处理方式 |
|---|---|---|
| 模型把 `question` 写成 `query` | prompt 没有足够强地暴露工具 schema，或上下文里残留旧格式 | 用 metadata 渲染 schema；问数工具已增加旧参数归一化 |
| 工具报缺少必填参数 | `ToolPack` 删除了 schema 里不存在的参数 | 检查 `Action Input` key 是否与 `BaseTool.args` 一致 |
| 工具注册了但模型不用 | prompt 中没有触发条件或工具能力说明 | 增加业务触发规则和能力摘要 |
| Connector 工具找不到 | MCP tool pack 未扁平化，或用户未选择该 connector | 使用 `_select_connector_tools()` 扁平化，只注入已选择 active connector |
| 模型通过 `execute_tool` 调直接工具 | prompt 没写清直接调用方式 | 写明 `Action: <tool_name>`，不要二次代理 |
| 问数返回 SQL 校验错误 | 场景 SQL Agent 生成了跨绑定对象、写操作或不安全 SQL | 优化场景数据字典 / 业务语义文档；系统会拒绝执行 |

## 9. 推荐后续优化

当前仍有一部分内置工具参数说明在 prompt 中手写。建议后续逐步统一为：

- `ToolPack` 是唯一工具注册源。
- prompt 工具列表完全从 `ToolPack.sub_resources` / `BaseTool.args` 渲染。
- 手写 prompt 只保留业务规则，不手写参数 schema。
- 对特殊工具追加安全规则和错误处理规则。

这个方向可以从机制上避免“后端工具能执行，但主 Agent 不知道精确 schema”的问题。
