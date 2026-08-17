import { apiInterceptors, createModel, getSupportModels, updateModel } from '@/client/api';
import { renderModelIcon } from '@/components/chat/header/model-selector';
import { ConfigurableParams } from '@/types/common';
import { IModelData, StartModelParams, SupportModel } from '@/types/model';
import { AutoComplete, Button, Form, Select, Tooltip, message } from 'antd';
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import ReactMarkdown from 'react-markdown';
import ConfigurableForm from '../common/configurable-form';

const { Option } = Select;
const FormItem = Form.Item;

// The supported worker types
const WORKER_TYPES = ['llm', 'text2vec', 'reranker'];
const MODEL_IDENTITY_PARAM_NAMES = new Set(['name', 'provider', 'worker_type']);

type ModelFormProps = {
  onCancel: () => void;
  onSuccess: () => void;
  mode?: 'create' | 'edit';
  initialModel?: IModelData;
};

const normalizeParams = (modelParams?: SupportModel['params']): ConfigurableParams[] => {
  if (!modelParams) return [];
  return Array.isArray(modelParams) ? [...modelParams] : [modelParams];
};

const inferParamType = (value: unknown) => {
  if (typeof value === 'boolean') return 'bool';
  if (typeof value === 'number') return Number.isInteger(value) ? 'int' : 'float';
  return 'string';
};

const createStoredParam = (key: string, value: unknown): ConfigurableParams => ({
  param_class: 'stored.model.param',
  param_name: key,
  param_type: inferParamType(value),
  default_value: typeof value === 'boolean' || typeof value === 'number' || typeof value === 'string' ? value : '',
  description: '',
  required: false,
  valid_values: null,
  ext_metadata: { tags: '', order: 0 },
  is_array: false,
  label: key,
  nested_fields: null,
});

const mergeParamsWithStoredValues = (
  modelParams?: SupportModel['params'],
  storedParams?: IModelData['params'],
): ConfigurableParams[] => {
  const mergedParams = normalizeParams(modelParams);
  if (!storedParams) return mergedParams;

  const knownParamNames = new Set(mergedParams.map(param => param.param_name));
  Object.entries(storedParams).forEach(([key, value]) => {
    const isPrimitive = value == null || ['string', 'number', 'boolean'].includes(typeof value);
    if (!MODEL_IDENTITY_PARAM_NAMES.has(key) && !knownParamNames.has(key) && isPrimitive) {
      mergedParams.push(createStoredParam(key, value));
      knownParamNames.add(key);
    }
  });
  return mergedParams;
};

function ModelForm({ onCancel, onSuccess, mode = 'create', initialModel }: ModelFormProps) {
  const { t } = useTranslation();
  const isEdit = mode === 'edit' && !!initialModel;
  const [_, setModels] = useState<Array<SupportModel> | null>([]);
  const [selectedWorkerType, setSelectedWorkerType] = useState<string>();
  const [selectedProvider, setSelectedProvider] = useState<string>();
  const [params, setParams] = useState<Array<ConfigurableParams> | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [form] = Form.useForm();

  const [groupedModels, setGroupedModels] = useState<{ [key: string]: SupportModel[] }>({});
  const [providers, setProviders] = useState<string[]>([]);

  const getProvidersByWorkerType = useCallback(
    (workerType: string, groups = groupedModels) => {
      const availableProviders = new Set<string>();
      Object.entries(groups).forEach(([provider, models]) => {
        if (models.some(model => model.worker_type === workerType)) {
          availableProviders.add(provider);
        }
      });
      return Array.from(availableProviders).sort();
    },
    [groupedModels],
  );

  const findSupportModel = useCallback(
    (provider: string, workerType: string, modelName?: string) => {
      const providerModels = groupedModels[provider] || [];
      return (
        providerModels.find(model => model.worker_type === workerType && model.model === modelName) ||
        providerModels.find(model => model.worker_type === workerType)
      );
    },
    [groupedModels],
  );

  async function getModels() {
    const [, res] = await apiInterceptors(getSupportModels());
    if (res && res.length) {
      const sortedModels = res.sort((a: SupportModel, b: SupportModel) => {
        if (a.enabled && !b.enabled) return -1;
        if (!a.enabled && b.enabled) return 1;
        return a.model.localeCompare(b.model);
      });

      setModels(sortedModels);

      const grouped = sortedModels.reduce((acc: { [key: string]: SupportModel[] }, model) => {
        const provider = model.provider;
        if (!acc[provider]) acc[provider] = [];
        acc[provider].push(model);
        return acc;
      }, {});

      setGroupedModels(grouped);
      // Note: Initially do not set providers, wait for worker_type selection before setting
      setProviders([]);
    }
  }

  useEffect(() => {
    getModels();
  }, []);

  useEffect(() => {
    if (!isEdit || !initialModel || !Object.keys(groupedModels).length) return;

    const workerType = initialModel.worker_type;
    const availableProviders = getProvidersByWorkerType(workerType, groupedModels);
    const storedProvider = typeof initialModel.params?.provider === 'string' ? initialModel.params.provider : undefined;
    const inferredProvider =
      storedProvider ||
      initialModel.provider ||
      Object.values(groupedModels)
        .flat()
        .find(model => model.model === initialModel.model_name && model.worker_type === workerType)?.provider ||
      availableProviders[0];
    const displayProviders =
      inferredProvider && !availableProviders.includes(inferredProvider)
        ? [...availableProviders, inferredProvider].sort()
        : availableProviders;

    setSelectedWorkerType(workerType);
    setProviders(displayProviders);
    setSelectedProvider(inferredProvider);

    if (inferredProvider) {
      const supportModel = findSupportModel(inferredProvider, workerType, initialModel.model_name);
      setParams(mergeParamsWithStoredValues(supportModel?.params, initialModel.params));
    } else {
      setParams(mergeParamsWithStoredValues(undefined, initialModel.params));
    }

    form.setFieldsValue({
      ...(initialModel.params ?? {}),
      worker_type: workerType,
      provider: inferredProvider,
      name: initialModel.model_name,
    });
  }, [isEdit, initialModel, groupedModels, form, getProvidersByWorkerType, findSupportModel]);

  // Filter and set available providers based on worker_type
  function updateProvidersByWorkerType(workerType: string) {
    setProviders(getProvidersByWorkerType(workerType));
  }

  function handleWorkerTypeChange(value: string) {
    setSelectedWorkerType(value);
    setSelectedProvider(undefined);
    form.resetFields();
    form.setFieldValue('worker_type', value);
    updateProvidersByWorkerType(value);
  }

  function handleProviderChange(value: string) {
    setSelectedProvider(value);
    form.setFieldValue('provider', value);

    // Get the params of the first model that matches the selected worker_type under the current provider as the default params
    const firstModel = selectedWorkerType ? findSupportModel(value, selectedWorkerType) : undefined;
    setParams(normalizeParams(firstModel?.params));
  }

  async function onFinish(values: any) {
    if (!selectedProvider || !selectedWorkerType) return;

    const processFormValues = (formValues: any) => {
      const processed = { ...formValues };

      params?.forEach(param => {
        if (param.nested_fields && processed[param.param_name]) {
          const nestedValue = processed[param.param_name];
          // Make sure to keep all field values
          if (nestedValue.type) {
            const typeFields = param.nested_fields[nestedValue.type] || [];
            const fieldValues = {};

            // Collect values of all fields
            typeFields.forEach(field => {
              if (nestedValue[field.param_name] !== undefined) {
                fieldValues[field.param_name] = nestedValue[field.param_name];
              }
            });

            processed[param.param_name] = {
              ...fieldValues,
              type: nestedValue.type,
            };
          }
        }
      });

      return processed;
    };

    setLoading(true);
    try {
      const processedValues = processFormValues(values);
      const modelName = isEdit && initialModel ? initialModel.model_name : processedValues.name;
      const workerType = isEdit && initialModel ? initialModel.worker_type : selectedWorkerType;
      if (!workerType) return;
      const selectedModel = groupedModels[selectedProvider]?.find(m => m.model === modelName);
      const payloadParams = {
        ...(isEdit ? (initialModel?.params ?? {}) : {}),
        ...processedValues,
        name: modelName,
        provider: selectedProvider,
        worker_type: workerType,
      };

      const request: StartModelParams = {
        host: initialModel?.host || selectedModel?.host || '',
        port: initialModel?.port || selectedModel?.port || 0,
        model: modelName,
        worker_type: workerType,
        params: payloadParams,
      };

      const [, , data] = await apiInterceptors(isEdit ? updateModel(request) : createModel(request));
      if (data?.success) {
        message.success(t(isEdit ? 'edit_model_success' : 'start_model_success'));
        form.resetFields();
        onSuccess?.();
      }
    } catch (_error) {
      message.error(t(isEdit ? 'edit_model_failed' : 'start_model_failed'));
    } finally {
      setLoading(false);
    }
  }

  const renderTooltipContent = (model: SupportModel) => (
    <div className='max-w-md'>
      <div className='whitespace-pre-wrap markdown-body'>
        <ReactMarkdown>{model.description || model.model}</ReactMarkdown>
      </div>
      <div className='mt-2 text-xs opacity-75'>
        {model.enabled ? `${model.host}:${model.port}` : t('download_model_tip')}
      </div>
    </div>
  );

  return (
    <Form form={form} labelCol={{ span: 8 }} wrapperCol={{ span: 16 }} onFinish={onFinish}>
      <FormItem
        label='Worker Type'
        name='worker_type'
        rules={[{ required: true, message: t('worker_type_select_tips') }]}
      >
        <Select disabled={isEdit} onChange={handleWorkerTypeChange} placeholder={t('model_select_worker_type')}>
          {WORKER_TYPES.map(type => (
            <Option key={type} value={type}>
              {type}
            </Option>
          ))}
        </Select>
      </FormItem>

      {selectedWorkerType && (
        <FormItem label='Provider' name='provider' rules={[{ required: true, message: t('provider_select_tips') }]}>
          <Select
            disabled={isEdit}
            onChange={handleProviderChange}
            placeholder={t('model_select_provider')}
            value={selectedProvider}
          >
            {providers.map(provider => (
              <Option key={provider} value={provider}>
                {provider}
              </Option>
            ))}
          </Select>
        </FormItem>
      )}

      {selectedProvider && selectedWorkerType && params && (
        <>
          <FormItem
            label={t('model_deploy_name')}
            name='name'
            rules={[{ required: true, message: t('model_please_input_name') }]}
          >
            <AutoComplete
              disabled={isEdit}
              style={{ width: '100%' }}
              placeholder={t('model_select_or_input_model')}
              options={groupedModels[selectedProvider]
                ?.filter(model => model.worker_type === selectedWorkerType)
                .map(model => ({
                  value: model.model,
                  label: (
                    <div className='flex items-center w-full'>
                      <div className='flex items-center'>
                        {renderModelIcon(model.model)}
                        <Tooltip title={renderTooltipContent(model)} placement='right'>
                          <span className='ml-2'>{model.model}</span>
                        </Tooltip>
                      </div>
                    </div>
                  ),
                }))}
              filterOption={(inputValue, option) =>
                option!.value.toUpperCase().indexOf(inputValue.toUpperCase()) !== -1
              }
            />
          </FormItem>

          <ConfigurableForm params={params.filter(p => !MODEL_IDENTITY_PARAM_NAMES.has(p.param_name))} form={form} />
        </>
      )}

      <div className='flex justify-center space-x-4'>
        <Button type='primary' htmlType='submit' loading={loading}>
          {t('submit')}
        </Button>
        <Button onClick={onCancel}>{t('cancel')}</Button>
      </div>
    </Form>
  );
}

export default ModelForm;
