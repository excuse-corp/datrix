import { ChatContext } from '@/app/chat-context';
import ModelSelector from '@/components/chat/header/model-selector';
import { ColumnAnalysis, PreprocessingResult, analyzeDataset } from '@/new-components/analysis';
import { ChartConfig, ChartType } from '@/new-components/charts';
import ContextUsageBar from '@/new-components/chat/content/ContextUsageBar';
import ManusLeftPanel, {
  ExecutionStep as ManusExecutionStep,
  StepType,
  ThinkingSection,
} from '@/new-components/chat/content/ManusLeftPanel';
import ManusRightPanel, {
  ActiveStepInfo,
  ExecutionOutput as ManusExecutionOutput,
  PanelView,
} from '@/new-components/chat/content/ManusRightPanel';
import { MessagePart, ToolPart, ToolStatus } from '@/new-components/chat/content/OpenCodeSessionTurn';
import QuestionDock from '@/new-components/chat/content/QuestionDock';
import TaskPlanCard, { TaskItem } from '@/new-components/chat/content/TaskPlanCard';
import ConfirmDialog from '@/new-components/connector/ConfirmDialog';
import { AttachedConnector, ConnectorInstance } from '@/new-components/connector/types';
import { useConfirmPolling } from '@/new-components/connector/useConfirmPolling';
import FromTaskBanner from '@/new-components/scheduled-task/FromTaskBanner';
import { listScenes, type AskDataQueryResponse, type SceneSummary } from '@/utils/ask-data';
import axios from '@/utils/ctx-axios';
import {
  ArrowUpOutlined,
  AudioOutlined,
  BarChartOutlined,
  CheckCircleFilled,
  CodeOutlined,
  FileExcelOutlined,
  FileImageOutlined,
  FileOutlined,
  FilePptOutlined,
  FileTextOutlined,
  LeftOutlined,
  PaperClipOutlined,
  PieChartOutlined,
  PlusOutlined,
  RightOutlined,
  SearchOutlined,
  StopOutlined,
  TableOutlined,
  ThunderboltOutlined,
  UploadOutlined,
} from '@ant-design/icons';
import { useRequest } from 'ahooks';
import { Button, ConfigProvider, Dropdown, Input, Popover, Spin, Tag, Tooltip, Upload, message } from 'antd';
import { NextPage } from 'next';
import Image from 'next/image';
import { useRouter } from 'next/router';
import { useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

const generateUUID = () => {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
};

const cleanFinalContent = (text: string): string => {
  let cleaned = text.replace(/\\n/g, '\n').trim();
  cleaned = cleaned.replace(/\n{3,}/g, '\n\n');
  cleaned = cleaned.replace(/"\s*\}\s*$/, '').trim();
  // Strip raw ReAct prefixes that may leak from the backend
  cleaned = cleaned.replace(/^(Thought|Action|Action Input|Observation|Phase):\s*/gm, '').trim();
  return cleaned;
};

const getAskDataSceneResults = (resultPayload: any): any[] => {
  if (!resultPayload || typeof resultPayload !== 'object') return [];
  if (Array.isArray(resultPayload.scenes)) return resultPayload.scenes;
  if (Array.isArray(resultPayload.results)) return resultPayload.results;
  if (Array.isArray(resultPayload.scene_summaries)) return resultPayload.scene_summaries;
  return [];
};

type AskDataSceneNameLookup = Record<string, string>;

const INTERNAL_SCENE_ID_REGEX = /scene_[a-zA-Z0-9_-]+/;

const textValue = (value: unknown): string => (typeof value === 'string' && value.trim() ? value.trim() : '');

const getAskDataSceneId = (scene?: any): string => {
  const sceneId = textValue(scene?.scene_id);
  if (sceneId) return sceneId;
  const taskId = textValue(scene?.task_id);
  if (!taskId) return '';
  return taskId.match(INTERNAL_SCENE_ID_REGEX)?.[0] || taskId;
};

const getAskDataStageSceneId = (stage?: unknown, title?: unknown, detail?: unknown): string => {
  const source = [stage, title, detail].map(textValue).filter(Boolean).join('\n');
  const internalSceneId = source.match(INTERNAL_SCENE_ID_REGEX)?.[0];
  if (internalSceneId) return internalSceneId;

  const stageText = textValue(stage);
  return stageText.match(/^(?:subagent|sql-gen|sql-exec)-(.+)$/)?.[1] || '';
};

const getAskDataSceneName = (scene?: any, sceneNameById: AskDataSceneNameLookup = {}): string => {
  const sceneId = getAskDataSceneId(scene);
  return (
    textValue(scene?.name) ||
    textValue(scene?.scene_name) ||
    textValue(scene?.display_name) ||
    (sceneId ? sceneNameById[sceneId] : '') ||
    sceneId ||
    '业务场景'
  );
};

const escapeRegExp = (value: string): string => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

const replaceAskDataSceneId = (text: string, sceneId: string, sceneName: string): string => {
  if (!sceneId || !sceneName || sceneId === sceneName) return text;
  return text.replace(new RegExp(escapeRegExp(sceneId), 'g'), sceneName);
};

const replaceAskDataSceneIdsWithNames = (value: string, sceneNameById: AskDataSceneNameLookup = {}): string => {
  return Object.entries(sceneNameById).reduce(
    (text, [sceneId, sceneName]) => replaceAskDataSceneId(text, sceneId, sceneName),
    value,
  );
};

const buildAskDataSceneNameLookup = (
  resultPayload: any,
  baseLookup: AskDataSceneNameLookup = {},
): AskDataSceneNameLookup => {
  const lookup: AskDataSceneNameLookup = { ...baseLookup };
  const addScene = (scene: any) => {
    const sceneId = getAskDataSceneId(scene);
    if (!sceneId) return;
    const sceneName = getAskDataSceneName(scene, lookup);
    if (sceneName && sceneName !== sceneId) lookup[sceneId] = sceneName;
  };

  getAskDataSceneResults(resultPayload).forEach(addScene);
  if (Array.isArray(resultPayload?.results)) resultPayload.results.forEach(addScene);
  if (Array.isArray(resultPayload?.scenes)) resultPayload.scenes.forEach(addScene);
  if (Array.isArray(resultPayload?.scene_summaries)) resultPayload.scene_summaries.forEach(addScene);
  return lookup;
};

const normalizeAskDataVisibleText = (
  value: string | undefined,
  sceneNameById: AskDataSceneNameLookup = {},
  sceneId?: string,
): string | undefined => {
  if (!value) return value;
  const namedSceneId = sceneId || getAskDataStageSceneId(undefined, value, undefined);
  const namedSceneName = namedSceneId ? sceneNameById[namedSceneId] : '';
  const replaced = namedSceneName ? replaceAskDataSceneId(value, namedSceneId, namedSceneName) : value;
  return replaceAskDataSceneIdsWithNames(replaced, sceneNameById);
};

const hasAskDataSceneReference = (...values: unknown[]): boolean =>
  values.some(value => typeof value === 'string' && INTERNAL_SCENE_ID_REGEX.test(value));

const getAskDataStageDisplay = (stage?: unknown, sceneNameById: AskDataSceneNameLookup = {}): string => {
  const stageText = textValue(stage);
  if (!stageText) return '';
  if (stageText === 'routing') return '业务场景语义选择';
  if (stageText === 'planning') return '业务场景规划';
  if (stageText === 'querying') return '场景 SQL 执行';

  const sceneId = getAskDataStageSceneId(stageText);
  const sceneName = sceneId ? sceneNameById[sceneId] || sceneId : '';
  if (stageText.startsWith('subagent-')) return sceneName ? `场景子 Agent：${sceneName}` : '场景子 Agent';
  if (stageText.startsWith('sql-gen-')) return sceneName ? `SQL 生成：${sceneName}` : 'SQL 生成';
  if (stageText.startsWith('sql-exec-')) return sceneName ? `SQL 执行：${sceneName}` : 'SQL 执行';
  return replaceAskDataSceneIdsWithNames(stageText, sceneNameById);
};

const normalizeAskDataOutputContent = (content: any, sceneNameById: AskDataSceneNameLookup = {}): any => {
  if (typeof content === 'string') return replaceAskDataSceneIdsWithNames(content, sceneNameById);
  return content;
};

const isAskDataStageStep = (step?: Partial<ExecutionStep> | null): boolean =>
  String(step?.action || '').toLowerCase() === 'ask_data_stage' || String(step?.id || '').startsWith('ask-data-');

const formatAskDataStageOutput = (
  stage: {
    stage?: string;
    title?: string;
    detail?: string;
    status?: string;
    duration_ms?: number;
    scene?: any;
    resultPayload?: any;
  },
  sceneNameById: AskDataSceneNameLookup = {},
): string => {
  const lines: string[] = [];
  const scene = stage.scene;
  const stageSceneId = getAskDataStageSceneId(stage.stage, stage.title, stage.detail) || getAskDataSceneId(scene);
  const stageSceneName =
    textValue(scene?.name) ||
    textValue(scene?.scene_name) ||
    textValue(scene?.display_name) ||
    (stageSceneId ? sceneNameById[stageSceneId] : '');
  const localSceneNameById =
    stageSceneId && stageSceneName && stageSceneName !== stageSceneId
      ? { ...sceneNameById, [stageSceneId]: stageSceneName }
      : sceneNameById;
  const title = normalizeAskDataVisibleText(stage.title, localSceneNameById, stageSceneId) || 'AskData 阶段';
  const status = stage.status || stage.scene?.status || stage.resultPayload?.status;
  lines.push(`### ${title}`);
  if (stage.stage) lines.push(`- 阶段：\`${getAskDataStageDisplay(stage.stage, localSceneNameById)}\``);
  if (status) lines.push(`- 状态：\`${status}\``);
  if (typeof stage.duration_ms === 'number') lines.push(`- 耗时：${(stage.duration_ms / 1000).toFixed(2)} 秒`);
  const detail = normalizeAskDataVisibleText(stage.detail, localSceneNameById, stageSceneId);
  if (detail) lines.push(`- 说明：${detail}`);

  if (scene && typeof scene === 'object') {
    const sceneName = getAskDataSceneName(scene, localSceneNameById);
    if (sceneName) lines.push(`- 场景：\`${sceneName}\``);
    if (scene.snapshot_id) lines.push(`- Snapshot：\`${scene.snapshot_id}\``);
    if (scene.row_count !== undefined) lines.push(`- 返回行数：${scene.row_count}`);
    if (scene.truncated !== undefined) lines.push(`- 是否截断：${scene.truncated ? '是' : '否'}`);
    if (Array.isArray(scene.columns) && scene.columns.length) {
      lines.push(`- 返回字段：${scene.columns.map((col: any) => `\`${col?.name || col?.key || col}\``).join('、')}`);
    }
    if (Array.isArray(scene.warnings) && scene.warnings.length) {
      lines.push(`- 警告：${scene.warnings.map((item: any) => String(item)).join('；')}`);
    }
    const errorText = scene.error || scene.message || scene.error_message;
    if (errorText) lines.push(`- 错误：${errorText}`);
  }

  const resultPayload = stage.resultPayload;
  if (resultPayload && typeof resultPayload === 'object') {
    if (resultPayload.query_id) lines.push(`- Query ID：\`${resultPayload.query_id}\``);
    if (resultPayload.request_id) lines.push(`- Request ID：\`${resultPayload.request_id}\``);
  }
  return lines.join('\n');
};

const buildAskDataStageOutput = (
  stage: {
    stage?: string;
    title?: string;
    detail?: string;
    status?: string;
    duration_ms?: number;
    scene?: any;
    resultPayload?: any;
  },
  sceneNameById: AskDataSceneNameLookup = {},
): ExecutionOutput[] => [
  {
    output_type: 'markdown',
    content: formatAskDataStageOutput(stage, sceneNameById),
  },
];

const formatStageDetailWithDuration = (detail?: string, durationMs?: number): string => {
  const base = detail || 'AskData';
  if (typeof durationMs !== 'number') return base;
  return `${base} · 耗时 ${(durationMs / 1000).toFixed(2)} 秒`;
};

const getAskDataCallId = (payload: any): string =>
  String(payload?.call_id || payload?.query_id || 'legacy').replace(/[^a-zA-Z0-9_-]/g, '_');

const getAskDataStageId = (callId: string, stage: unknown): string => `ask-data-${callId}-${String(stage || 'query')}`;

const normalizeAskDataActionInput = (value: unknown): string => {
  if (value && typeof value === 'object') {
    const question = (value as Record<string, unknown>).question;
    if (typeof question === 'string') return question.trim().replace(/\s+/g, ' ');
  }
  if (typeof value !== 'string') return '';
  try {
    const parsed = JSON.parse(value);
    if (parsed && typeof parsed.question === 'string') return parsed.question.trim().replace(/\s+/g, ' ');
  } catch {
    return value.trim().replace(/\s+/g, ' ');
  }
  return value.trim().replace(/\s+/g, ' ');
};

const collapseDuplicateAskDataSteps = (rawSteps: any[]): any[] => {
  const seenQuestions = new Set<string>();
  return (Array.isArray(rawSteps) ? rawSteps : []).filter(step => {
    if (String(step?.action || '').toLowerCase() !== 'ask_data_query') return true;
    const question = normalizeAskDataActionInput(step?.action_input ?? step?.actionInput);
    if (step?.ask_data_reused || (question && seenQuestions.has(question))) return false;
    if (question) seenQuestions.add(question);
    return true;
  });
};

const buildAskDataFallbackSteps = (resultPayload: any, sceneNameById: AskDataSceneNameLookup = {}): any[] => {
  const scenes = getAskDataSceneResults(resultPayload);
  if (!scenes.length) return [];

  const callId = getAskDataCallId(resultPayload);
  const payloadSceneNameById = buildAskDataSceneNameLookup(resultPayload, sceneNameById);
  const sceneNames = scenes.map((scene: any) => getAskDataSceneName(scene, payloadSceneNameById));
  return [
    {
      id: getAskDataStageId(callId, 'routing'),
      title: '业务场景语义选择完成',
      detail: '基于用户问题和场景 Snapshot 完成判断',
      action: 'ask_data_stage',
      status: 'done',
      askDataCallId: callId,
      parentId: `ask-data-${callId}`,
      outputs: buildAskDataStageOutput(
        {
          stage: 'routing',
          title: '业务场景语义选择完成',
          detail: '基于用户问题和场景 Snapshot 完成判断',
          status: resultPayload.status,
          resultPayload,
        },
        payloadSceneNameById,
      ),
    },
    {
      id: getAskDataStageId(callId, 'planning'),
      title: '已选中业务场景',
      detail: sceneNames.join('、'),
      action: 'ask_data_stage',
      status: 'done',
      askDataCallId: callId,
      parentId: `ask-data-${callId}`,
      outputs: buildAskDataStageOutput(
        {
          stage: 'planning',
          title: '已选中业务场景',
          detail: sceneNames.join('、'),
          status: resultPayload.status,
          resultPayload,
        },
        payloadSceneNameById,
      ),
    },
    ...scenes.flatMap((scene: any) => {
      const sceneId = getAskDataSceneId(scene) || 'unknown';
      const sceneName = getAskDataSceneName(scene, payloadSceneNameById);
      const status = String(scene?.status || resultPayload.status || '').toLowerCase();
      const failed = status === 'failed' || status === 'error';
      return [
        {
          id: getAskDataStageId(callId, `subagent-${sceneId}`),
          title: `场景子 Agent：${sceneName}`,
          detail: '读取数据字典和业务语义文档',
          action: 'ask_data_stage',
          status: failed ? 'failed' : 'done',
          askDataCallId: callId,
          parentId: `ask-data-${callId}`,
          outputs: buildAskDataStageOutput(
            {
              stage: `subagent-${sceneId}`,
              title: `场景子 Agent：${sceneName}`,
              detail: '读取数据字典和业务语义文档',
              status: failed ? 'failed' : 'done',
              scene,
              resultPayload,
            },
            payloadSceneNameById,
          ),
        },
        {
          id: getAskDataStageId(callId, `sql-gen-${sceneId}`),
          title: `${failed ? 'SQL 生成失败' : 'SQL 生成成功'}：${sceneName}`,
          detail: failed ? scene?.error || scene?.message || '查询失败' : '已通过绑定表/视图校验',
          action: 'ask_data_stage',
          status: failed ? 'failed' : 'done',
          askDataCallId: callId,
          parentId: `ask-data-${callId}`,
          outputs: buildAskDataStageOutput(
            {
              stage: `sql-gen-${sceneId}`,
              title: `${failed ? 'SQL 生成失败' : 'SQL 生成成功'}：${sceneName}`,
              detail: failed ? scene?.error || scene?.message || '查询失败' : '已通过绑定表/视图校验',
              status: failed ? 'failed' : 'done',
              scene,
              resultPayload,
            },
            payloadSceneNameById,
          ),
        },
      ];
    }),
    {
      id: getAskDataStageId(callId, 'querying'),
      title: '场景 SQL 执行完成',
      detail: `已执行 ${scenes.length} 个场景`,
      action: 'ask_data_stage',
      status: 'done',
      askDataCallId: callId,
      parentId: `ask-data-${callId}`,
      outputs: buildAskDataStageOutput(
        {
          stage: 'querying',
          title: '场景 SQL 执行完成',
          detail: `已执行 ${scenes.length} 个场景`,
          status: resultPayload.status,
          resultPayload,
        },
        payloadSceneNameById,
      ),
    },
    ...scenes.map((scene: any) => {
      const sceneId = getAskDataSceneId(scene) || 'unknown';
      const sceneName = getAskDataSceneName(scene, payloadSceneNameById);
      const status = String(scene?.status || resultPayload.status || '').toLowerCase();
      const failed = status === 'failed' || status === 'error';
      const detail = failed ? scene?.error || scene?.message || '查询失败' : `${scene?.row_count ?? 0} 行`;
      return {
        id: getAskDataStageId(callId, `sql-exec-${sceneId}`),
        title: `${failed ? 'SQL 执行失败' : 'SQL 执行成功'}：${sceneName}`,
        detail,
        action: 'ask_data_stage',
        status: failed ? 'failed' : 'done',
        askDataCallId: callId,
        parentId: `ask-data-${callId}`,
        outputs: buildAskDataStageOutput(
          {
            stage: `sql-exec-${sceneId}`,
            title: `${failed ? 'SQL 执行失败' : 'SQL 执行成功'}：${sceneName}`,
            detail,
            status: failed ? 'failed' : 'done',
            scene,
            resultPayload,
          },
          payloadSceneNameById,
        ),
      };
    }),
  ];
};

const normalizeAskDataStepRecord = (step: any, sceneNameById: AskDataSceneNameLookup = {}) => {
  const stageSceneId = getAskDataStageSceneId(step?.id, step?.title, step?.detail);
  const shouldNormalize = isAskDataStageStep(step) || hasAskDataSceneReference(step?.id, step?.title, step?.detail);
  if (!shouldNormalize) return step;
  return {
    ...step,
    title: normalizeAskDataVisibleText(step?.title, sceneNameById, stageSceneId) || step?.title,
    detail: normalizeAskDataVisibleText(step?.detail, sceneNameById, stageSceneId) || step?.detail,
    outputs: Array.isArray(step?.outputs)
      ? step.outputs.map((output: ExecutionOutput) => ({
          ...output,
          content: normalizeAskDataOutputContent(output.content, sceneNameById),
        }))
      : step?.outputs,
  };
};

const mergeAskDataFallbackSteps = (
  rawSteps: any[],
  resultPayload: any,
  sceneNameById: AskDataSceneNameLookup = {},
): any[] => {
  const safeRawSteps = Array.isArray(rawSteps) ? rawSteps : [];
  const payloadSceneNameById = buildAskDataSceneNameLookup(resultPayload, sceneNameById);
  const fallbackSteps = buildAskDataFallbackSteps(resultPayload, payloadSceneNameById);
  if (!fallbackSteps.length) {
    return safeRawSteps.map((step, idx) =>
      normalizeAskDataStepRecord({ ...step, step: idx + 1 }, payloadSceneNameById),
    );
  }

  const rawById = new Map(safeRawSteps.map(step => [String(step?.id || ''), step]));
  const fallbackIds = new Set(fallbackSteps.map(step => step.id));
  const mergedFallbackSteps = fallbackSteps.map(step => {
    const raw = rawById.get(step.id) || {};
    return normalizeAskDataStepRecord(
      {
        ...step,
        ...raw,
        id: step.id,
        outputs: Array.isArray(raw.outputs) && raw.outputs.length ? raw.outputs : step.outputs,
      },
      payloadSceneNameById,
    );
  });
  const rawWithoutFallback = safeRawSteps.filter(step => !fallbackIds.has(String(step?.id || '')));
  const callId = getAskDataCallId(resultPayload);
  const askDataStepIndex = rawWithoutFallback.findIndex(
    step =>
      String(step?.action || '').toLowerCase() === 'ask_data_query' &&
      (!step?.ask_data_call_id || String(step.ask_data_call_id) === callId),
  );
  if (askDataStepIndex < 0) {
    return [...rawWithoutFallback, ...mergedFallbackSteps].map((step, idx) =>
      normalizeAskDataStepRecord({ ...step, step: idx + 1 }, payloadSceneNameById),
    );
  }
  const askDataStep = rawWithoutFallback[askDataStepIndex];
  return [
    ...rawWithoutFallback.slice(0, askDataStepIndex),
    askDataStep,
    ...mergedFallbackSteps,
    ...rawWithoutFallback.slice(askDataStepIndex + 1),
  ].map((step, idx) => normalizeAskDataStepRecord({ ...step, step: idx + 1 }, payloadSceneNameById));
};

const extractAskDataResultPayloadFromSteps = (rawSteps: any[]): any | null => {
  if (!Array.isArray(rawSteps)) return null;
  const askDataStepIndex = rawSteps.findIndex(step => String(step?.action || '').toLowerCase() === 'ask_data_query');
  if (askDataStepIndex < 0) return null;

  const askDataStep = rawSteps[askDataStepIndex];
  const outputTexts = Array.isArray(askDataStep?.outputs)
    ? askDataStep.outputs.map((item: any) => item?.content).filter((item: unknown) => typeof item === 'string')
    : [];

  for (const text of outputTexts) {
    try {
      const parsed = JSON.parse(text as string);
      if (parsed && typeof parsed === 'object' && (Array.isArray(parsed.scenes) || parsed.status || parsed.query_id)) {
        return parsed;
      }
    } catch {
      /* ignore non-json tool output */
    }
  }
  return null;
};

const expandAskDataHistorySteps = (rawSteps: any[], sceneNameById: AskDataSceneNameLookup = {}): any[] => {
  const collapsedSteps = collapseDuplicateAskDataSteps(rawSteps);
  const resultPayload = extractAskDataResultPayloadFromSteps(collapsedSteps);
  return resultPayload ? mergeAskDataFallbackSteps(collapsedSteps, resultPayload, sceneNameById) : collapsedSteps;
};

const _formatFileSize = (bytes: number): string => {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(2) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(2) + ' MB';
};

const _getFileTypeLabel = (fileName: string, mimeType?: string): string => {
  const ext = fileName.toLowerCase().split('.').pop() || '';
  if (['xlsx', 'xls'].includes(ext) || mimeType?.includes('spreadsheet') || mimeType?.includes('excel')) {
    return '电子表格';
  }
  if (ext === 'csv' || mimeType?.includes('csv')) {
    return '电子表格';
  }
  if (ext === 'pdf' || mimeType?.includes('pdf')) return 'PDF';
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'].includes(ext) || mimeType?.includes('image')) return '图片';
  if (['doc', 'docx'].includes(ext) || mimeType?.includes('word')) return 'Word 文档';
  if (['txt', 'md'].includes(ext) || mimeType?.includes('text')) return '文本文件';
  if (['json'].includes(ext)) return 'JSON';
  return '文件';
};

const _getFileIcon = (fileName: string, mimeType?: string) => {
  const ext = fileName.toLowerCase().split('.').pop() || '';
  if (
    ['xlsx', 'xls', 'csv'].includes(ext) ||
    mimeType?.includes('spreadsheet') ||
    mimeType?.includes('excel') ||
    mimeType?.includes('csv')
  ) {
    return <FileExcelOutlined className='text-green-600 text-lg' />;
  }
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'].includes(ext) || mimeType?.includes('image')) {
    return <FileImageOutlined className='text-pink-500 text-lg' />;
  }
  if (['ppt', 'pptx'].includes(ext)) {
    return <FilePptOutlined className='text-orange-500 text-lg' />;
  }
  return <FileTextOutlined className='text-blue-500 text-lg' />;
};

// Define file attachment type for user messages
interface FileAttachment {
  name: string;
  size: number;
  type: string;
}

// Define message type for chat
interface ChatMessage {
  id?: string;
  role: 'human' | 'view';
  context: string;
  model_name?: string;
  order?: number;
  thinking?: boolean;
  attachedFile?: FileAttachment;
  attachedKnowledge?: {
    id: number;
    name: string;
    vector_type: string;
    desc?: string;
    owner?: string;
  };
  attachedSkill?: { name: string; id: string };
  attachedDb?: { db_name: string; db_type: string };
  taskPlan?: TaskItem[];
  attachedConnectors?: AttachedConnector[];
  askDataResult?: AskDataQueryResponse;
}

interface ContextStatus {
  state: 'OK' | 'WARNING' | 'ERROR';
  used_tokens: number;
  max_tokens: number;
  usage_percent: number;
  layer: string | null;
}

const CONTEXT_STATUS_STORAGE_KEY = 'dataman:context-status';
const DEFAULT_CONTEXT_STATUS: ContextStatus = {
  state: 'OK',
  used_tokens: 0,
  max_tokens: 0,
  usage_percent: 0,
  layer: null,
};

function normalizeContextStatus(value: unknown): ContextStatus | null {
  if (!value || typeof value !== 'object') return null;
  const status = value as Partial<ContextStatus>;
  const usedTokens = Number(status.used_tokens);
  const maxTokens = Number(status.max_tokens);
  const usagePercent = Number(status.usage_percent);
  if (!Number.isFinite(usedTokens) || !Number.isFinite(maxTokens) || !Number.isFinite(usagePercent)) return null;
  return {
    state: status.state === 'WARNING' || status.state === 'ERROR' ? status.state : 'OK',
    used_tokens: Math.max(0, usedTokens),
    max_tokens: Math.max(0, maxTokens),
    usage_percent: Math.min(Math.max(usagePercent, 0), 100),
    layer: typeof status.layer === 'string' ? status.layer : null,
  };
}

function getInitialContextStatus(): ContextStatus {
  if (typeof window === 'undefined') return DEFAULT_CONTEXT_STATUS;
  try {
    const stored = window.localStorage.getItem(CONTEXT_STATUS_STORAGE_KEY);
    return normalizeContextStatus(stored ? JSON.parse(stored) : null) || DEFAULT_CONTEXT_STATUS;
  } catch {
    return DEFAULT_CONTEXT_STATUS;
  }
}

function persistContextStatus(status: ContextStatus) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(CONTEXT_STATUS_STORAGE_KEY, JSON.stringify(status));
  } catch {
    /* ignore localStorage failures */
  }
}

const CHAT_DIALOGUE_UPSERT_EVENT = 'dataman:chat-dialogue-upsert';
const CHAT_DIALOGUE_REFRESH_EVENT = 'dataman:chat-dialogue-refresh';

function notifyChatDialogueUpsert(convUid: string, userInput: string) {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(
    new CustomEvent(CHAT_DIALOGUE_UPSERT_EVENT, {
      detail: {
        conv_uid: convUid,
        user_input: userInput,
        user_name: '',
        chat_mode: 'chat_react_agent',
        select_param: '',
        app_code: 'chat_react_agent',
      },
    }),
  );
}

function notifyChatDialogueRefresh() {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent(CHAT_DIALOGUE_REFRESH_EVENT));
}

function getAskDataStageAction(): string {
  return 'ask_data_stage';
}

interface ExecutionStep {
  id: string;
  step: number;
  title?: string;
  detail: string;
  status: 'running' | 'done' | 'failed' | 'cancelled';
  action?: string;
  actionInput?: string;
  askDataCallId?: string;
  askDataReused?: boolean;
  parentId?: string;
  todoMeta?: {
    state?: 'init' | 'progress' | 'done';
    done?: number;
    total?: number;
  };
}

interface ExecutionOutput {
  output_type: string;
  content: any;
}

interface ActiveRun {
  controller: AbortController;
  reader: ReadableStreamDefaultReader<Uint8Array> | null;
  convUid: string;
  responseId: string;
  runId: string;
  cancelled: boolean;
  pendingQuestionRequestId?: string | null;
}

const CANCELLED_RESPONSE_TEXT = '任务已取消。';

const cancelTaskItems = (tasks?: TaskItem[]): TaskItem[] =>
  (tasks || []).map(task =>
    task.status === 'pending' || task.status === 'in_progress' ? { ...task, status: 'cancelled' } : task,
  );

interface FilePreview {
  kind: 'table' | 'text';
  file_name?: string;
  file_path?: string;
  columns?: string[];
  rows?: Record<string, any>[];
  text?: string;
  shape?: [number, number];
}

interface ChartPreview {
  chartType?: ChartType;
  data: Array<{ x: string | number; y: number; [key: string]: any }>;
  xField: string;
  yField: string;
  seriesField?: string;
  colorField?: string;
  angleField?: string;
  title?: string;
  description?: string;
  smooth?: boolean;
}

interface Skill {
  id: string;
  name: string;
  description: string;
  type: 'official' | 'personal';
  icon?: string;
}

type ArtifactType = 'file' | 'table' | 'chart' | 'image' | 'code' | 'markdown' | 'summary' | 'html';

interface Artifact {
  id: string;
  type: ArtifactType;
  name: string;
  content: any;
  createdAt: number;
  messageId?: string;
  stepId?: string;
  downloadable?: boolean;
  mimeType?: string;
  size?: number;
  filePath?: string;
  // Chart-specific metadata
  chartType?: ChartType;
  chartConfig?: Partial<ChartConfig>;
}

type RightPanelTab = 'preview' | 'files' | 'charts' | 'tables' | 'analysis' | 'preprocess' | 'summary';

const _convertExecutionToMessageParts = (
  execution:
    | {
        steps: ExecutionStep[];
        outputs: Record<string, ExecutionOutput[]>;
        activeStepId: string | null;
        collapsed: boolean;
      }
    | undefined,
): MessagePart[] => {
  if (!execution || !execution.steps.length) return [];

  return execution.steps.map((step): ToolPart => {
    const outputs = execution.outputs[step.id] || [];
    const outputText = outputs
      .map(o => {
        if (o.output_type === 'text' || o.output_type === 'markdown') {
          return String(o.content);
        }
        if (o.output_type === 'code') {
          return `\`\`\`\n${String(o.content)}\n\`\`\``;
        }
        if (o.output_type === 'table' || o.output_type === 'json') {
          return JSON.stringify(o.content, null, 2);
        }
        if (o.output_type === 'html') {
          return '[HTML Report]';
        }
        return String(o.content);
      })
      .filter(Boolean)
      .join('\n');

    const statusMap: Record<string, ToolStatus> = {
      running: 'running',
      done: 'completed',
      failed: 'error',
      cancelled: 'error',
    };

    const actionLower = (step.action || '').toLowerCase();
    const isSkill =
      actionLower.includes('skill') ||
      actionLower === 'execute_skill_script_file' ||
      actionLower === 'get_skill_resource' ||
      actionLower === 'select_skill' ||
      actionLower === 'load_skill';
    const toolName = isSkill
      ? 'skill'
      : step.title?.toLowerCase().includes('skill')
        ? 'skill'
        : step.title?.toLowerCase().includes('read')
          ? 'read'
          : step.title?.toLowerCase().includes('write')
            ? 'write'
            : step.title?.toLowerCase().includes('code') ||
                step.title?.toLowerCase().includes('execute') ||
                actionLower === 'shell_interpreter'
              ? 'bash'
              : 'task';

    return {
      id: step.id,
      type: 'tool',
      tool: toolName,
      state: {
        status: statusMap[step.status] || 'completed',
        input: { description: step.title || 'Step', detail: step.detail },
        output: outputText || step.detail,
      },
    };
  });
};

// Convert execution data to Manus panel format
const convertToManusFormat = (
  execution:
    | {
        steps: ExecutionStep[];
        outputs: Record<string, ExecutionOutput[]>;
        activeStepId: string | null;
        collapsed: boolean;
        stepThoughts?: Record<string, string>;
      }
    | undefined,
  _userQuery?: string,
  t?: (key: string) => string,
  sceneNameById: AskDataSceneNameLookup = {},
): {
  sections: ThinkingSection[];
  activeStep: ActiveStepInfo | null;
  outputs: ManusExecutionOutput[];
  stepThoughts: Record<string, string>;
} => {
  if (!execution || !execution.steps.length) {
    return { sections: [], activeStep: null, outputs: [], stepThoughts: execution?.stepThoughts || {} };
  }

  // Determine step type from title
  const getStepType = (title?: string, action?: string): StepType => {
    // Check action name first — it's the most reliable indicator
    const actionLower = (action || '').toLowerCase();
    if (
      actionLower.includes('skill') ||
      actionLower === 'execute_skill_script_file' ||
      actionLower === 'get_skill_resource' ||
      actionLower === 'select_skill' ||
      actionLower === 'load_skill'
    )
      return 'skill';
    if (actionLower === 'shell_interpreter') return 'bash';
    if (actionLower === 'ask_data_stage') return 'task';
    if (actionLower === 'question') return 'question';

    const lower = (title || '').toLowerCase();
    if (
      lower.includes('load_skill') ||
      lower.includes('load skill') ||
      lower.includes('execute_skill_script_file') ||
      lower.includes('get_skill_resource') ||
      lower.includes('select_skill')
    )
      return 'skill';
    if (lower.includes('read') || lower.includes('load')) return 'read';
    if (lower.includes('edit')) return 'edit';
    if (lower.includes('write') || lower.includes('save')) return 'write';
    if (lower.includes('bash') || lower.includes('execute') || lower.includes('command') || lower.includes('shell'))
      return 'bash';
    if (lower.includes('grep') || lower.includes('search')) return 'grep';
    if (lower.includes('glob') || lower.includes('find')) return 'glob';
    if (lower.includes('html')) return 'html';
    if (lower.includes('python') || lower.includes('code')) return 'python';
    if (lower.includes('question')) return 'question';
    if (lower.includes('skill')) return 'skill';
    if (lower.includes('task')) return 'task';
    return 'other';
  };

  // Get step status mapping
  const getStepStatus = (status: string): 'pending' | 'running' | 'completed' | 'error' | 'cancelled' => {
    if (status === 'running') return 'running';
    if (status === 'done' || status === 'completed') return 'completed';
    if (status === 'failed' || status === 'error') return 'error';
    if (status === 'cancelled') return 'cancelled';
    return 'pending';
  };

  const isCompletedThinkingPlaceholder = (step: ExecutionStep) => {
    if (step.status === 'running') return false;
    const title = (step.title || '')
      .trim()
      .toLowerCase()
      .replace(/\.{3}$/, '');
    return title === 'thinking' || title === '思考中' || title === '正在思考中';
  };

  // Group steps into sections (for now, create one section with all steps)
  // In a more advanced version, you could group by phase/category
  const steps: ManusExecutionStep[] = execution.steps
    .filter(step => {
      const detail = (step.detail || '').toLowerCase();
      return !detail.includes('action: terminate') && !isCompletedThinkingPlaceholder(step);
    })
    .map(step => {
      const displayStep = normalizeAskDataStepRecord(step, sceneNameById);
      const cleanDetail = displayStep.detail?.replace(/^Thought:.*\n?/gm, '').trim();
      return {
        id: step.id,
        type: getStepType(displayStep.title, displayStep.action),
        title: displayStep.title || `Step ${step.step}`,
        subtitle: cleanDetail?.split('\n')[0]?.slice(0, 80),
        description: cleanDetail || undefined,
        phase: (displayStep as any).phase,
        status: getStepStatus(step.status),
      };
    });

  const sections: ThinkingSection[] = steps.length
    ? [
        {
          id: 'section-execution',
          title: t ? t('execution_steps') : 'Execution Steps',
          isCompleted: steps.every(s => s.status === 'completed' || s.status === 'cancelled'),
          steps,
        },
      ]
    : [];

  // Get active step info
  let activeStep: ActiveStepInfo | null = null;
  if (execution.activeStepId) {
    const step = execution.steps.find(s => s.id === execution.activeStepId);
    if (step && !isCompletedThinkingPlaceholder(step)) {
      const displayStep = normalizeAskDataStepRecord(step, sceneNameById);
      const cleanDetail = displayStep.detail?.replace(/^Thought:.*\n?/gm, '').trim();
      activeStep = {
        id: step.id,
        type: getStepType(displayStep.title, displayStep.action),
        title: displayStep.title || `Step ${step.step}`,
        subtitle: cleanDetail?.split('\n')[0]?.slice(0, 80),
        status: getStepStatus(step.status),
        detail: cleanDetail,
        action: displayStep.action,
        actionInput: displayStep.actionInput,
      };
    }
  }

  // Get outputs for active step
  const selectedStep = execution.activeStepId ? execution.steps.find(s => s.id === execution.activeStepId) : undefined;
  const normalizeActiveOutputs = isAskDataStageStep(selectedStep);
  const outputs: ManusExecutionOutput[] = execution.activeStepId
    ? (execution.outputs[execution.activeStepId] || []).map(o => ({
        output_type: o.output_type as any,
        content: normalizeActiveOutputs ? normalizeAskDataOutputContent(o.content, sceneNameById) : o.content,
        timestamp: Date.now(),
      }))
    : [];

  return { sections, activeStep, outputs, stepThoughts: execution?.stepThoughts || {} };
};

const Playground: NextPage = () => {
  const router = useRouter();
  const { t: translate } = useTranslation();
  const t = translate as unknown as (key: string, options?: Record<string, unknown>) => string;
  const { model, setModel } = useContext(ChatContext);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [askDataSceneNameById, setAskDataSceneNameById] = useState<AskDataSceneNameLookup>({});
  const askDataSceneNameByIdRef = useRef<AskDataSceneNameLookup>({});

  useEffect(() => {
    askDataSceneNameByIdRef.current = askDataSceneNameById;
  }, [askDataSceneNameById]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const pageSize = 100;
      let page = 1;
      const nextLookup: AskDataSceneNameLookup = {};

      while (!cancelled) {
        const response = await listScenes({ page, page_size: pageSize });
        const data = response.data;
        const items = data?.items || [];

        items.forEach((scene: SceneSummary) => {
          if (scene.scene_id && scene.name) nextLookup[scene.scene_id] = scene.name;
        });

        if (!data?.total || page * pageSize >= data.total || items.length === 0) break;
        page += 1;
      }

      if (!cancelled) setAskDataSceneNameById(prev => ({ ...prev, ...nextLookup }));
    })().catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const prompt = router.query.q;
    if (typeof prompt === 'string' && prompt.trim()) {
      setQuery(prompt);
    }
  }, [router.query.q]);

  // Contexts
  const [uploadedFile, setUploadedFile] = useState<any | null>(null);

  // Chat messages state
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const [executionMap, setExecutionMap] = useState<
    Record<
      string,
      {
        steps: ExecutionStep[];
        outputs: Record<string, ExecutionOutput[]>;
        activeStepId: string | null;
        collapsed: boolean;
        stepThoughts: Record<string, string>;
      }
    >
  >({});
  const [activeMessageId, setActiveMessageId] = useState<string | null>(null);
  const [uploadedFilePath, setUploadedFilePath] = useState<string | null>(null);
  const [filePreview, setFilePreview] = useState<FilePreview | null>(null);
  const [_filePreviewLoading, setFilePreviewLoading] = useState(false);
  const [_filePreviewError, setFilePreviewError] = useState<string | null>(null);
  const [chartPreview, setChartPreview] = useState<ChartPreview | null>(null);
  const lastArtifactKeyRef = useRef<string>('');

  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [createdSkillNames, setCreatedSkillNames] = useState<Record<string, string>>({});
  const [_rightPanelTab, setRightPanelTab] = useState<RightPanelTab>('preview');
  const [streamingSummary, setStreamingSummary] = useState<string>('');
  const [_summaryComplete, setSummaryComplete] = useState(false);
  const [_dataAnalysis, setDataAnalysis] = useState<ColumnAnalysis[] | null>(null);
  const [_analysisLoading, setAnalysisLoading] = useState(false);
  const [_showProfessionalReport, _setShowProfessionalReport] = useState(false);
  const [_preprocessedData, _setPreprocessedData] = useState<PreprocessingResult | null>(null);

  const [isSkillPanelOpen, setIsSkillPanelOpen] = useState(false);
  const [selectedSkill, setSelectedSkill] = useState<Skill | null>(null);
  const [skillSearchQuery, setSkillSearchQuery] = useState('');

  const selectedConnectors: ConnectorInstance[] = [];

  // HITL 确认轮询临时关闭：当前不需要写操作前的人工确认能力。
  // 恢复连接器入口时把下一行改回 `loading && selectedConnectors.length > 0` 即可，
  // 后端 _PENDING_CONFIRMATIONS 通道 / ConfirmDialog 组件保持原样未删除。
  const isConfirmPollingActive = false;
  const { pendingConfirmation, approve, deny, dismiss } = useConfirmPolling({
    isActive: isConfirmPollingActive,
  });

  const [selectedStepId, setSelectedStepId] = useState<string | null>(null);
  const [rightPanelCollapsed, setRightPanelCollapsed] = useState(true);
  const [rightPanelView, setRightPanelView] = useState<PanelView>('execution');
  const [previewArtifact, setPreviewArtifact] = useState<Artifact | null>(null);

  // Active round tracking: which view message is currently selected for the right panel
  const [activeViewMsgId, setActiveViewMsgId] = useState<string | null>(null);

  // Track step IDs that belong to a terminate action so we can suppress them
  const terminatedStepIdsRef = useRef<Set<string>>(new Set());
  const preloadedFilePathRef = useRef<string | null>(null);
  const historyLoadSeqRef = useRef(0);
  const activeRunRef = useRef<ActiveRun | null>(null);

  const [historyLoading, setHistoryLoading] = useState(false);
  const [contextStatus, setContextStatus] = useState<ContextStatus>(() => getInitialContextStatus());
  const [pendingQuestion, setPendingQuestion] = useState<{
    request_id: string;
    conv_id: string;
    questions: Array<{
      question: string;
      header?: string;
      options?: Array<{ label: string; description?: string }>;
      multiple?: boolean;
      custom?: boolean;
    }>;
  } | null>(null);
  const [taskPlan, setTaskPlan] = useState<TaskItem[]>([]);

  const resetConversationSurface = useCallback(() => {
    const run = activeRunRef.current;
    if (run) {
      run.cancelled = true;
      run.reader?.cancel().catch(() => undefined);
      run.controller.abort();
      activeRunRef.current = null;
    }
    setMessages([]);
    setQuery('');
    setExecutionMap({});
    setActiveMessageId(null);
    setActiveViewMsgId(null);
    setUploadedFile(null);
    setUploadedFilePath(null);
    setFilePreview(null);
    setFilePreviewError(null);
    setChartPreview(null);
    setArtifacts([]);
    setRightPanelTab('preview');
    setStreamingSummary('');
    setSummaryComplete(false);
    setDataAnalysis(null);
    setPendingQuestion(null);
    setTaskPlan([]);
    setSelectedStepId(null);
    setPreviewArtifact(null);
  }, []);

  const replyQuestion = useCallback(async (requestId: string, answers: string[][]) => {
    const res = await fetch(`${process.env.API_BASE_URL ?? ''}/api/v1/chat/question/${requestId}/reply`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ answers }),
    });
    if (res.ok) {
      setPendingQuestion(null);
    }
  }, []);

  const rejectQuestion = useCallback(async (requestId: string) => {
    const res = await fetch(`${process.env.API_BASE_URL ?? ''}/api/v1/chat/question/${requestId}/reject`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
    if (res.ok) {
      setPendingQuestion(null);
    }
  }, []);

  const requestBackendRunCancel = useCallback((run: ActiveRun, questionRequestId?: string | null) => {
    const baseUrl = process.env.API_BASE_URL ?? '';
    fetch(`${baseUrl}/api/v1/chat/react-agent/cancel`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ conv_uid: run.convUid, run_id: run.runId }),
    }).catch(() => undefined);

    if (questionRequestId) {
      fetch(`${baseUrl}/api/v1/chat/question/${questionRequestId}/reject`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      }).catch(() => undefined);
    }
  }, []);

  const markRunCancelled = useCallback((responseId: string | null) => {
    if (responseId) {
      setExecutionMap(prev => {
        const current = prev[responseId];
        if (!current) return prev;
        const steps = current.steps.map<ExecutionStep>(step =>
          step.status === 'running' ? { ...step, status: 'cancelled' } : step,
        );
        return { ...prev, [responseId]: { ...current, steps } };
      });
      setMessages(prev =>
        prev.map(msg =>
          msg.id === responseId && msg.role === 'view'
            ? {
                ...msg,
                context: msg.context || CANCELLED_RESPONSE_TEXT,
                thinking: false,
                taskPlan: msg.taskPlan ? cancelTaskItems(msg.taskPlan) : msg.taskPlan,
              }
            : msg,
        ),
      );
    }
    setTaskPlan(prev => cancelTaskItems(prev));
    setPendingQuestion(null);
    setStreamingSummary('');
    setSummaryComplete(true);
    setLoading(false);
  }, []);

  const handleCancelRun = useCallback(() => {
    const run = activeRunRef.current;
    const questionRequestId = run?.pendingQuestionRequestId || pendingQuestion?.request_id;

    if (!run) {
      if (questionRequestId) {
        fetch(`${process.env.API_BASE_URL ?? ''}/api/v1/chat/question/${questionRequestId}/reject`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
        }).catch(() => undefined);
      }
      markRunCancelled(null);
      return;
    }

    if (run.cancelled) return;
    run.cancelled = true;
    requestBackendRunCancel(run, questionRequestId);
    run.reader?.cancel().catch(() => undefined);
    run.controller.abort();
    activeRunRef.current = null;
    markRunCancelled(run.responseId);
    notifyChatDialogueRefresh();
  }, [markRunCancelled, pendingQuestion?.request_id, requestBackendRunCancel]);

  const normalizeText = (value: unknown): string => {
    if (typeof value === 'string') return value;
    if (value && typeof value === 'object') {
      const todoValue = (value as Record<string, unknown>).TODO;
      if (typeof todoValue === 'string') return todoValue;
      try {
        return JSON.stringify(value);
      } catch {
        return String(value);
      }
    }
    return value == null ? '' : String(value);
  };

  /** Extract the actual created skill name from shell_interpreter output.
   *  Uses priority-based matching to avoid returning 'skill-creator' (the tool path). */
  const extractCreatedSkillName = (allText: string): string | null => {
    // Priority 1: Skill 'xxx' initialized/packaged (quoted name from output)
    const quotedSkill = allText.match(/[Ss]kill\s+['"]([\w-]+)['"]/);
    if (quotedSkill) return quotedSkill[1];

    // Priority 2: Initializing/Packaging skill: xxx
    const colonSkill = allText.match(/(?:Initializing|Packaging)\s+skill:\s*(?:skills\/)?([\w-]+)/);
    if (colonSkill) return colonSkill[1];

    // Priority 3: Created skill directory: .../skills/xxx
    const createdDir = allText.match(/Created skill directory:.*\/skills\/([\w-]+)/);
    if (createdDir) return createdDir[1];

    // Priority 4: Last skills/xxx path, filtering out 'skill-creator'
    const allPaths = [...allText.matchAll(/skills\/([\w-]+)/g)].map(m => m[1]).filter(name => name !== 'skill-creator');
    if (allPaths.length > 0) return allPaths[allPaths.length - 1];

    return null;
  };

  // Fetch Skills/DBGPTs list
  const { data: skillsList, loading: _loadingSkills } = useRequest(async () => {
    try {
      const response = await axios.get(`${process.env.API_BASE_URL ?? ''}/api/v1/skills/list`);
      // ctx-axios interceptor returns response.data directly
      if (response?.success && Array.isArray(response.data)) {
        return response.data.map((item: any) => ({
          id: String(item.id || item.name),
          name: normalizeText(item.name),
          description: normalizeText(item.description),
          type: item.type === 'official' ? 'official' : 'personal',
          icon:
            item.skill_type === 'data_analysis'
              ? '📊'
              : item.skill_type === 'coding'
                ? '💻'
                : item.skill_type === 'web_search'
                  ? '🔍'
                  : item.skill_type === 'knowledge_qa'
                    ? '📚'
                    : item.skill_type === 'chat'
                      ? '💬'
                      : '⚡',
        })) as Skill[];
      }
      return [];
    } catch (e) {
      console.error('Failed to fetch skills', e);
      return [];
    }
  });

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    if (!router.isReady) return;
    const rawConvId = router.query.id;
    const convId = Array.isArray(rawConvId) ? rawConvId[0] : rawConvId;
    if (convId && convId !== conversationId) {
      void loadConversation(convId);
    } else if (!convId && conversationId) {
      historyLoadSeqRef.current += 1;
      setHistoryLoading(false);
      // URL 中 id 消失（如点击 new_task / 探索广场），清空当前会话状态
      setConversationId(null);
      resetConversationSurface();
    }
    // Only route-id changes should drive history switching; depending on conversationId would clear new chats created on `/`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router.isReady, router.query.id, resetConversationSurface]);

  useEffect(() => {
    const lastView = [...messages].reverse().find(msg => msg.role === 'view');
    if (lastView?.id) {
      setActiveMessageId(lastView.id);
    }
  }, [messages]);

  useEffect(() => {
    const loadPreview = async () => {
      if (!uploadedFilePath) return;
      setFilePreviewLoading(true);
      setFilePreviewError(null);
      try {
        const res = await axios.post(`${process.env.API_BASE_URL ?? ''}/api/v1/resource/file/read`, null, {
          params: {
            conv_uid: conversationId || 'preview',
            file_key: uploadedFilePath,
          },
        });
        if (res.data?.success && res.data?.data) {
          let parsed: any;
          try {
            parsed = JSON.parse(res.data.data);
          } catch {
            parsed = res.data.data;
          }
          if (Array.isArray(parsed) && parsed.length > 0) {
            const columns = Object.keys(parsed[0] || {});
            setFilePreview({
              kind: 'table',
              file_name: uploadedFile?.name,
              file_path: uploadedFilePath,
              columns,
              rows: parsed.slice(0, 50),
              shape: [parsed.length, columns.length],
            });
          } else if (typeof parsed === 'string') {
            setFilePreview({
              kind: 'text',
              file_name: uploadedFile?.name,
              file_path: uploadedFilePath,
              text: parsed,
            });
          } else {
            setFilePreview({
              kind: 'text',
              file_name: uploadedFile?.name,
              file_path: uploadedFilePath,
              text: JSON.stringify(parsed, null, 2),
            });
          }
        } else {
          setFilePreviewError(res.data?.err_msg || '文件预览失败');
        }
      } catch (err: any) {
        setFilePreviewError(err?.message || '文件预览失败');
      } finally {
        setFilePreviewLoading(false);
      }
    };
    loadPreview();
  }, [uploadedFilePath, conversationId, uploadedFile]);

  useEffect(() => {
    if (!filePreview || filePreview.kind !== 'table') {
      setChartPreview(null);
      return;
    }
    const rows = filePreview.rows || [];
    const columns = filePreview.columns || [];
    if (!rows.length || !columns.length) {
      setChartPreview(null);
      return;
    }
    const numericColumns = columns.filter(col => {
      const sample = rows.slice(0, 20).map(row => Number(row[col]));
      const numericCount = sample.filter(val => Number.isFinite(val)).length;
      return numericCount >= Math.max(3, Math.floor(sample.length * 0.6));
    });
    if (!numericColumns.length) {
      setChartPreview(null);
      return;
    }
    const yCol = numericColumns[0];
    const xCol = columns.find(col => col !== yCol) || '__index__';
    const data = rows.slice(0, 60).map((row, idx) => {
      const xVal = xCol === '__index__' ? idx + 1 : row[xCol];
      const yVal = Number(row[yCol]);
      return {
        x: typeof xVal === 'string' || typeof xVal === 'number' ? xVal : String(xVal ?? idx + 1),
        y: Number.isFinite(yVal) ? yVal : 0,
      };
    });
    setChartPreview({
      data,
      xField: 'x',
      yField: 'y',
      title: `${yCol} trend`,
    });
  }, [filePreview]);

  // Auto-analyze data when filePreview updates
  useEffect(() => {
    if (!filePreview || filePreview.kind !== 'table' || !filePreview.rows?.length) {
      setDataAnalysis(null);
      return;
    }

    setAnalysisLoading(true);
    try {
      const analysis = analyzeDataset(filePreview.rows, filePreview.columns);
      setDataAnalysis(analysis);
      // Auto-switch to analysis tab when data is ready
      if (analysis.length > 0) {
        setRightPanelTab('analysis');
      }
    } catch (err) {
      console.error('Data analysis failed:', err);
      setDataAnalysis(null);
    } finally {
      setAnalysisLoading(false);
    }
  }, [filePreview]);

  useEffect(() => {
    if (!activeMessageId || !filePreview) return;
    const artifactKey = `${activeMessageId}:${filePreview.file_path || filePreview.file_name || ''}`;
    if (artifactKey === lastArtifactKeyRef.current) return;
    lastArtifactKeyRef.current = artifactKey;
    const previewStepId = 'client-preview';
    setExecutionMap(prev => {
      const current = prev[activeMessageId] || { steps: [], outputs: {}, activeStepId: null, collapsed: false };
      const hasStep = current.steps.some(step => step.id === previewStepId);
      const nextSteps = hasStep
        ? current.steps.map(step => (step.id === previewStepId ? { ...step, status: 'done' as const } : step))
        : [
            ...current.steps,
            {
              id: previewStepId,
              step: current.steps.length + 1,
              title: 'Preview & Visualize',
              detail: 'Parsed file preview and prepared visual insights.',
              status: 'done' as const,
            },
          ];
      const outputs = { ...current.outputs };
      const previewOutputs: ExecutionOutput[] = [];
      if (filePreview.kind === 'table') {
        previewOutputs.push({
          output_type: 'table',
          content: {
            columns: (filePreview.columns || []).map(col => ({ title: col, dataIndex: col, key: col })),
            rows: filePreview.rows || [],
          },
        });
      } else if (filePreview.kind === 'text') {
        previewOutputs.push({ output_type: 'text', content: filePreview.text || '' });
      }
      if (chartPreview) {
        previewOutputs.push({
          output_type: 'chart',
          content: {
            data: chartPreview.data,
            xField: chartPreview.xField,
            yField: chartPreview.yField,
          },
        });
      }
      outputs[previewStepId] = previewOutputs;
      return {
        ...prev,
        [activeMessageId]: {
          ...current,
          steps: nextSteps,
          outputs,
          activeStepId: previewStepId,
        },
      };
    });
  }, [activeMessageId, filePreview, chartPreview]);

  interface Round {
    humanMsg: ChatMessage | null;
    viewMsg: ChatMessage | null;
  }

  const rounds = useMemo<Round[]>(() => {
    const result: Round[] = [];
    let i = 0;
    while (i < messages.length) {
      const msg = messages[i];
      if (msg.role === 'human') {
        const next = messages[i + 1];
        if (next && next.role === 'view') {
          result.push({ humanMsg: msg, viewMsg: next });
          i += 2;
        } else {
          result.push({ humanMsg: msg, viewMsg: null });
          i += 1;
        }
      } else if (msg.role === 'view') {
        result.push({ humanMsg: null, viewMsg: msg });
        i += 1;
      } else {
        i += 1;
      }
    }
    return result;
  }, [messages]);

  const selectedViewMsgId = useMemo(() => {
    if (activeViewMsgId) {
      const exists = rounds.some(r => r.viewMsg?.id === activeViewMsgId);
      if (exists) return activeViewMsgId;
    }
    const lastRound = rounds[rounds.length - 1];
    return lastRound?.viewMsg?.id || null;
  }, [activeViewMsgId, rounds]);

  const parseCsvLine = (line: string) => {
    const result: string[] = [];
    let current = '';
    let inQuotes = false;
    for (let i = 0; i < line.length; i++) {
      const char = line[i];
      const nextChar = line[i + 1];
      if (char === '"') {
        if (inQuotes && nextChar === '"') {
          current += '"';
          i++;
        } else {
          inQuotes = !inQuotes;
        }
      } else if (char === ',' && !inQuotes) {
        result.push(current);
        current = '';
      } else {
        current += char;
      }
    }
    result.push(current);
    return result.map(val => val.trim());
  };

  const _parseCsvText = (text: string, fileName?: string) => {
    const lines = text.split(/\r?\n/).filter(line => line.trim());
    if (!lines.length) return null;
    const header = parseCsvLine(lines[0]);
    const rows = lines.slice(1, 51).map((line, idx) => {
      const values = parseCsvLine(line);
      const row: Record<string, any> = { id: idx + 1 };
      header.forEach((col, i) => {
        row[col || `col_${i + 1}`] = values[i] ?? '';
      });
      return row;
    });
    return {
      kind: 'table' as const,
      file_name: fileName,
      columns: header.map(col => col || 'Column'),
      rows,
      shape: [lines.length - 1, header.length],
    };
  };

  const _getArtifactName = (outputType: string, content: any): string => {
    if (outputType === 'table') {
      const rowCount = content?.rows?.length || 0;
      const colCount = content?.columns?.length || 0;
      return `Data Table (${rowCount} rows × ${colCount} cols)`;
    }
    if (outputType === 'chart') {
      const chartType = content?.chartType || 'line';
      const chartTypeNames: Record<string, string> = {
        line: 'Line Chart',
        column: 'Column Chart',
        bar: 'Bar Chart',
        pie: 'Pie Chart',
        donut: 'Donut Chart',
        area: 'Area Chart',
        scatter: 'Scatter Plot',
        'dual-axes': 'Dual Axes Chart',
      };
      return content?.title || chartTypeNames[chartType] || 'Chart Visualization';
    }
    if (outputType === 'code') {
      return `Code Snippet`;
    }
    if (outputType === 'image') {
      return content?.name || 'Image';
    }
    if (outputType === 'markdown') {
      const preview = String(content).slice(0, 30);
      return `Document: ${preview}${String(content).length > 30 ? '...' : ''}`;
    }
    if (outputType === 'file') {
      return content?.name || content?.file_name || 'File';
    }
    return `${outputType} output`;
  };

  const extractCodeFileName = (code: string, stepLabel: string, index: number): string => {
    const saveMatch = code.match(/\.to_(?:excel|csv)\s*\(\s*['"]([^'"]+)['"]/);
    if (saveMatch) return saveMatch[1].split('/').pop() || saveMatch[1];
    const openMatch = code.match(/open\s*\(\s*['"]([^'"]+\.(?:py|txt|json|csv|xlsx?))['"]/);
    if (openMatch) return openMatch[1].split('/').pop() || openMatch[1];

    const savefigMatch = code.match(/savefig\s*\(\s*['"]([^'"]+)['"]/);
    if (savefigMatch) return savefigMatch[1].split('/').pop() || savefigMatch[1];

    const readMatch = code.match(/pd\.read_(?:csv|excel)\s*\(\s*['"]([^'"]+)['"]/);
    if (readMatch) {
      const srcName = (readMatch[1].split('/').pop() || readMatch[1]).replace(/\.[^.]+$/, '');
      return `analyze_${srcName}.py`;
    }

    const defMatch = code.match(/def\s+(\w+)\s*\(/);
    if (defMatch) return `${defMatch[1]}.py`;

    const classMatch = code.match(/class\s+(\w+)/);
    if (classMatch) return `${classMatch[1]}.py`;

    if (/import\s+matplotlib|plt\./.test(code)) return `visualization_${index + 1}.py`;
    if (/sns\.|import\s+seaborn/.test(code)) return `chart_${index + 1}.py`;
    if (/pd\.|import\s+pandas/.test(code)) return `data_processing_${index + 1}.py`;

    const label = stepLabel.replace(/[^a-zA-Z0-9_-]/g, '_').slice(0, 30);
    return `${label}_${index}.py`;
  };

  const extractFileReferences = (
    text: string,
  ): Array<{ name: string; downloadable: boolean; size?: number; filePath?: string }> => {
    const refs: Array<{ name: string; downloadable: boolean; size?: number; filePath?: string }> = [];
    const filePattern = /[/\w\-.:]+\.(?:xlsx|xls|csv|py|json|txt|pdf|png|jpg|jpeg|html|md)/gi;
    const matches = text.match(filePattern) || [];
    const seen = new Set<string>();
    matches.forEach(m => {
      const name = m.split('/').pop() || m;
      const lower = name.toLowerCase();
      if (!seen.has(lower)) {
        seen.add(lower);
        // Preserve full path if it looks like an absolute path
        const filePath = m.startsWith('/') ? m : undefined;
        refs.push({ name, downloadable: true, filePath });
      }
    });
    return refs;
  };

  const downloadArtifact = async (artifact: Artifact) => {
    const triggerBlobDownload = (blob: Blob, filename: string) => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    };

    switch (artifact.type) {
      case 'image': {
        const imgUrl =
          typeof artifact.content === 'string'
            ? artifact.content
            : artifact.content?.url || artifact.content?.src || String(artifact.content);
        const resolvedUrl = imgUrl.startsWith('/images/') ? `${process.env.API_BASE_URL || ''}${imgUrl}` : imgUrl;
        try {
          const resp = await fetch(resolvedUrl);
          const blob = await resp.blob();
          const filename = artifact.name || imgUrl.split('/').pop() || 'image.png';
          triggerBlobDownload(blob, filename);
        } catch {
          const a = document.createElement('a');
          a.href = resolvedUrl;
          a.download = artifact.name || 'image.png';
          a.target = '_blank';
          document.body.appendChild(a);
          a.click();
          document.body.removeChild(a);
        }
        break;
      }
      case 'html': {
        const htmlContent =
          typeof artifact.content === 'string'
            ? artifact.content
            : artifact.content?.content || artifact.content?.html || String(artifact.content);
        const blob = new Blob([htmlContent], { type: 'text/html' });
        triggerBlobDownload(blob, artifact.name || 'report.html');
        break;
      }
      case 'code': {
        const blob = new Blob([String(artifact.content)], { type: 'text/plain' });
        triggerBlobDownload(blob, artifact.name || 'code.py');
        break;
      }
      case 'table': {
        const rows = artifact.content?.rows || [];
        const columns = artifact.content?.columns?.map((c: any) => c.dataIndex || c.key || c) || [];
        const csvContent = [
          columns.join(','),
          ...rows.map((row: any) => columns.map((col: string) => JSON.stringify(row[col] ?? '')).join(',')),
        ].join('\n');
        const blob = new Blob([csvContent], { type: 'text/csv' });
        triggerBlobDownload(blob, artifact.name?.replace(/\.\w+$/, '.csv') || 'table.csv');
        break;
      }
      case 'markdown':
      case 'summary': {
        const blob = new Blob([String(artifact.content)], { type: 'text/markdown' });
        triggerBlobDownload(blob, artifact.name || `${artifact.type}.md`);
        break;
      }
      case 'file': {
        const filePath = artifact.content?.file_path || artifact.content?.path || (artifact as any).filePath;
        if (filePath && filePath.includes('/images/')) {
          const imgName = filePath.split('/').pop();
          const resolvedUrl = `${process.env.API_BASE_URL || ''}/images/${imgName}`;
          try {
            const resp = await fetch(resolvedUrl);
            const blob = await resp.blob();
            triggerBlobDownload(blob, artifact.name || imgName || 'file');
          } catch {
            message.warning('文件暂不可下载');
          }
        } else if (filePath) {
          // Download via backend file download endpoint (for agent-created files)
          const downloadUrl = `${process.env.API_BASE_URL || ''}/api/v1/agent/files/download?file_path=${encodeURIComponent(filePath)}`;
          try {
            const resp = await fetch(downloadUrl);
            if (!resp.ok) {
              const errData = await resp.json().catch(() => ({}));
              message.warning(errData.detail || '文件暂不可下载');
              break;
            }
            const blob = await resp.blob();
            triggerBlobDownload(blob, artifact.name || filePath.split('/').pop() || 'file');
          } catch {
            message.warning('文件下载失败');
          }
        } else {
          message.warning('文件暂不可下载');
        }
        break;
      }
      default: {
        const blob = new Blob([JSON.stringify(artifact.content, null, 2)], { type: 'application/json' });
        triggerBlobDownload(blob, artifact.name || 'artifact.json');
      }
    }
  };

  const _copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text).then(() => {
      message.success('Copied to clipboard');
    });
  };

  const _getArtifactIcon = (type: ArtifactType, chartType?: ChartType) => {
    switch (type) {
      case 'table':
        return <TableOutlined className='text-blue-500' />;
      case 'chart':
        if (chartType === 'pie' || chartType === 'donut') {
          return <PieChartOutlined className='text-green-500' />;
        }
        return <BarChartOutlined className='text-green-500' />;
      case 'code':
        return <CodeOutlined className='text-purple-500' />;
      case 'image':
        return <FileImageOutlined className='text-pink-500' />;
      case 'markdown':
        return <FileTextOutlined className='text-orange-500' />;
      case 'summary':
        return <FileTextOutlined className='text-emerald-500' />;
      case 'file':
        return <FileOutlined className='text-gray-500' />;
      default:
        return <FileOutlined className='text-gray-500' />;
    }
  };

  // Build artifacts from execution data — shared between live streaming and history restore
  const buildArtifactsFromExecution = (
    messageId: string,
    execution: {
      steps: ExecutionStep[];
      outputs: Record<string, ExecutionOutput[]>;
    },
    summaryText?: string,
    filePath?: string | null,
  ): Artifact[] => {
    const finalArtifacts: Artifact[] = [];
    const now = Date.now();
    const seenCodeHashes = new Set<string>();

    if (execution) {
      const allSteps = execution.steps || [];
      allSteps.forEach(step => {
        const stepOutputs = execution.outputs[step.id] || [];
        stepOutputs.forEach((output, oIdx) => {
          if (output.output_type === 'code') {
            const codeStr = String(output.content || '').trim();
            const hash = codeStr.slice(0, 200);
            if (codeStr && !seenCodeHashes.has(hash)) {
              seenCodeHashes.add(hash);
              const fileName = extractCodeFileName(codeStr, step.action || step.id, oIdx);
              finalArtifacts.push({
                id: `${messageId}-code-${step.id}-${oIdx}`,
                type: 'code',
                name: fileName,
                content: codeStr,
                createdAt: now,
                messageId,
                stepId: step.id,
                downloadable: true,
              });
            }
          } else if (output.output_type === 'file') {
            finalArtifacts.push({
              id: `${messageId}-file-${step.id}-${oIdx}`,
              type: 'file',
              name: output.content?.name || output.content?.file_name || 'File',
              content: output.content,
              createdAt: now,
              messageId,
              stepId: step.id,
              downloadable: true,
              size: output.content?.size,
            });
          } else if (output.output_type === 'html') {
            const htmlContent =
              typeof output.content === 'string'
                ? output.content
                : output.content?.content || output.content?.html || String(output.content);
            const htmlTitle = output.content?.title || 'Report';
            finalArtifacts.push({
              id: `${messageId}-html-${step.id}-${oIdx}`,
              type: 'html',
              name: `${htmlTitle}.html`,
              content: htmlContent,
              createdAt: now,
              messageId,
              stepId: step.id,
              downloadable: true,
            });
          } else if (output.output_type === 'image') {
            const imgUrl =
              typeof output.content === 'string'
                ? output.content
                : output.content?.url || output.content?.src || String(output.content);
            const imgName = imgUrl.split('/').pop() || `image_${oIdx}.png`;
            const displayName = imgName.replace(/^[a-f0-9]{8}_/, '');
            finalArtifacts.push({
              id: `${messageId}-img-${step.id}-${oIdx}`,
              type: 'image',
              name: displayName,
              content: imgUrl,
              createdAt: now,
              messageId,
              stepId: step.id,
              downloadable: true,
            });
          }
        });

        // For shell_interpreter steps, extract file paths from code/text outputs
        // and create downloadable file artifacts
        if (step.action === 'shell_interpreter') {
          // Match both absolute paths and relative filenames with extensions
          const absPathPattern = /(?:\/[\w\-.]+)+\.\w{1,10}/g;
          const relFilePattern = /(?:>|>>|\btee\b|\btouch\b)\s+([\w\-./ ]+\.\w{1,10})/g;
          const seenFilePaths = new Set<string>();
          stepOutputs.forEach(output => {
            if (output.output_type === 'code' || output.output_type === 'text') {
              const text = String(output.content || '');
              // Look for file creation patterns
              const hasFileCreation = /(?:>|>>|\btee\b|\bcat\b.*>|\bcp\b|\bmv\b|\btouch\b|\becho\b.*>)/.test(text);
              if (hasFileCreation) {
                const foundPaths: string[] = [];
                // Extract absolute paths
                const absMatches = text.match(absPathPattern) || [];
                foundPaths.push(...absMatches);
                // Extract relative paths after redirection operators
                let relMatch;
                while ((relMatch = relFilePattern.exec(text)) !== null) {
                  const p = relMatch[1].trim();
                  if (p && !p.startsWith('/')) foundPaths.push(p);
                }
                foundPaths.forEach(fp => {
                  // Normalize: strip leading ./ if present
                  const normalized = fp.replace(/^\.\//, '');
                  const fileName = normalized.split('/').pop() || normalized;
                  if (!seenFilePaths.has(fileName.toLowerCase())) {
                    seenFilePaths.add(fileName.toLowerCase());
                    const alreadyHasFile = finalArtifacts.some(
                      a => (a.type === 'file' || a.type === 'image') && a.name.toLowerCase() === fileName.toLowerCase(),
                    );
                    if (!alreadyHasFile) {
                      // Use the path as-is; backend resolves relative paths against pilot/tmp
                      finalArtifacts.push({
                        id: `${messageId}-shellfile-${step.id}-${fileName}`,
                        type: 'file',
                        name: fileName,
                        content: { name: fileName, file_path: normalized },
                        createdAt: now,
                        messageId,
                        stepId: step.id,
                        downloadable: true,
                        filePath: normalized,
                      });
                    }
                  }
                });
              }
            }
          });
        }
      });
    }

    if (summaryText) {
      const fileRefs = extractFileReferences(summaryText);
      fileRefs.forEach((ref, idx) => {
        const alreadyExists = finalArtifacts.some(a => a.name.toLowerCase() === ref.name.toLowerCase());
        if (!alreadyExists) {
          finalArtifacts.push({
            id: `${messageId}-fileref-${idx}`,
            type: 'file',
            name: ref.name,
            content: { name: ref.name, file_path: ref.filePath },
            createdAt: now,
            messageId,
            downloadable: ref.downloadable,
            filePath: ref.filePath,
            size: ref.size,
          });
        }
      });
    }

    if (filePath) {
      const uploadName = filePath.split('/').pop() || 'uploaded_file';
      const alreadyExists = finalArtifacts.some(a => a.name.toLowerCase() === uploadName.toLowerCase());
      if (!alreadyExists) {
        finalArtifacts.push({
          id: `${messageId}-upload`,
          type: 'file',
          name: uploadName,
          content: { name: uploadName, file_path: filePath },
          createdAt: now,
          messageId,
          downloadable: true,
        });
      }
    }

    // Deduplicate: for artifacts with the same name+type, keep only the last one
    const deduped: Artifact[] = [];
    const seen = new Map<string, number>();
    for (let i = finalArtifacts.length - 1; i >= 0; i--) {
      const key = `${finalArtifacts[i].type}:${finalArtifacts[i].name}`;
      if (!seen.has(key)) {
        seen.set(key, i);
        deduped.unshift(finalArtifacts[i]);
      }
    }

    return deduped;
  };

  const handleStart = async (inputQuery = query, overrideFile?: File | null, overrideSkill?: Skill | null) => {
    const effectiveFile = overrideFile !== undefined ? overrideFile : uploadedFile;
    const effectiveSkill = overrideSkill !== undefined ? overrideSkill : selectedSkill;
    if (loading) {
      handleCancelRun();
      return;
    }
    if (!inputQuery.trim() && !effectiveFile) return;

    let finalQuery = inputQuery;
    const appCode = 'chat_react_agent';
    const chatMode = 'chat_react_agent';
    let currentUploadedFilePath = null;

    // Handle File Upload if present
    if (preloadedFilePathRef.current) {
      // Example file already copied to server - skip upload
      currentUploadedFilePath = preloadedFilePathRef.current;
      setUploadedFilePath(currentUploadedFilePath);
      preloadedFilePathRef.current = null;
      finalQuery = inputQuery || 'Analyze the uploaded file.';
    } else if (effectiveFile) {
      const formData = new FormData();
      formData.append('file', effectiveFile);

      try {
        const uploadRes = await axios.post(`${process.env.API_BASE_URL ?? ''}/api/v1/python/file/upload`, formData, {
          headers: { 'Content-Type': 'multipart/form-data' },
        });

        const resData = uploadRes.data;
        // Handle both wrapped Result {success, data} and raw string path
        if (resData?.success && resData?.data) {
          currentUploadedFilePath = resData.data;
          setUploadedFilePath(currentUploadedFilePath);
          finalQuery = inputQuery || 'Analyze the uploaded Excel file.';
        } else if (typeof resData === 'string' && resData.length > 0) {
          // Backend returned the file path directly as a string
          currentUploadedFilePath = resData;
          setUploadedFilePath(currentUploadedFilePath);
          finalQuery = inputQuery || 'Analyze the uploaded Excel file.';
        } else {
          const errMsg = resData?.err_msg || resData?.message || 'Unknown error';
          message.error('File upload failed: ' + errMsg);
          return;
        }
      } catch (uploadErr: any) {
        console.error('[Upload] error:', uploadErr);
        const errDetail =
          uploadErr?.response?.data?.err_msg ||
          uploadErr?.response?.data?.message ||
          uploadErr?.message ||
          'Network error';
        message.error('File upload failed: ' + errDetail);
        return;
      }
    } else {
      if (uploadedFilePath) {
        setUploadedFilePath(null);
        setFilePreview(null);
      }
    }

    // Prepare conversation ID
    const currentConvId = conversationId || generateUUID();
    const isNewConversation = !conversationId;
    if (!conversationId) {
      setConversationId(currentConvId);
    }
    if (isNewConversation) {
      notifyChatDialogueUpsert(currentConvId, inputQuery);
    }

    // Calculate current order
    const currentOrder = Math.floor(messages.length / 2) + 1;

    const responseId = generateUUID();
    const runId = generateUUID();

    const humanId = generateUUID();

    // Add user message and AI placeholder message
    setMessages(prev => [
      ...prev,
      {
        id: humanId,
        role: 'human',
        context: inputQuery,
        order: currentOrder,
        attachedFile: effectiveFile
          ? {
              name: effectiveFile.name,
              size: effectiveFile.size,
              type: effectiveFile.type,
            }
          : undefined,
        attachedSkill: effectiveSkill ? { name: effectiveSkill.name, id: effectiveSkill.id } : undefined,
        attachedConnectors:
          selectedConnectors.length > 0
            ? selectedConnectors.map(c => ({
                id: c.id,
                connector_type: c.connector_type,
                display_name: c.display_name,
              }))
            : undefined,
      },
      {
        id: responseId,
        role: 'view',
        context: '',
        order: currentOrder,
        thinking: true,
      },
    ]);

    setLoading(true);
    setQuery(''); // Clear input
    setStreamingSummary('');
    setActiveViewMsgId(responseId); // Auto-switch right panel to new round

    const controller = new AbortController();
    const activeRun: ActiveRun = {
      controller,
      reader: null,
      convUid: currentConvId,
      responseId,
      runId,
      cancelled: false,
    };
    activeRunRef.current = activeRun;

    const isCurrentRun = () => activeRunRef.current?.runId === runId && !activeRunRef.current.cancelled;

    terminatedStepIdsRef.current.clear();
    setExecutionMap(prev => ({
      ...prev,
      [responseId]: {
        steps: [],
        outputs: {},
        activeStepId: null,
        collapsed: false,
        stepThoughts: {},
      },
    }));
    setActiveMessageId(responseId);

    const settleResponseState = (runningStepStatus: 'done' | 'failed' | 'cancelled' = 'done') => {
      const stillCurrent = activeRunRef.current?.runId === runId;
      if (!stillCurrent && runningStepStatus !== 'cancelled') return;
      setExecutionMap(prev => {
        const current = prev[responseId];
        if (!current) return prev;
        const steps = current.steps.map<ExecutionStep>(step => {
          if (step.status !== 'running') return step;
          return { ...step, status: runningStepStatus };
        });
        return { ...prev, [responseId]: { ...current, steps } };
      });
      setMessages(prev =>
        prev.map(msg => (msg.id === responseId && msg.role === 'view' ? { ...msg, thinking: false } : msg)),
      );
      if (stillCurrent) {
        activeRunRef.current = null;
      }
      setLoading(false);
    };

    // Build ext_info once so file, skill, and connector context stays stable during this send.
    const extInfo: Record<string, any> = {
      client_run_id: runId,
      ...(currentUploadedFilePath ? { file_path: currentUploadedFilePath } : {}),
      ...(effectiveSkill ? { skill_id: effectiveSkill.id, skill_name: effectiveSkill.name } : {}),
      ...(selectedConnectors.length > 0 ? { connector_ids: selectedConnectors.map(c => c.id) } : {}),
    };
    const selectParam = appCode === 'chat_react_agent' ? '' : appCode;

    try {
      const response = await fetch(`${process.env.API_BASE_URL ?? ''}/api/v1/chat/react-agent`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          conv_uid: currentConvId,
          chat_mode: chatMode,
          model_name: model,
          user_input: finalQuery,
          temperature: 0.6,
          max_new_tokens: 4000,
          select_param: selectParam,
          ext_info: extInfo,
        }),
        signal: controller.signal,
      });

      if (!response.body) {
        throw new Error('No response body');
      }

      const reader = response.body.getReader();
      activeRun.reader = reader;
      const decoder = new TextDecoder('utf-8');
      let buffer = '';

      const processEvent = (raw: string) => {
        if (!raw.startsWith('data:')) return;
        const data = raw.slice(5).trim();
        if (!data) return;
        let payload: any;
        try {
          payload = JSON.parse(data);
        } catch (_err) {
          return;
        }
        if (!isCurrentRun()) return;
        if (payload.type === 'context.status') {
          const budget = Number(payload.budget ?? 0);
          if (!Number.isFinite(budget) || budget <= 0) {
            return;
          }
          const stateMap: Record<string, 'OK' | 'WARNING' | 'ERROR'> = {
            normal: 'OK',
            warning: 'WARNING',
            error: 'ERROR',
            critical: 'ERROR',
            overflow: 'ERROR',
          };
          const nextContextStatus: ContextStatus = {
            state: stateMap[payload.state] || 'OK',
            used_tokens: payload.used ?? 0,
            max_tokens: budget,
            usage_percent: (payload.ratio ?? 0) * 100,
            layer: payload.compact_layer ?? null,
          };
          setContextStatus(nextContextStatus);
          persistContextStatus(nextContextStatus);
          return;
        }
        if (payload.type === 'ask_data.stage') {
          const callId = getAskDataCallId(payload);
          const id = getAskDataStageId(callId, payload.stage);
          const eventSceneId = textValue(payload.scene_id);
          const eventSceneName = textValue(payload.scene_name || payload.name || payload.display_name);
          const sceneNameLookup =
            eventSceneId && eventSceneName
              ? { ...askDataSceneNameByIdRef.current, [eventSceneId]: eventSceneName }
              : askDataSceneNameByIdRef.current;
          if (eventSceneId && eventSceneName && askDataSceneNameByIdRef.current[eventSceneId] !== eventSceneName) {
            askDataSceneNameByIdRef.current = sceneNameLookup;
            setAskDataSceneNameById(prev => ({ ...prev, [eventSceneId]: eventSceneName }));
          }
          const stageSceneId = eventSceneId || getAskDataStageSceneId(payload.stage, payload.title, payload.detail);
          const stageTitle = normalizeAskDataVisibleText(payload.title, sceneNameLookup, stageSceneId) || payload.title;
          const stageDetailText = normalizeAskDataVisibleText(payload.detail, sceneNameLookup, stageSceneId);
          const stageStatus: ExecutionStep['status'] =
            payload.status === 'failed' || payload.status === 'error'
              ? 'failed'
              : payload.status === 'done' || payload.status === 'succeeded'
                ? 'done'
                : 'running';
          const stageDetail = formatStageDetailWithDuration(stageDetailText, payload.duration_ms);
          const stageOutputs = buildAskDataStageOutput(
            {
              stage: payload.stage,
              title: stageTitle,
              detail: stageDetailText,
              status: payload.status || stageStatus,
              duration_ms: payload.duration_ms,
              scene: eventSceneId ? { scene_id: eventSceneId, name: eventSceneName } : undefined,
            },
            sceneNameLookup,
          );
          setExecutionMap(prev => {
            const current = prev[responseId] || {
              steps: [],
              outputs: {},
              activeStepId: null,
              collapsed: false,
              stepThoughts: {},
            };
            const steps = current.steps.some(step => step.id === id)
              ? current.steps.map(step =>
                  step.id === id
                    ? { ...step, title: stageTitle, detail: stageDetail, status: stageStatus }
                    : step.status === 'running'
                      ? { ...step, status: 'done' as const }
                      : step,
                )
              : [
                  ...current.steps.map(step =>
                    step.status === 'running' ? { ...step, status: 'done' as const } : step,
                  ),
                  {
                    id,
                    step: current.steps.length + 1,
                    title: stageTitle || '正在查询业务数据',
                    detail: stageDetail,
                    status: stageStatus,
                    action: getAskDataStageAction(),
                    askDataCallId: callId,
                    parentId: `ask-data-${callId}`,
                  },
                ];
            return {
              ...prev,
              [responseId]: {
                ...current,
                steps,
                outputs: {
                  ...current.outputs,
                  [id]: stageOutputs,
                },
              },
            };
          });
          return;
        }
        if (payload.type === 'ask_data.result') {
          const askDataResult = payload as AskDataQueryResponse;
          const resultSceneNameById = buildAskDataSceneNameLookup(askDataResult, askDataSceneNameByIdRef.current);
          askDataSceneNameByIdRef.current = { ...askDataSceneNameByIdRef.current, ...resultSceneNameById };
          setAskDataSceneNameById(prev => ({ ...prev, ...resultSceneNameById }));
          setExecutionMap(prev => {
            const current = prev[responseId];
            if (!current) return prev;
            const stepsWithAskDataStages = mergeAskDataFallbackSteps(current.steps, askDataResult, resultSceneNameById);
            const outputsWithAskDataStages = stepsWithAskDataStages.reduce<Record<string, ExecutionOutput[]>>(
              (acc, step: any) => {
                if (
                  String(step?.id || '').startsWith('ask-data-') &&
                  Array.isArray(step.outputs) &&
                  step.outputs.length
                ) {
                  acc[step.id] = step.outputs;
                }
                return acc;
              },
              { ...current.outputs },
            );
            return {
              ...prev,
              [responseId]: {
                ...current,
                steps: stepsWithAskDataStages.map(step =>
                  step.action === 'ask_data_query' && step.status === 'running' ? { ...step, status: 'done' } : step,
                ),
                outputs: outputsWithAskDataStages,
              },
            };
          });
          setMessages(prev =>
            prev.map(msg =>
              msg.id === responseId && msg.role === 'view'
                ? { ...msg, askDataResult, context: askDataResult.answer || msg.context }
                : msg,
            ),
          );
          return;
        }
        if (payload.type === 'question.asked') {
          activeRun.pendingQuestionRequestId = payload.request_id;
          setPendingQuestion(payload);
          return;
        }
        if (payload.type === 'question.replied' || payload.type === 'question.rejected') {
          activeRun.pendingQuestionRequestId = null;
          setPendingQuestion(null);
          return;
        }
        if (payload.type === 'plan.update') {
          if (Array.isArray(payload.tasks)) {
            const nextTasks = payload.tasks as TaskItem[];
            setTaskPlan(nextTasks);
            setMessages(prev =>
              prev.map(msg => {
                if (msg.id !== responseId || msg.role !== 'view') return msg;
                return { ...msg, taskPlan: nextTasks };
              }),
            );
          }
          return;
        }
        if (payload.type === 'run.cancelled') {
          activeRun.cancelled = true;
          activeRunRef.current = null;
          markRunCancelled(responseId);
          notifyChatDialogueRefresh();
          return;
        }
        if (payload.type === 'step.start') {
          const id = payload.id || `${payload.step}`;
          if (terminatedStepIdsRef.current.has(id)) return;
          setExecutionMap(prev => {
            const current = prev[responseId] || {
              steps: [],
              outputs: {},
              activeStepId: null,
              collapsed: false,
              stepThoughts: {},
            };
            const existingThoughts = current.stepThoughts || {};
            const nextThoughts = existingThoughts;
            // Check if step already exists - if so, update it (especially phase) instead of creating duplicate
            const existingStepIndex = current.steps.findIndex(s => s.id === id);
            let nextSteps;
            if (existingStepIndex >= 0) {
              // Update existing step with new title/phase
              nextSteps = current.steps.map((step, idx) =>
                idx === existingStepIndex
                  ? {
                      ...step,
                      title: payload.title,
                      detail: payload.detail,
                      phase: payload.phase,
                      todoMeta: payload.todo_meta || step.todoMeta,
                      status: 'running' as const,
                    }
                  : step.status === 'running'
                    ? { ...step, status: 'done' as const }
                    : step,
              );
            } else {
              // New step - mark running steps as done and add new step
              nextSteps = [
                ...current.steps.map(item => (item.status === 'running' ? { ...item, status: 'done' as const } : item)),
                {
                  id,
                  step: payload.step,
                  title: payload.title,
                  detail: payload.detail,
                  phase: payload.phase,
                  todoMeta: payload.todo_meta,
                  status: 'running' as const,
                  action: payload.action,
                },
              ];
            }
            return {
              ...prev,
              [responseId]: {
                ...current,
                steps: nextSteps,
                outputs: { ...current.outputs, [id]: current.outputs[id] || [] },
                stepThoughts: nextThoughts,
                // Only auto-focus for existing step updates.
                // New placeholder steps wait for step.meta to get real content before stealing focus.
                activeStepId: existingStepIndex >= 0 ? id : current.activeStepId || id,
              },
            };
          });
          setActiveMessageId(responseId);
        } else if (payload.type === 'step.meta') {
          if (payload.action && payload.action.toLowerCase() === 'terminate') {
            terminatedStepIdsRef.current.add(payload.id);
            setExecutionMap(prev => {
              const current = prev[responseId];
              if (!current) return prev;
              const nextSteps = current.steps.filter(item => item.id !== payload.id);
              const nextActiveStepId = current.activeStepId === payload.id ? null : current.activeStepId;
              return {
                ...prev,
                [responseId]: { ...current, steps: nextSteps, activeStepId: nextActiveStepId },
              };
            });
            return;
          }
          // Clear manual step selection so the right panel auto-tracks this step
          if (payload.action) {
            setSelectedStepId(null);
          }
          setExecutionMap(prev => {
            const current = prev[responseId];
            if (!current) return prev;
            // Build detail from action only (thought goes to stepThoughts)
            let nextSteps = current.steps.map(item => {
              if (item.id !== payload.id) return item;
              const parts = [] as string[];
              if (payload.action) {
                parts.push(`Action: ${payload.action}`);
                if (
                  payload.action !== 'code_interpreter' &&
                  payload.action !== 'shell_interpreter' &&
                  payload.action_input
                ) {
                  const inputStr =
                    typeof payload.action_input === 'string'
                      ? payload.action_input
                      : JSON.stringify(payload.action_input, null, 2);
                  parts.push(`Action Input: ${inputStr}`);
                }
              }
              return {
                ...item,
                title: payload.title || item.title,
                detail: parts.join('\n') || item.detail,
                action: payload.action || item.action,
                actionInput: payload.action_input || item.actionInput,
                askDataCallId: payload.ask_data_call_id || item.askDataCallId,
                askDataReused: Boolean(payload.ask_data_reused || item.askDataReused),
                todoMeta: payload.todo_meta || item.todoMeta,
              };
            });
            // Route model-provided action display fields to the subtle status row.
            const displayThought = payload.action_intention
              ? payload.action_reason
                ? `${payload.action_intention}\n${payload.action_reason}`
                : payload.action_intention
              : payload.thought;
            const nextThoughts = displayThought
              ? {
                  ...current.stepThoughts,
                  [payload.id]: displayThought,
                }
              : current.stepThoughts;
            let nextActiveStepId = payload.action ? payload.id : current.activeStepId;
            if (String(payload.action || '').toLowerCase() === 'ask_data_query') {
              const question = normalizeAskDataActionInput(payload.action_input);
              const existingAskDataStep = nextSteps.find(
                item =>
                  item.id !== payload.id &&
                  String(item.action || '').toLowerCase() === 'ask_data_query' &&
                  question &&
                  normalizeAskDataActionInput(item.actionInput) === question,
              );
              if (payload.ask_data_reused || existingAskDataStep) {
                nextSteps = nextSteps.filter(item => item.id !== payload.id);
                nextActiveStepId = existingAskDataStep?.id || current.activeStepId;
              } else if (payload.ask_data_call_id) {
                const callId = getAskDataCallId({ call_id: payload.ask_data_call_id });
                const stagePrefix = `ask-data-${callId}-`;
                const parentStep = nextSteps.find(item => item.id === payload.id);
                const stepsWithoutParent = nextSteps.filter(item => item.id !== payload.id);
                const firstStageIndex = stepsWithoutParent.findIndex(item => item.id.startsWith(stagePrefix));
                if (parentStep && firstStageIndex >= 0) {
                  nextSteps = [
                    ...stepsWithoutParent.slice(0, firstStageIndex),
                    parentStep,
                    ...stepsWithoutParent.slice(firstStageIndex),
                  ];
                }
              }
              nextSteps = nextSteps.map((item, index) => ({ ...item, step: index + 1 }));
            }
            return {
              ...prev,
              [responseId]: {
                ...current,
                steps: nextSteps,
                stepThoughts: nextThoughts,
                activeStepId: nextActiveStepId,
              },
            };
          });
        } else if (payload.type === 'step.output') {
          if (terminatedStepIdsRef.current.has(payload.id || '')) return;
          setExecutionMap(prev => {
            const current = prev[responseId];
            if (!current) return prev;
            const targetId = current.activeStepId;
            if (!targetId) return prev;
            const nextSteps = current.steps.map(item => {
              if (item.id !== targetId) return item;
              const detail = `${item.detail}\n${payload.detail}`.trim();
              return { ...item, detail };
            });
            return { ...prev, [responseId]: { ...current, steps: nextSteps } };
          });
        } else if (payload.type === 'step.chunk') {
          const id = payload.id;
          if (terminatedStepIdsRef.current.has(id || '')) return;
          setExecutionMap(prev => {
            const current = prev[responseId];
            if (!current) return prev;
            const targetId = id || current.activeStepId;
            if (!targetId) return prev;
            const list = current.outputs[targetId] ? [...current.outputs[targetId]] : [];
            list.push({ output_type: payload.output_type, content: payload.content });
            return {
              ...prev,
              [responseId]: {
                ...current,
                outputs: { ...current.outputs, [targetId]: list },
              },
            };
          });

          // Artifacts are now generated at task completion (final event),
          // not during streaming — to avoid showing intermediate outputs as artifacts
        } else if (payload.type === 'step.done') {
          const id = payload.id;
          if (terminatedStepIdsRef.current.has(id || '')) return;
          setExecutionMap(prev => {
            const current = prev[responseId];
            if (!current) return prev;
            const targetId = id || current.activeStepId;
            if (!targetId) return prev;
            const nextSteps = current.steps.map(item =>
              item.id === targetId ? { ...item, status: payload.status || 'done' } : item,
            );
            return { ...prev, [responseId]: { ...current, steps: nextSteps } };
          });
        } else if (payload.type === 'step.thought') {
          const content = payload.content || '';
          let normalizedThought = '';
          if (typeof content === 'string') {
            normalizedThought = content;
          } else if (content && typeof content === 'object') {
            const todoValue = (content as Record<string, unknown>).TODO;
            if (typeof todoValue === 'string') {
              normalizedThought = todoValue;
            } else {
              try {
                normalizedThought = JSON.stringify(content);
              } catch {
                normalizedThought = String(content);
              }
            }
          }
          if (normalizedThought) {
            setExecutionMap(prev => {
              const current = prev[responseId];
              if (!current) return prev;
              const targetId = payload.id || current.activeStepId || 'initial';
              return {
                ...prev,
                [responseId]: {
                  ...current,
                  stepThoughts: {
                    ...current.stepThoughts,
                    [targetId]: (current.stepThoughts?.[targetId] || '') + normalizedThought,
                  },
                },
              };
            });
          }
        } else if (payload.type === 'final') {
          settleResponseState();
          notifyChatDialogueRefresh();
          setMessages(prev =>
            prev.map(msg => {
              if (msg.id !== responseId || msg.role !== 'view') return msg;
              return {
                ...msg,
                context: cleanFinalContent(payload.content || ''),
                thinking: false,
              };
            }),
          );
          setTaskPlan([]);
          setActiveMessageId(responseId);

          if (payload.content && payload.content.trim()) {
            setStreamingSummary('');
            setSummaryComplete(false);
            setRightPanelTab('summary');
            setRightPanelView('summary');

            const summaryText = cleanFinalContent(payload.content);
            const streamInterval = setInterval(() => {
              setStreamingSummary(prev => {
                if (prev.length >= summaryText.length) {
                  clearInterval(streamInterval);
                  setSummaryComplete(true);

                  setExecutionMap(currentExecMap => {
                    const execution = currentExecMap[responseId];
                    const deduped = buildArtifactsFromExecution(
                      responseId,
                      execution || { steps: [], outputs: {} },
                      summaryText,
                      uploadedFilePath,
                    );

                    setArtifacts(prevArtifacts => {
                      const filtered = prevArtifacts.filter(a => a.messageId !== responseId);
                      const newArtifacts = [...filtered, ...deduped];

                      // Auto-select the first HTML artifact for preview, or image if no HTML
                      const htmlArtifact = deduped.find(a => a.type === 'html');
                      if (htmlArtifact) {
                        setPreviewArtifact(htmlArtifact as Artifact);
                        setRightPanelView('html-preview');
                      } else {
                        const imgArtifact = deduped.find(a => a.type === 'image');
                        if (imgArtifact) {
                          setPreviewArtifact(imgArtifact as Artifact);
                          setRightPanelView('image-preview');
                        }
                      }

                      return newArtifacts;
                    });

                    // Detect skill creation from shell_interpreter steps
                    if (execution) {
                      const isSkillPackageStep = (s: ExecutionStep) => {
                        if (s.action !== 'shell_interpreter') return false;
                        // Check detail, actionInput, and outputs for package_skill/init_skill
                        const detailHas = s.detail?.includes('package_skill') || s.detail?.includes('init_skill');
                        const inputHas =
                          s.actionInput?.includes('package_skill') || s.actionInput?.includes('init_skill');
                        const outputTexts = (execution.outputs[s.id] || []).map(o => String(o.content)).join(' ');
                        const outputHas =
                          outputTexts.includes('package_skill') ||
                          outputTexts.includes('init_skill') ||
                          outputTexts.includes('Successfully packaged');
                        return detailHas || inputHas || outputHas;
                      };
                      const skillStep = (execution.steps || []).find(isSkillPackageStep);
                      if (skillStep) {
                        // Extract skill name from actionInput, detail, or outputs
                        const allText = [
                          skillStep.actionInput || '',
                          skillStep.detail || '',
                          ...(execution.outputs[skillStep.id] || []).map(o => String(o.content)),
                        ].join(' ');
                        const skillName = extractCreatedSkillName(allText);
                        if (skillName) {
                          setCreatedSkillNames(prev => ({ ...prev, [responseId]: skillName }));
                          setRightPanelView('skill-preview');
                        }
                      }
                    }

                    return currentExecMap;
                  });

                  return prev;
                }
                const chunkSize = Math.min(3, summaryText.length - prev.length);
                return prev + summaryText.slice(prev.length, prev.length + chunkSize);
              });
            }, 15);
          }
        } else if (payload.type === 'done') {
          settleResponseState();
        }
      };

      // eslint-disable-next-line no-constant-condition
      while (true) {
        if (!isCurrentRun()) break;
        const { done, value } = await reader.read();
        if (!isCurrentRun()) break;
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split('\n\n');
        buffer = parts.pop() || '';
        parts.forEach(processEvent);
      }
      if (buffer.trim() && isCurrentRun()) {
        processEvent(buffer);
      }
      if (isCurrentRun()) {
        settleResponseState();
        notifyChatDialogueRefresh();
      }
    } catch (err: any) {
      if (err?.name === 'AbortError' || activeRun.cancelled) {
        markRunCancelled(responseId);
        return;
      }
      if (!isCurrentRun()) return;
      settleResponseState('failed');
      notifyChatDialogueRefresh();
      message.error(err?.message || 'Failed to get response');
      setMessages(prev => {
        const newMessages = [...prev];
        const lastMsg = newMessages[newMessages.length - 1];
        if (lastMsg && lastMsg.role === 'view') {
          lastMsg.context = err?.message || 'Error occurred';
          lastMsg.thinking = false;
        }
        return newMessages;
      });
    }
  };

  // Clear chat history
  const restoreFromHistory = (
    historyMessages: Array<{ role: string; context: string; order?: number; model_name?: string }>,
  ) => {
    setExecutionMap({});
    setActiveMessageId(null);
    setActiveViewMsgId(null);
    setArtifacts([]);
    setStreamingSummary('');
    setSummaryComplete(false);

    const newMessages: ChatMessage[] = [];
    const newExecutionMap: typeof executionMap = {};
    const allArtifacts: Artifact[] = [];
    const restoredSkillNames: Record<string, string> = {};

    historyMessages.forEach(msg => {
      if (msg.role === 'human') {
        newMessages.push({ id: generateUUID(), role: 'human', context: msg.context, order: msg.order });
      } else if (msg.role === 'view') {
        const viewId = generateUUID();
        let payload: any = null;
        try {
          payload = JSON.parse(msg.context);
        } catch {
          /* ignore parse failure */
        }

        if (payload && payload.version === 1 && payload.type === 'react-agent') {
          const historyPayloadSteps = expandAskDataHistorySteps(payload.steps || [], askDataSceneNameByIdRef.current);
          const steps: ExecutionStep[] = historyPayloadSteps.map((s: any, idx: number) => ({
            id: s.id || `history-step-${idx}`,
            step: idx + 1,
            title: s.title || s.action || `Step ${idx + 1}`,
            detail: s.detail || '',
            status: (s.status === 'cancelled' ? 'cancelled' : s.status === 'failed' ? 'failed' : 'done') as
              | 'done'
              | 'failed'
              | 'cancelled',
            action: s.action,
            actionInput: s.action_input || undefined,
            askDataCallId: s.ask_data_call_id || undefined,
            askDataReused: Boolean(s.ask_data_reused),
            parentId: s.parent_id || undefined,
            todoMeta: s.todo_meta || undefined,
          }));

          const outputs: Record<string, ExecutionOutput[]> = {};
          const stepThoughts: Record<string, string> = {};

          historyPayloadSteps.forEach((s: any, idx: number) => {
            const stepId = s.id || `history-step-${idx}`;
            if (Array.isArray(s.outputs)) {
              outputs[stepId] = s.outputs.map((o: any) => ({
                output_type: o.output_type || 'text',
                content: o.content,
              }));
            }
            if ((s.action === 'code_interpreter' || s.action === 'shell_interpreter') && s.action_input) {
              const existingOutputs = outputs[stepId] || [];
              const hasCode = existingOutputs.some((o: ExecutionOutput) => o.output_type === 'code');
              if (!hasCode) {
                try {
                  const input = typeof s.action_input === 'string' ? JSON.parse(s.action_input) : s.action_input;
                  if (input && input.code) {
                    outputs[stepId] = [{ output_type: 'code', content: input.code }, ...existingOutputs];
                  }
                } catch {
                  /* ignore */
                }
              }
            }
            const historyThought = s.action_intention
              ? s.action_reason
                ? `${s.action_intention}\n${s.action_reason}`
                : s.action_intention
              : s.thought;
            if (historyThought) {
              if (typeof historyThought === 'string') {
                stepThoughts[stepId] = historyThought;
              } else if (typeof historyThought === 'object') {
                const todoValue = (historyThought as Record<string, unknown>).TODO;
                if (typeof todoValue === 'string') {
                  stepThoughts[stepId] = todoValue;
                } else {
                  try {
                    stepThoughts[stepId] = JSON.stringify(historyThought);
                  } catch {
                    stepThoughts[stepId] = String(historyThought);
                  }
                }
              }
            }
          });

          newExecutionMap[viewId] = {
            steps,
            outputs,
            activeStepId: steps.length > 0 ? steps[steps.length - 1].id : null,
            collapsed: false,
            stepThoughts,
          };

          const finalContent = cleanFinalContent(payload.final_content || '');

          const restoredArtifacts = buildArtifactsFromExecution(viewId, { steps, outputs }, finalContent, null);
          allArtifacts.push(...restoredArtifacts);

          // Detect skill creation from restored execution
          const isSkillPackageStep = (s: ExecutionStep) => {
            if (s.action !== 'shell_interpreter') return false;
            const detailHas = s.detail?.includes('package_skill') || s.detail?.includes('init_skill');
            const inputHas = s.actionInput?.includes('package_skill') || s.actionInput?.includes('init_skill');
            const outputTexts = (outputs[s.id] || []).map(o => String(o.content)).join(' ');
            const outputHas =
              outputTexts.includes('package_skill') ||
              outputTexts.includes('init_skill') ||
              outputTexts.includes('Successfully packaged');
            return detailHas || inputHas || outputHas;
          };
          const skillStep = steps.find(isSkillPackageStep);
          if (skillStep) {
            const allText = [
              skillStep.actionInput || '',
              skillStep.detail || '',
              ...(outputs[skillStep.id] || []).map(o => String(o.content)),
            ].join(' ');
            const skillName = extractCreatedSkillName(allText);
            if (skillName) {
              restoredSkillNames[viewId] = skillName;
            }
          }

          newMessages.push({
            id: viewId,
            role: 'view',
            context: finalContent,
            order: msg.order,
            thinking: false,
            taskPlan: Array.isArray(payload.task_plan)
              ? payload.task_plan
              : Array.isArray(payload.tasks)
                ? payload.tasks
                : undefined,
          });
        } else {
          newMessages.push({
            id: viewId,
            role: 'view',
            context: msg.context || '',
            order: msg.order,
            thinking: false,
          });
        }
      }
    });

    setMessages(newMessages);
    setExecutionMap(newExecutionMap);
    setArtifacts(allArtifacts);
    if (Object.keys(restoredSkillNames).length > 0) {
      setCreatedSkillNames(prev => ({ ...prev, ...restoredSkillNames }));
    }

    const lastView = [...newMessages].reverse().find(m => m.role === 'view');
    if (lastView?.id) {
      setActiveMessageId(lastView.id);
      setStreamingSummary(lastView.context || '');
      setSummaryComplete(true);
    }
  };

  const loadConversation = async (convUid: string) => {
    const requestSeq = historyLoadSeqRef.current + 1;
    historyLoadSeqRef.current = requestSeq;
    setHistoryLoading(true);
    setConversationId(convUid);
    resetConversationSurface();
    try {
      const res: any = await axios.get(`/api/v1/chat/dialogue/messages/history?con_uid=${convUid}`);
      if (historyLoadSeqRef.current !== requestSeq) return;
      let msgList: any[] | null = null;
      if (res?.success && Array.isArray(res.data)) {
        msgList = res.data;
      } else if (Array.isArray(res?.data?.data)) {
        msgList = res.data.data;
      } else if (Array.isArray(res?.data)) {
        msgList = res.data;
      } else if (Array.isArray(res)) {
        msgList = res;
      }
      restoreFromHistory(
        (msgList || []).map((m: any) => ({
          role: m.role,
          context: m.context,
          order: m.order,
          model_name: m.model_name,
        })),
      );
    } catch (e) {
      if (historyLoadSeqRef.current !== requestSeq) return;
      console.error('Failed to load conversation', e);
      message.error('加载历史对话失败');
    } finally {
      if (historyLoadSeqRef.current === requestSeq) {
        setHistoryLoading(false);
      }
    }
  };

  const _QuickAction = ({ icon, text, onClick }: { icon: any; text: string; onClick?: () => void }) => (
    <div
      onClick={onClick}
      className='flex items-center gap-2 px-4 py-2 bg-white dark:bg-[#2c2d31] border border-gray-200 dark:border-gray-700 rounded-full cursor-pointer hover:bg-gray-50 dark:hover:bg-[#35363a] transition-colors text-sm text-gray-600 dark:text-gray-300 shadow-sm'
    >
      {icon}
      <span>{text}</span>
    </div>
  );

  // Upload Props
  const uploadProps: any = {
    name: 'file',
    multiple: false,
    showUploadList: false,
    beforeUpload: (file: any) => {
      setUploadedFile(file);
      parseLocalFilePreview(file as File);
      message.success(`${file.name} attached successfully`);
      return false; // Prevent auto upload, we just want to select it
    },
  };

  return (
    <ConfigProvider
      theme={{
        token: {
          colorPrimary: '#000000',
        },
      }}
    >
      <div className='dataman-chat-home flex h-full w-full bg-[#f7f7f9] dark:bg-[#0f1012] text-[#1a1b1e] dark:text-gray-200 font-sans overflow-hidden'>
        {/* Main Content */}
        <div className='industrial-canvas flex-1 flex flex-col relative overflow-hidden bg-white dark:bg-[#111217]'>
          {/* From Task Banner - shown when navigating from a scheduled task */}
          {router.query.from_task && <FromTaskBanner taskId={router.query.from_task as string} />}

          {/* Chat Messages or Hero Section */}
          {/* When from_task mode and loading history, show loading spinner instead of Hero */}
          {historyLoading && messages.length === 0 ? (
            <div className='flex-1 flex items-center justify-center'>
              <Spin size='large' tip='加载对话历史...' />
            </div>
          ) : messages.length > 0 ? (
            <div className={`flex-1 flex overflow-hidden ${rightPanelCollapsed ? 'justify-center' : ''}`}>
              <div
                className={`${rightPanelCollapsed ? 'flex-1 max-w-[800px] border-r-0' : 'flex-[2] min-w-0 border-r border-gray-200/80 dark:border-gray-800'} flex flex-col overflow-hidden bg-white dark:bg-[#111217] transition-all duration-300 relative`}
              >
                <div className='flex-1 min-h-0 overflow-y-auto'>
                  {rounds.map((round, roundIndex) => {
                    const isLastRound = roundIndex === rounds.length - 1;
                    const isSelected = round.viewMsg?.id === selectedViewMsgId;

                    const execution = round.viewMsg?.id ? executionMap[round.viewMsg.id] : undefined;
                    const {
                      sections,
                      activeStep: _activeStep,
                      outputs: _outputs,
                      stepThoughts,
                    } = convertToManusFormat(execution, round.humanMsg?.context, t, askDataSceneNameById);
                    const isWorking =
                      (isLastRound &&
                        (round.viewMsg?.thinking || execution?.steps.some(s => s.status === 'running'))) ||
                      false;

                    const roundAssistantText = isLastRound
                      ? streamingSummary || round.viewMsg?.context || undefined
                      : round.viewMsg?.context || undefined;

                    return (
                      <div key={round.viewMsg?.id || round.humanMsg?.id || `round-${roundIndex}`}>
                        <ManusLeftPanel
                          sections={sections}
                          activeStepId={isSelected ? selectedStepId || execution?.activeStepId : undefined}
                          onStepClick={(stepId, _sectionId) => {
                            if (round.viewMsg?.id) {
                              setActiveViewMsgId(round.viewMsg.id);
                              setSelectedStepId(stepId);
                              setRightPanelCollapsed(false);
                              setExecutionMap(prev => ({
                                ...prev,
                                [round.viewMsg!.id!]: {
                                  ...prev[round.viewMsg!.id!],
                                  activeStepId: stepId,
                                },
                              }));
                            }
                          }}
                          isWorking={isWorking}
                          userQuery={round.humanMsg?.context}
                          attachedFile={round.humanMsg?.attachedFile}
                          attachedKnowledge={round.humanMsg?.attachedKnowledge}
                          attachedSkill={round.humanMsg?.attachedSkill}
                          attachedDb={round.humanMsg?.attachedDb}
                          taskPlan={round.viewMsg?.taskPlan}
                          attachedConnectors={round.humanMsg?.attachedConnectors}
                          assistantText={roundAssistantText}
                          stepThoughts={stepThoughts}
                          artifacts={artifacts.filter(a => a.messageId === round.viewMsg?.id)}
                          onArtifactClick={artifact => {
                            if (round.viewMsg?.id) setActiveViewMsgId(round.viewMsg.id);
                            setRightPanelCollapsed(false);
                            if (artifact.type === 'html') {
                              setPreviewArtifact(artifact as Artifact);
                              setRightPanelView('html-preview');
                            } else if (artifact.type === 'code' && artifact.stepId) {
                              setSelectedStepId(artifact.stepId);
                              setRightPanelView('execution');
                              if (round.viewMsg?.id && execution) {
                                setExecutionMap(prev => ({
                                  ...prev,
                                  [round.viewMsg!.id!]: {
                                    ...prev[round.viewMsg!.id!],
                                    activeStepId: artifact.stepId!,
                                  },
                                }));
                              }
                            } else if (artifact.type === 'file') {
                              // Image file artifacts: preview instead of download
                              if (/\.(png|jpg|jpeg|gif|webp|svg|bmp)$/i.test(artifact.name)) {
                                setPreviewArtifact(artifact as Artifact);
                                setRightPanelView('image-preview');
                              } else {
                                downloadArtifact(artifact as Artifact);
                              }
                            } else if (artifact.type === 'image') {
                              setPreviewArtifact(artifact as Artifact);
                              setRightPanelView('image-preview');
                            }
                          }}
                          onArtifactDownload={artifact => downloadArtifact(artifact as Artifact)}
                          onViewAllFiles={() => {
                            if (round.viewMsg?.id) setActiveViewMsgId(round.viewMsg.id);
                            setRightPanelCollapsed(false);
                            setRightPanelView('files');
                          }}
                          isCollapsed={false}
                          createdSkillName={createdSkillNames[round.viewMsg?.id || '']}
                          onSkillCardClick={_skillName => {
                            if (round.viewMsg?.id) setActiveViewMsgId(round.viewMsg.id);
                            setRightPanelCollapsed(false);
                            setRightPanelView('skill-preview');
                            // Find the package_skill step and select it so right panel shows SkillCardRenderer
                            if (execution) {
                              const skillStep = execution.steps.find((s: ExecutionStep) => {
                                if (s.action !== 'shell_interpreter') return false;
                                const detailHas =
                                  s.detail?.includes('package_skill') || s.detail?.includes('init_skill');
                                const inputHas =
                                  s.actionInput?.includes('package_skill') || s.actionInput?.includes('init_skill');
                                const outputTexts = (execution.outputs[s.id] || [])
                                  .map(o => String(o.content))
                                  .join(' ');
                                const outputHas =
                                  outputTexts.includes('package_skill') ||
                                  outputTexts.includes('init_skill') ||
                                  outputTexts.includes('Successfully packaged');
                                return detailHas || inputHas || outputHas;
                              });
                              if (skillStep) {
                                setSelectedStepId(skillStep.id);
                                setExecutionMap(prev => ({
                                  ...prev,
                                  [round.viewMsg!.id!]: { ...prev[round.viewMsg!.id!], activeStepId: skillStep.id },
                                }));
                              }
                            }
                          }}
                          onSkillDownload={async skillName => {
                            try {
                              const base = process.env.API_BASE_URL || '';
                              const res = await fetch(
                                `${base}/api/v1/agent/skills/download?skill_name=${encodeURIComponent(skillName)}`,
                              );
                              if (!res.ok) throw new Error('Download failed');
                              const blob = await res.blob();
                              const url = URL.createObjectURL(blob);
                              const a = document.createElement('a');
                              a.href = url;
                              a.download = `${skillName}.zip`;
                              document.body.appendChild(a);
                              a.click();
                              document.body.removeChild(a);
                              URL.revokeObjectURL(url);
                            } catch {
                              // Download failed silently
                            }
                          }}
                        />
                      </div>
                    );
                  })}
                </div>

                {/* Input Area at Bottom for Chat Mode - Hidden in read-only task replay mode */}
                {!router.query.from_task && (
                  <div className='chat-composer-region bg-white dark:bg-[#111217] p-4 md:p-6'>
                    <div className='max-w-[720px] mx-auto'>
                      {/* Uploaded File Tags */}
                      {uploadedFile && (
                        <div className='flex flex-wrap gap-2 mb-2'>
                          <Tag
                            closable
                            onClose={() => setUploadedFile(null)}
                            className='flex items-center gap-1 bg-green-50 border-green-200 text-green-700 px-3 py-1 rounded-full'
                          >
                            <FileExcelOutlined /> <span className='font-medium ml-1'>{uploadedFile.name}</span>
                          </Tag>
                        </div>
                      )}

                      {/* Human-in-the-loop Question Dock */}
                      {pendingQuestion && (
                        <div className='mb-3 w-full'>
                          <QuestionDock
                            request={{
                              request_id: pendingQuestion.request_id,
                              conv_id: pendingQuestion.conv_id,
                              questions: pendingQuestion.questions,
                            }}
                            onReply={replyQuestion}
                            onReject={rejectQuestion}
                          />
                        </div>
                      )}

                      {/* Outer Frame - Floating Effect */}
                      <div className='rounded-2xl w-full relative transition-all duration-300 shadow-[0_12px_32px_rgba(0,0,0,0.1),0_4px_12px_rgba(0,0,0,0.06)] hover:shadow-[0_20px_48px_rgba(0,0,0,0.16),0_8px_24px_rgba(0,0,0,0.08)] dark:shadow-[0_12px_32px_rgba(0,0,0,0.4)] dark:hover:shadow-[0_20px_48px_rgba(0,0,0,0.5)]'>
                        {/* White Inner Box - Clean Glass Card */}
                        <div className='bg-white/95 backdrop-blur-md dark:bg-[#1e1f24]/95 rounded-2xl border border-gray-100 dark:border-[#33353b] shadow-[inset_0_1px_0_rgba(255,255,255,1)] dark:shadow-[inset_0_1px_0_rgba(255,255,255,0.05)] p-3 px-4'>
                          {taskPlan.length > 0 && (
                            <div className='mb-3'>
                              <TaskPlanCard tasks={taskPlan} embedded />
                            </div>
                          )}
                          <Input.TextArea
                            value={query}
                            onChange={e => {
                              const newValue = e.target.value;
                              setQuery(newValue);
                              if (newValue === '/' && !isSkillPanelOpen && !selectedSkill) {
                                setIsSkillPanelOpen(true);
                              }
                            }}
                            onPressEnter={e => {
                              if (!e.shiftKey) {
                                e.preventDefault();
                                if (!loading) handleStart();
                              }
                            }}
                            placeholder={
                              t('ask_data_question') || 'Ask a question, upload a CSV, or generate a report...'
                            }
                            autoSize={{ minRows: 2, maxRows: 6 }}
                            className='flex-1 resize-none !border-none !shadow-none !bg-transparent px-0 py-2'
                            style={{ backgroundColor: 'transparent' }}
                          />

                          {/* Toolbar Row */}
                          <div className='flex items-center justify-between mt-1'>
                            <div className='flex items-center gap-3'>
                              {/* Add Button */}
                              <Dropdown
                                menu={{
                                  items: [
                                    {
                                      key: 'upload',
                                      label: (
                                        <Upload {...uploadProps}>
                                          <div className='w-full'>Upload File</div>
                                        </Upload>
                                      ),
                                      icon: <UploadOutlined />,
                                    },
                                  ],
                                }}
                                trigger={['click']}
                              >
                                <Tooltip title={t('add_context')}>
                                  <Button
                                    type='text'
                                    shape='circle'
                                    size='small'
                                    icon={<PlusOutlined />}
                                    className='flex items-center justify-center text-gray-500 hover:text-violet-600 bg-gradient-to-b from-white to-gray-50 dark:from-[#2a2b2f] dark:to-[#1e1f24] dark:text-gray-300 border border-gray-200/80 dark:border-white/10 shadow-[0_1px_2px_rgba(0,0,0,0.05),inset_0_1px_0_rgba(255,255,255,1)] dark:shadow-[0_1px_2px_rgba(0,0,0,0.2),inset_0_1px_0_rgba(255,255,255,0.05)] hover:-translate-y-[0.5px] hover:shadow-[0_2px_4px_rgba(0,0,0,0.06),inset_0_1px_0_rgba(255,255,255,1)] dark:hover:border-white/20 transition-all flex-shrink-0'
                                  />
                                </Tooltip>
                              </Dropdown>

                              {/* Skill Selector Button with Badge */}
                              <Popover
                                trigger='click'
                                placement='topLeft'
                                open={isSkillPanelOpen}
                                onOpenChange={setIsSkillPanelOpen}
                                overlayClassName='manus-skill-menu'
                                overlayInnerStyle={{ padding: 0, borderRadius: 12 }}
                                content={
                                  <div className='w-[320px] bg-white dark:bg-[#2c2d31] rounded-xl shadow-xl overflow-hidden'>
                                    <div className='p-3 border-b border-gray-100 dark:border-gray-700'>
                                      <Input
                                        placeholder={t('search_skill')}
                                        prefix={<SearchOutlined className='text-gray-400' />}
                                        value={skillSearchQuery}
                                        onChange={e => setSkillSearchQuery(e.target.value)}
                                        className='rounded-lg'
                                        allowClear
                                        size='small'
                                      />
                                    </div>
                                    <div className='max-h-[300px] overflow-y-auto'>
                                      {(skillsList || [])
                                        .filter(
                                          skill =>
                                            !skillSearchQuery ||
                                            skill.name.toLowerCase().includes(skillSearchQuery.toLowerCase()) ||
                                            skill.description.toLowerCase().includes(skillSearchQuery.toLowerCase()),
                                        )
                                        .map(skill => (
                                          <div
                                            key={skill.id}
                                            onClick={() => {
                                              if (selectedSkill?.id === skill.id) {
                                                setSelectedSkill(null);
                                                setQuery('');
                                              } else {
                                                setSelectedSkill(skill);
                                                setQuery(`/${skill.name} `);
                                              }
                                              setIsSkillPanelOpen(false);
                                              setSkillSearchQuery('');
                                            }}
                                            className={`flex items-start gap-3 px-3 py-2.5 cursor-pointer transition-all hover:bg-gray-50 dark:hover:bg-gray-800 ${
                                              selectedSkill?.id === skill.id ? 'bg-purple-50 dark:bg-purple-900/20' : ''
                                            }`}
                                          >
                                            <div className='flex-shrink-0 w-7 h-7 rounded-lg bg-gradient-to-br from-purple-500 to-blue-500 flex items-center justify-center text-white text-xs'>
                                              {skill.icon || <ThunderboltOutlined />}
                                            </div>
                                            <div className='flex-1 min-w-0'>
                                              <div className='flex items-center gap-2'>
                                                <span className='font-medium text-sm text-gray-800 dark:text-gray-200'>
                                                  {skill.name}
                                                </span>
                                                <span
                                                  className={`text-[10px] px-1.5 py-0.5 rounded ${
                                                    skill.type === 'official'
                                                      ? 'bg-blue-100 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400'
                                                      : 'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400'
                                                  }`}
                                                >
                                                  {skill.type === 'official'
                                                    ? t('picker_skill_official')
                                                    : t('picker_skill_personal')}
                                                </span>
                                              </div>
                                              <p className='text-xs text-gray-500 dark:text-gray-400 mt-0.5 line-clamp-2'>
                                                {skill.description}
                                              </p>
                                            </div>
                                            {selectedSkill?.id === skill.id && (
                                              <CheckCircleFilled className='text-purple-500 flex-shrink-0 text-sm' />
                                            )}
                                          </div>
                                        ))}
                                      {(skillsList || []).filter(
                                        skill =>
                                          !skillSearchQuery ||
                                          skill.name.toLowerCase().includes(skillSearchQuery.toLowerCase()) ||
                                          skill.description.toLowerCase().includes(skillSearchQuery.toLowerCase()),
                                      ).length === 0 && (
                                        <div className='text-center py-8 text-gray-400'>
                                          <ThunderboltOutlined className='text-2xl mb-2 opacity-50' />
                                          <div className='text-xs'>
                                            {skillSearchQuery ? t('picker_skill_no_match') : t('picker_skill_empty')}
                                          </div>
                                        </div>
                                      )}
                                    </div>
                                    <div className='border-t border-gray-100 dark:border-gray-700 px-3 py-2 flex items-center justify-end bg-gray-50/50 dark:bg-gray-900/50'>
                                      <span className='text-[10px] text-gray-400'>
                                        {t('picker_skill_count', { count: (skillsList || []).length })}
                                      </span>
                                    </div>
                                  </div>
                                }
                              >
                                <Tooltip
                                  title={
                                    selectedSkill
                                      ? t('skill_selected', { name: selectedSkill.name })
                                      : t('select_skill')
                                  }
                                >
                                  <Button
                                    type='text'
                                    shape='circle'
                                    size='small'
                                    className={`relative flex items-center justify-center flex-shrink-0 transition-all ${
                                      selectedSkill
                                        ? 'bg-gradient-to-br from-[#a78bfa] to-[#7c3aed] text-white border border-transparent shadow-[0_2px_4px_rgba(139,92,246,0.3),inset_0_1px_0_rgba(255,255,255,0.3)] hover:-translate-y-[0.5px] hover:shadow-[0_4px_8px_rgba(139,92,246,0.4),inset_0_1px_0_rgba(255,255,255,0.3)]'
                                        : 'text-gray-500 hover:text-violet-600 bg-gradient-to-b from-white to-gray-50 dark:from-[#2a2b2f] dark:to-[#1e1f24] dark:text-gray-300 border border-gray-200/80 dark:border-white/10 shadow-[0_1px_2px_rgba(0,0,0,0.05),inset_0_1px_0_rgba(255,255,255,1)] dark:shadow-[0_1px_2px_rgba(0,0,0,0.2),inset_0_1px_0_rgba(255,255,255,0.05)] hover:-translate-y-[0.5px] hover:shadow-[0_2px_4px_rgba(0,0,0,0.06),inset_0_1px_0_rgba(255,255,255,1)] dark:hover:border-white/20'
                                    }`}
                                  >
                                    <div className='relative'>
                                      <ThunderboltOutlined className={selectedSkill ? 'text-white' : ''} />
                                      {selectedSkill && (
                                        <span className='absolute -top-1.5 -right-1.5 bg-white text-[#7c3aed] text-[8px] rounded-full w-3.5 h-3.5 flex items-center justify-center font-bold shadow-sm ring-1 ring-[#7c3aed]/30'>
                                          1
                                        </span>
                                      )}
                                    </div>
                                  </Button>
                                </Tooltip>
                              </Popover>

                              {/* Separator dot */}
                              <div className='w-px h-4 bg-gray-200 dark:bg-gray-700 mx-0.5' />

                              {/* Model Selector with premium styling */}
                              <div className='model-selector-premium'>
                                <ModelSelector onChange={val => setModel(val)} />
                              </div>
                              <style
                                dangerouslySetInnerHTML={{
                                  __html: `
                                  .model-selector-premium .ant-select { border-radius: 8px !important; border: none !important; }
                                  .model-selector-premium .ant-select-selector { background: linear-gradient(180deg, #ffffff 0%, #f9fafb 100%) !important; border: 1px solid rgba(0,0,0,0.12) !important; box-shadow: 0 1px 2px rgba(0,0,0,0.05), inset 0 1px 0 rgba(255,255,255,1) !important; border-radius: 8px !important; transition: all 0.2s ease !important; padding: 0 8px !important; }
                                  .dark .model-selector-premium .ant-select-selector { background: linear-gradient(180deg, #2a2b2f 0%, #1e1f24 100%) !important; border: 1px solid rgba(255,255,255,0.1) !important; box-shadow: 0 1px 2px rgba(0,0,0,0.2), inset 0 1px 0 rgba(255,255,255,0.05) !important; }
                                  .model-selector-premium .ant-select:hover .ant-select-selector { border-color: rgba(0,0,0,0.2) !important; box-shadow: 0 2px 4px rgba(0,0,0,0.06), inset 0 1px 0 rgba(255,255,255,1) !important; transform: translateY(-0.5px); }
                                  .dark .model-selector-premium .ant-select:hover .ant-select-selector { border-color: rgba(255,255,255,0.15) !important; }
                                  .model-selector-premium .ant-select-focused .ant-select-selector { border-color: #a78bfa !important; box-shadow: 0 0 0 2px rgba(167,139,250,0.15), inset 0 1px 0 rgba(255,255,255,1) !important; }
                                  .dark .model-selector-premium .ant-select-focused .ant-select-selector { box-shadow: 0 0 0 2px rgba(167,139,250,0.2), inset 0 1px 0 rgba(255,255,255,0.05) !important; }
                                  
                                  /* Global Dropdown Item Styles for Model Selectors */
                                  .ant-select-dropdown .ant-select-item-option-selected { background-color: #f1f5f9 !important; color: #0f172a !important; font-weight: 500 !important; }
                                  .ant-select-dropdown .ant-select-item-option-active:not(.ant-select-item-option-selected) { background-color: #f8fafc !important; }
                                  .dark .ant-select-dropdown .ant-select-item-option-selected { background-color: rgba(255,255,255,0.08) !important; color: #e2e8f0 !important; }
                                  .dark .ant-select-dropdown .ant-select-item-option-active:not(.ant-select-item-option-selected) { background-color: rgba(255,255,255,0.04) !important; }
                                `,
                                }}
                              />
                            </div>

                            <div className='flex items-center gap-2.5'>
                              <ContextUsageBar
                                used={contextStatus.used_tokens}
                                budget={contextStatus.max_tokens}
                                ratio={contextStatus.usage_percent / 100}
                                state={contextStatus.state}
                                compactLayer={contextStatus.layer}
                                variant='compact'
                                className='mr-0.5'
                              />

                              {/* Voice Button */}
                              <Tooltip title={t('voice_input')}>
                                <Button
                                  type='text'
                                  shape='circle'
                                  icon={<AudioOutlined className='text-gray-500 text-[18px]' />}
                                  onClick={() => message.info(t('voice_input_coming_soon'))}
                                  className='flex-shrink-0 h-9 w-9 transition-all duration-200 flex items-center justify-center hover:bg-gray-100 dark:hover:bg-gray-800'
                                />
                              </Tooltip>

                              {/* Send Button with blue gradient + gloss animation */}
                              <Button
                                type='primary'
                                shape='circle'
                                icon={loading ? <StopOutlined /> : <ArrowUpOutlined />}
                                onClick={() => (loading ? handleCancelRun() : handleStart())}
                                disabled={!loading && !query.trim() && !uploadedFile}
                                className={`group/send relative overflow-hidden border-none shadow-lg flex-shrink-0 h-9 w-9 transition-all duration-200 ${
                                  loading
                                    ? 'bg-gradient-to-br from-[#ef4444] to-[#dc2626] hover:shadow-red-300/40 hover:shadow-xl hover:scale-105'
                                    : query.trim() || uploadedFile
                                      ? 'bg-gradient-to-br from-[#3b82f6] to-[#2563eb] hover:shadow-blue-300/40 hover:shadow-xl hover:scale-105'
                                      : 'bg-gray-200 text-gray-400'
                                }`}
                                style={
                                  loading
                                    ? { background: 'linear-gradient(135deg, #ef4444, #dc2626)' }
                                    : query.trim() || uploadedFile
                                      ? { background: 'linear-gradient(135deg, #3b82f6, #2563eb)' }
                                      : undefined
                                }
                              >
                                {(loading || query.trim() || uploadedFile) && (
                                  <span
                                    className='absolute inset-0 opacity-0 group-hover/send:opacity-100 transition-opacity duration-300 pointer-events-none'
                                    style={{
                                      background:
                                        'linear-gradient(105deg, transparent 40%, rgba(255,255,255,0.25) 45%, rgba(255,255,255,0.35) 50%, rgba(255,255,255,0.25) 55%, transparent 60%)',
                                      animation: 'glossSweepChat 1.8s ease-in-out infinite',
                                    }}
                                  />
                                )}
                              </Button>
                            </div>
                            <style
                              dangerouslySetInnerHTML={{
                                __html: `@keyframes glossSweepChat { 0% { transform: translateX(-100%); } 100% { transform: translateX(100%); } }`,
                              }}
                            />
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                )}
              </div>
              {/* Panel toggle handle — placed between panels to avoid overflow clipping */}
              <div className='relative z-20 flex-shrink-0'>
                <Tooltip title={rightPanelCollapsed ? t('expand_panel') : t('collapse_panel')} placement='left'>
                  <button
                    onClick={() => setRightPanelCollapsed(prev => !prev)}
                    className='absolute top-1/2 -translate-y-1/2 -translate-x-1/2 w-4 h-8 flex items-center justify-center bg-white dark:bg-[#1a1b1e] border border-gray-200 dark:border-gray-700 rounded-full shadow-sm hover:bg-gray-100 dark:hover:bg-gray-800 hover:w-5 hover:shadow-md transition-all duration-200 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300'
                  >
                    {rightPanelCollapsed ? (
                      <LeftOutlined style={{ fontSize: 10 }} />
                    ) : (
                      <RightOutlined style={{ fontSize: 10 }} />
                    )}
                  </button>
                </Tooltip>
              </div>
              <div
                className={`${rightPanelCollapsed ? 'w-0 min-w-0 overflow-hidden opacity-0' : 'flex-[3] min-w-0 overflow-hidden'} bg-[#f8f8fb] dark:bg-[#0f1114] flex flex-col transition-all duration-300`}
              >
                {(() => {
                  const activeViewMsg = messages.find(m => m.id === selectedViewMsgId && m.role === 'view');
                  const rawExecution = activeViewMsg?.id ? executionMap[activeViewMsg.id] : undefined;
                  // Respect user's manual step selection for the right panel
                  const execution =
                    rawExecution && selectedStepId ? { ...rawExecution, activeStepId: selectedStepId } : rawExecution;
                  const {
                    activeStep,
                    outputs,
                    stepThoughts: _stepThoughts,
                  } = convertToManusFormat(execution, undefined, t, askDataSceneNameById);
                  const isRunning = execution?.steps.some(s => s.status === 'running') || false;

                  return (
                    <ManusRightPanel
                      activeStep={activeStep}
                      outputs={outputs}
                      isRunning={isRunning}
                      artifacts={artifacts.filter(a => a.messageId === activeViewMsg?.id)}
                      onArtifactClick={artifact => {
                        if (artifact.type === 'html') {
                          setPreviewArtifact(artifact as Artifact);
                          setRightPanelView('html-preview');
                        } else if (artifact.type === 'code' && artifact.stepId) {
                          setSelectedStepId(artifact.stepId);
                          setRightPanelView('execution');
                          if (activeViewMsg?.id && execution) {
                            setExecutionMap(prev => ({
                              ...prev,
                              [activeViewMsg.id!]: {
                                ...prev[activeViewMsg.id!],
                                activeStepId: artifact.stepId!,
                              },
                            }));
                          }
                        } else if (artifact.type === 'file') {
                          if (/\.(png|jpg|jpeg|gif|webp|svg|bmp)$/i.test(artifact.name)) {
                            setPreviewArtifact(artifact as Artifact);
                            setRightPanelView('image-preview');
                            setRightPanelCollapsed(false);
                          }
                        } else if (artifact.type === 'image') {
                          setPreviewArtifact(artifact as Artifact);
                          setRightPanelView('image-preview');
                          setRightPanelCollapsed(false);
                        }
                      }}
                      panelView={rightPanelView}
                      onPanelViewChange={setRightPanelView}
                      previewArtifact={previewArtifact}
                      skillName={createdSkillNames[activeViewMsg?.id || ''] || null}
                      summaryContent={streamingSummary || activeViewMsg?.context || ''}
                      isSummaryStreaming={!_summaryComplete && !!streamingSummary}
                    />
                  );
                })()}
              </div>
            </div>
          ) : (
            // Welcome Mode: Display Hero Section
            <div className='industrial-welcome relative flex-1 flex flex-col items-center justify-center px-6 py-4 overflow-y-auto'>
              <div className='industrial-welcome-content w-full max-w-[860px] flex flex-col items-center animate-fade-in-up'>
                <div className='industrial-hero-header w-full flex flex-col items-center'>
                  <h1
                    className={`industrial-hero-title text-4xl md:text-5xl text-gray-900 dark:text-gray-100 text-center flex items-center gap-4 ${
                      conversationId ? 'mb-3' : 'mb-8'
                    }`}
                  >
                    <div className='w-14 h-14 rounded-xl bg-white dark:bg-[#1a1b1e] shadow-md flex items-center justify-center flex-shrink-0'>
                      <Image src='/datrix-brand.png' alt='Datrix' width={40} height={40} className='object-contain' />
                    </div>
                    <span className='industrial-hero-name'>
                      {conversationId ? '当前对话暂无历史消息' : t('home_title')}
                    </span>
                  </h1>
                  {conversationId && (
                    <p className='mb-6 text-center text-sm text-gray-400 dark:text-gray-500'>
                      该会话已打开，可在下方继续输入。
                    </p>
                  )}
                </div>

                {/* Input Box Container - Premium Layered Style */}
                <div className='industrial-command-region w-full relative'>
                  {/* Outer Frame - Floating Effect */}
                  <div className='industrial-command-frame w-full relative transition-all duration-500 rounded-[28px] shadow-[0_16px_48px_rgba(0,0,0,0.12),0_6px_20px_rgba(0,0,0,0.08)] hover:shadow-[0_24px_64px_rgba(0,0,0,0.2),0_12px_32px_rgba(0,0,0,0.1)] dark:shadow-[0_16px_48px_rgba(0,0,0,0.4)] dark:hover:shadow-[0_24px_64px_rgba(0,0,0,0.5)]'>
                    {/* White Inner Box - Clean Glass Card */}
                    <div className='industrial-command-shell bg-white/95 backdrop-blur-md dark:bg-[#1e1f24]/95 rounded-[28px] border border-gray-100 dark:border-[#33353b] shadow-[inset_0_1px_0_rgba(255,255,255,1)] dark:shadow-[inset_0_1px_0_rgba(255,255,255,0.05)] p-5 relative z-10'>
                      {/* Uploaded File Tags */}
                      {uploadedFile && (
                        <div className='flex flex-wrap gap-2 mb-2'>
                          <Tag
                            closable
                            onClose={() => setUploadedFile(null)}
                            className='flex items-center gap-1 bg-green-50 border-green-200 text-green-700 px-3 py-1 rounded-full'
                          >
                            <FileExcelOutlined /> <span className='font-medium ml-1'>{uploadedFile.name}</span>
                          </Tag>
                        </div>
                      )}

                      <Input.TextArea
                        value={query}
                        onChange={e => {
                          const newValue = e.target.value;
                          setQuery(newValue);
                          if (newValue === '/' && !isSkillPanelOpen && !selectedSkill) {
                            setIsSkillPanelOpen(true);
                          }
                        }}
                        onPressEnter={e => {
                          if (!e.shiftKey) {
                            e.preventDefault();
                            if (!loading) handleStart();
                          }
                        }}
                        placeholder={t('ask_data_question') || 'Ask a question, upload a CSV, or generate a report...'}
                        autoSize={{ minRows: 3, maxRows: 8 }}
                        className='industrial-command-input text-lg resize-none !border-none !shadow-none !bg-transparent px-2 py-2 mb-2'
                        style={{ backgroundColor: 'transparent' }}
                      />

                      {/* Input Toolbar */}
                      <div className='industrial-command-toolbar flex items-center justify-between px-1 mt-1'>
                        <div className='flex items-center gap-4'>
                          {/* Add Button with Dropdown Menu */}
                          <Dropdown
                            menu={{
                              items: [
                                {
                                  key: 'upload',
                                  label: (
                                    <Upload {...uploadProps}>
                                      <div className='w-full'>{t('add_from_local')}</div>
                                    </Upload>
                                  ),
                                  icon: <PaperClipOutlined />,
                                },
                                {
                                  key: 'skill',
                                  label: t('use_skill'),
                                  icon: <ThunderboltOutlined />,
                                  onClick: () => setIsSkillPanelOpen(true),
                                },
                              ],
                            }}
                            trigger={['click']}
                          >
                            <Tooltip title={t('add_context')}>
                              <Button
                                type='text'
                                shape='circle'
                                size='small'
                                icon={<PlusOutlined />}
                                className='flex items-center justify-center text-gray-500 hover:text-violet-600 bg-gradient-to-b from-white to-gray-50 dark:from-[#2a2b2f] dark:to-[#1e1f24] dark:text-gray-300 border border-gray-200/80 dark:border-white/10 shadow-[0_1px_2px_rgba(0,0,0,0.05),inset_0_1px_0_rgba(255,255,255,1)] dark:shadow-[0_1px_2px_rgba(0,0,0,0.2),inset_0_1px_0_rgba(255,255,255,0.05)] hover:-translate-y-[0.5px] hover:shadow-[0_2px_4px_rgba(0,0,0,0.06),inset_0_1px_0_rgba(255,255,255,1)] dark:hover:border-white/20 transition-all flex-shrink-0'
                              />
                            </Tooltip>
                          </Dropdown>

                          {/* Skill Selector Button with Badge - Purple Gradient */}
                          <Popover
                            trigger='click'
                            placement='topLeft'
                            open={isSkillPanelOpen}
                            onOpenChange={setIsSkillPanelOpen}
                            overlayClassName='manus-skill-menu'
                            overlayInnerStyle={{ padding: 0, borderRadius: 12 }}
                            content={
                              <div className='w-[320px] bg-white dark:bg-[#2c2d31] rounded-xl shadow-xl overflow-hidden'>
                                <div className='p-3 border-b border-gray-100 dark:border-gray-700'>
                                  <Input
                                    placeholder={t('search_skill')}
                                    prefix={<SearchOutlined className='text-gray-400' />}
                                    value={skillSearchQuery}
                                    onChange={e => setSkillSearchQuery(e.target.value)}
                                    className='rounded-lg'
                                    allowClear
                                    size='small'
                                  />
                                </div>
                                <div className='max-h-[300px] overflow-y-auto'>
                                  {(skillsList || [])
                                    .filter(
                                      skill =>
                                        !skillSearchQuery ||
                                        skill.name.toLowerCase().includes(skillSearchQuery.toLowerCase()) ||
                                        skill.description.toLowerCase().includes(skillSearchQuery.toLowerCase()),
                                    )
                                    .map(skill => (
                                      <div
                                        key={skill.id}
                                        onClick={() => {
                                          if (selectedSkill?.id === skill.id) {
                                            setSelectedSkill(null);
                                            setQuery('');
                                          } else {
                                            setSelectedSkill(skill);
                                            setQuery(`/${skill.name} `);
                                          }
                                          setIsSkillPanelOpen(false);
                                          setSkillSearchQuery('');
                                        }}
                                        className={`flex items-start gap-3 px-3 py-2.5 cursor-pointer transition-all hover:bg-gray-50 dark:hover:bg-gray-800 ${
                                          selectedSkill?.id === skill.id ? 'bg-purple-50 dark:bg-purple-900/20' : ''
                                        }`}
                                      >
                                        <div className='flex-shrink-0 w-7 h-7 rounded-lg bg-gradient-to-br from-purple-500 to-blue-500 flex items-center justify-center text-white text-xs'>
                                          {skill.icon || <ThunderboltOutlined />}
                                        </div>
                                        <div className='flex-1 min-w-0'>
                                          <div className='flex items-center gap-2'>
                                            <span className='font-medium text-sm text-gray-800 dark:text-gray-200'>
                                              {skill.name}
                                            </span>
                                            <span
                                              className={`text-[10px] px-1.5 py-0.5 rounded ${
                                                skill.type === 'official'
                                                  ? 'bg-blue-100 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400'
                                                  : 'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400'
                                              }`}
                                            >
                                              {skill.type === 'official'
                                                ? t('picker_skill_official')
                                                : t('picker_skill_personal')}
                                            </span>
                                          </div>
                                          <p className='text-xs text-gray-500 dark:text-gray-400 mt-0.5 line-clamp-2'>
                                            {skill.description}
                                          </p>
                                        </div>
                                        {selectedSkill?.id === skill.id && (
                                          <CheckCircleFilled className='text-purple-500 flex-shrink-0 text-sm' />
                                        )}
                                      </div>
                                    ))}
                                  {(skillsList || []).filter(
                                    skill =>
                                      !skillSearchQuery ||
                                      skill.name.toLowerCase().includes(skillSearchQuery.toLowerCase()) ||
                                      skill.description.toLowerCase().includes(skillSearchQuery.toLowerCase()),
                                  ).length === 0 && (
                                    <div className='text-center py-8 text-gray-400'>
                                      <ThunderboltOutlined className='text-2xl mb-2 opacity-50' />
                                      <div className='text-xs'>
                                        {skillSearchQuery ? t('picker_skill_no_match') : t('picker_skill_empty')}
                                      </div>
                                    </div>
                                  )}
                                </div>
                                <div className='border-t border-gray-100 dark:border-gray-700 px-3 py-2 flex items-center justify-end bg-gray-50/50 dark:bg-gray-900/50'>
                                  <span className='text-[10px] text-gray-400'>
                                    {t('picker_skill_count', { count: (skillsList || []).length })}
                                  </span>
                                </div>
                              </div>
                            }
                          >
                            <Tooltip
                              title={
                                selectedSkill ? t('skill_selected', { name: selectedSkill.name }) : t('select_skill')
                              }
                            >
                              <Button
                                type='text'
                                shape='circle'
                                size='small'
                                className={`relative flex items-center justify-center flex-shrink-0 transition-all ${
                                  selectedSkill
                                    ? 'bg-gradient-to-br from-[#a78bfa] to-[#7c3aed] text-white border border-transparent shadow-[0_2px_4px_rgba(139,92,246,0.3),inset_0_1px_0_rgba(255,255,255,0.3)] hover:-translate-y-[0.5px] hover:shadow-[0_4px_8px_rgba(139,92,246,0.4),inset_0_1px_0_rgba(255,255,255,0.3)]'
                                    : 'text-gray-500 hover:text-violet-600 bg-gradient-to-b from-white to-gray-50 dark:from-[#2a2b2f] dark:to-[#1e1f24] dark:text-gray-300 border border-gray-200/80 dark:border-white/10 shadow-[0_1px_2px_rgba(0,0,0,0.05),inset_0_1px_0_rgba(255,255,255,1)] dark:shadow-[0_1px_2px_rgba(0,0,0,0.2),inset_0_1px_0_rgba(255,255,255,0.05)] hover:-translate-y-[0.5px] hover:shadow-[0_2px_4px_rgba(0,0,0,0.06),inset_0_1px_0_rgba(255,255,255,1)] dark:hover:border-white/20'
                                }`}
                              >
                                <div className='relative'>
                                  <ThunderboltOutlined className={selectedSkill ? 'text-white' : ''} />
                                  {selectedSkill && (
                                    <span className='absolute -top-1.5 -right-1.5 bg-white text-[#7c3aed] text-[8px] rounded-full w-3.5 h-3.5 flex items-center justify-center font-bold shadow-sm ring-1 ring-[#7c3aed]/30'>
                                      1
                                    </span>
                                  )}
                                </div>
                              </Button>
                            </Tooltip>
                          </Popover>
                        </div>

                        <div className='flex items-center gap-3'>
                          <ContextUsageBar
                            used={contextStatus.used_tokens}
                            budget={contextStatus.max_tokens}
                            ratio={contextStatus.usage_percent / 100}
                            state={contextStatus.state}
                            compactLayer={contextStatus.layer}
                            variant='compact'
                            className='mr-0.5'
                          />

                          {/* Send Button with blue gradient + gloss */}
                          <Button
                            type='primary'
                            shape='circle'
                            size='large'
                            icon={loading ? <StopOutlined /> : <ArrowUpOutlined />}
                            onClick={() => (loading ? handleCancelRun() : handleStart())}
                            disabled={!loading && !query.trim() && !uploadedFile}
                            className={`group/send relative overflow-hidden border-none shadow-lg transition-all duration-200 ${
                              loading
                                ? 'bg-gradient-to-br from-[#ef4444] to-[#dc2626] hover:shadow-red-300/40 hover:shadow-xl hover:scale-105'
                                : query.trim() || uploadedFile
                                  ? 'bg-gradient-to-br from-[#3b82f6] to-[#2563eb] hover:shadow-blue-300/40 hover:shadow-xl hover:scale-105'
                                  : 'bg-gray-200 text-gray-400'
                            }`}
                            style={
                              loading
                                ? { background: 'linear-gradient(135deg, #ef4444, #dc2626)' }
                                : query.trim() || uploadedFile
                                  ? { background: 'linear-gradient(135deg, #3b82f6, #2563eb)' }
                                  : undefined
                            }
                          >
                            {(loading || query.trim() || uploadedFile) && (
                              <span
                                className='absolute inset-0 opacity-0 group-hover/send:opacity-100 transition-opacity duration-300 pointer-events-none'
                                style={{
                                  background:
                                    'linear-gradient(105deg, transparent 40%, rgba(255,255,255,0.25) 45%, rgba(255,255,255,0.35) 50%, rgba(255,255,255,0.25) 55%, transparent 60%)',
                                  animation: 'glossSweepHero 1.8s ease-in-out infinite',
                                }}
                              />
                            )}
                          </Button>
                        </div>
                        <style
                          dangerouslySetInnerHTML={{
                            __html: `@keyframes glossSweepHero { 0% { transform: translateX(-100%); } 100% { transform: translateX(100%); } }`,
                          }}
                        />
                      </div>
                    </div>
                  </div>
                </div>
              </div>
              <p className='industrial-hero-slogan absolute bottom-6 left-6 right-6 text-center text-sm text-gray-400 dark:text-gray-500 md:text-base'>
                {t('home_subtitle')}
              </p>
            </div>
          )}
        </div>
        <ConfirmDialog confirmation={pendingConfirmation} onApprove={approve} onDeny={deny} onDismiss={dismiss} />
      </div>
    </ConfigProvider>
  );
};

export default Playground;
