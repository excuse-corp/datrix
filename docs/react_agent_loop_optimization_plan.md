# ReAct Agent Loop 优化开发计划

## 背景

当前 ReAct/skill 路径暴露出两个连续性问题：

- 上传图片创建 skill 时，系统能拿到图片文件，但图片分析结果没有被固化为后续生成步骤必须遵守的结构化约束，导致最终 skill/template 回落到默认蓝色风格。
- 同一 `conv_uid` 内，第一轮创建了 `data-report` skill，第二轮用户说“优化这个 skill”时，Agent 没有恢复上一轮的目标对象，也没有读取既有 `SKILL.md`，而是输出通用建议。

这不是单点模型能力问题，而是 Agent loop 的状态设计问题：UI 历史、`gpts_messages` 执行日志、磁盘产物之间没有统一的可恢复 session state；每轮 prompt 组装也没有把 active skill、附件、产物和最近会话历史作为一等上下文注入。

## 目标

1. 让 `conv_uid` 对应的 ReAct 会话拥有可恢复状态，而不是只作为日志 ID。
2. 支持 active skill / artifact / attachment 的跨轮恢复，解决“这个 skill”等指代。
3. 将上传图片转换为结构化附件上下文，至少包含文件类型、尺寸、主色板等可执行约束。
4. 每轮 prompt 都注入 session state，并对 skill 维护类任务加入强制读取/修改约束。
5. 每轮结束后把本轮产物、active skill 和 checkpoint 写回状态文件。

## 非目标

- 不重写 DB-GPT 原生 memory 架构。
- 不新增数据库迁移；第一阶段使用 JSON 状态文件实现最小闭环。
- 不把 ReAct 路径一次性改造成完整 Newman 架构；先补齐影响用户体验的关键状态链路。
- 不在本阶段实现完整视觉 OCR/语义理解；图片先做结构化元数据与主色提取，后续可替换为视觉模型摘要。

## 设计

### ConversationState

状态文件按会话保存：`pilot/meta_data/react_agent_state/{conv_uid}.json`。

核心结构：

```json
{
  "version": 1,
  "session_id": "...",
  "active_skill": {
    "name": "data-report",
    "path": "skills/data-report/SKILL.md",
    "source": "turn_artifact"
  },
  "attachments": [],
  "artifacts": [],
  "workflow_state": {},
  "pending_user_input": null,
  "checkpoint": {},
  "created_at": "...",
  "updated_at": "..."
}
```

### 每轮恢复流程

```text
读取 ConversationState
-> 读取 StorageConversation 历史消息
-> 若状态缺失，则从历史 view/tool 输出和 skills 目录推断 active_skill
-> 记录本轮上传附件并提取图片元数据
-> 渲染 Session Context 注入 ReAct system prompt
-> 执行 Agent loop
-> 从 history_steps/final_content 中提取产物和 skill 目标
-> 写回 ConversationState
```

### 指代解析

当用户请求包含“这个 skill / 这个技能 / this skill / it”等指代时：

- 若 `ConversationState.active_skill` 存在，直接作为目标。
- 若状态缺失，从最近历史消息、`skills/<name>/SKILL.md`、`skills/<name>.skill`、final view 文本中推断唯一候选。
- 若多个候选并列，不让模型猜测；prompt 要求先提问澄清。

### 图片约束

上传图片被记录为 attachment：

```json
{
  "kind": "image",
  "path": "/root/dataman/python_uploads/...jpg",
  "mime_type": "image/jpeg",
  "image": {
    "width": 840,
    "height": 763,
    "mode": "RGB",
    "dominant_colors": [
      {"hex": "#C06030", "ratio": 0.2363}
    ]
  }
}
```

Prompt 中明确要求：如果用户把图片作为视觉参考，后续生成的 CSS、图表配色、模板说明必须复用这些颜色/风格约束，并在最终产物中能被验证。

## 实施步骤

1. 新增 `react_session_state.py`：负责状态文件读写、附件记录、图片主色提取、历史推断、session context 渲染、回写更新。
2. 接入 `_react_agent_stream()`：提前确定 `conv_id`，加载状态，构造 `StorageConversation` 后补充历史推断。
3. Prompt 注入：在 skill/full 两种 prompt 中加入 `Session Context` 和 skill 维护门禁规则。
4. 结束回写：正常完成、取消、异常时都尽量保存当前状态；正常完成时从本轮步骤和最终回答中提取 artifacts/active skill。
5. 验证：新增单元测试覆盖状态恢复、active skill 推断、图片附件摘要、artifact 去重。

## 验收标准

- 第一轮上传图片创建 skill 后，状态文件记录图片附件、主色板和新 skill artifact。
- 第二轮用户只说“优化这个 skill”时，prompt 中能看到 active skill，且要求先读取目标 skill 文件。
- `data-report` 这类从历史产物推断出的 skill 能在服务重启后恢复。
- 直接终止并声称“没有原 skill”的概率显著降低；若上下文不足，系统应澄清而不是猜测。
- 不提交本地上传图片和 DB 备份等运行时产物。

## 后续增强

- 将 JSON 状态迁移到正式 DB 表，并增加并发锁。
- 引入视觉模型 attachment analyzer，补齐 OCR、布局、风格语义。
- 将工具调用和产物登记从启发式解析升级为工具层结构化事件。
- 增加 loop-level completion gate，在必须读/改文件的任务中拦截无工具证据的 `terminate`。
