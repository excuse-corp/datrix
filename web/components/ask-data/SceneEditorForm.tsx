import { queryLimitDefinitions, stripDocumentFrontMatter, type SceneEditorValues } from '@/utils/ask-data-semantic';
import {
  ControlOutlined,
  DatabaseOutlined,
  FileTextOutlined,
  InfoCircleOutlined,
  UploadOutlined,
} from '@ant-design/icons';
import type { UploadProps } from 'antd';
import { Alert, Button, Form, Input, InputNumber, Switch, Upload, message } from 'antd';
import type { ReactNode } from 'react';

type SceneEditorFormProps = {
  form: ReturnType<typeof Form.useForm<SceneEditorValues>>[0];
  initialValues?: Partial<SceneEditorValues>;
  submitting: boolean;
  submitLabel: string;
  submitIcon?: ReactNode;
  onSubmit: (values: SceneEditorValues) => void;
};

const required = (label: string) => [{ required: true, whitespace: true, message: `请输入${label}` }];

const SectionTitle = ({ icon, title, description }: { icon: ReactNode; title: string; description: string }) => (
  <div className='border-y border-gray-100 px-5 py-4 first:border-t-0 dark:border-white/10 md:px-6'>
    <div className='flex items-center gap-3'>
      <div className='flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gray-100 text-gray-600 dark:bg-white/10 dark:text-gray-300'>
        {icon}
      </div>
      <div className='min-w-0'>
        <h2 className='m-0 text-sm font-medium text-gray-900 dark:text-gray-100'>{title}</h2>
        <p className='mt-1 mb-0 text-xs text-gray-500 dark:text-gray-400'>{description}</p>
      </div>
    </div>
  </div>
);

export default function SceneEditorForm({
  form,
  initialValues,
  submitting,
  submitLabel,
  submitIcon,
  onSubmit,
}: SceneEditorFormProps) {
  const importDocument =
    (field: 'data_dictionary_md' | 'business_semantics_md'): UploadProps['beforeUpload'] =>
    async file => {
      if (!file.name.toLowerCase().endsWith('.md')) {
        message.error('仅支持上传 .md 格式的文档');
        return Upload.LIST_IGNORE;
      }
      try {
        form.setFieldValue(field, stripDocumentFrontMatter(await file.text()));
        await form.validateFields([field]);
        message.success(`已导入 ${file.name}`);
      } catch (error) {
        message.error(error instanceof Error ? error.message : '文档读取失败');
      }
      return Upload.LIST_IGNORE;
    };

  return (
    <Form
      form={form}
      layout='vertical'
      requiredMark
      initialValues={initialValues}
      onFinish={onSubmit}
      className='ask-data-scene-form'
    >
      <section className='overflow-hidden rounded-2xl border border-gray-200/80 bg-white shadow-sm dark:border-white/10 dark:bg-[#1a1b1e]'>
        <SectionTitle icon={<InfoCircleOutlined />} title='基础信息' description='填写场景的目录展示信息' />
        <div className='px-5 pt-5 md:px-6'>
          <Form.Item name='name' label='场景名称' rules={required('场景名称')}>
            <Input allowClear placeholder='例如：合同分析' />
          </Form.Item>
          <Form.Item name='description' label='场景介绍' rules={required('场景介绍')}>
            <Input.TextArea
              showCount
              maxLength={500}
              autoSize={{ minRows: 3, maxRows: 6 }}
              placeholder='说明该场景可查询的业务范围，供主 Agent 选择场景'
            />
          </Form.Item>
        </div>

        <SectionTitle icon={<DatabaseOutlined />} title='数据绑定' description='关联已配置的数据源与业务表或视图' />
        <div className='grid gap-x-5 px-5 pt-5 md:grid-cols-2 md:px-6'>
          <Form.Item name='data_source_name' label='数据源名称' rules={required('数据源名称')}>
            <Input allowClear placeholder='例如：dataman_data' />
          </Form.Item>
          <Form.Item name='view_name' label='绑定对象' rules={required('绑定对象')}>
            <Input allowClear placeholder='例如：sync.pcm_project_info 或 reporting.vw_contract_report' />
          </Form.Item>
        </div>

        <SectionTitle icon={<ControlOutlined />} title='查询限制' description='控制单次场景查询的返回规模和超时' />
        <div className='grid gap-x-5 px-5 pt-5 md:grid-cols-2 lg:grid-cols-3 md:px-6'>
          {queryLimitDefinitions.map(definition => (
            <Form.Item
              key={definition.key}
              name={['query_limits', definition.key]}
              label={definition.label}
              extra={definition.description}
              valuePropName={definition.type === 'boolean' ? 'checked' : undefined}
              rules={
                definition.type === 'number' ? [{ required: true, message: `请输入${definition.label}` }] : undefined
              }
            >
              {definition.type === 'boolean' ? (
                <Switch checkedChildren='允许' unCheckedChildren='关闭' />
              ) : (
                <InputNumber
                  className='!w-full'
                  min={definition.min}
                  max={definition.max}
                  precision={0}
                  addonAfter={definition.unit}
                />
              )}
            </Form.Item>
          ))}
        </div>

        <SectionTitle
          icon={<FileTextOutlined />}
          title='场景文档'
          description='两份文档均支持上传后继续在线编辑；用于说明字段和业务关系'
        />
        <div className='grid gap-5 px-5 py-5 lg:grid-cols-2 md:px-6'>
          <Alert
            className='lg:col-span-2'
            type='info'
            showIcon
            message='业务语义文档用于解释业务关系、粒度、口径和边界。'
            description='无需填写查询指标或查询维度表。系统会根据绑定对象的实际 Schema 和这两份文档生成单场景 SQL；文档只会在命中本场景后提供给对应的场景子 Agent。'
          />
          <div className='min-w-0'>
            <div className='mb-3 flex items-center justify-between gap-3'>
              <div>
                <h3 className='m-0 text-sm font-medium text-gray-900 dark:text-gray-100'>
                  <span className='mr-1 text-red-500'>*</span>数据字典
                </h3>
                <p className='mt-1 mb-0 text-xs text-gray-500'>字段含义、类型、枚举值和数据口径</p>
              </div>
              <Upload accept='.md' showUploadList={false} beforeUpload={importDocument('data_dictionary_md')}>
                <Button icon={<UploadOutlined />}>上传</Button>
              </Upload>
            </div>
            <Form.Item name='data_dictionary_md' rules={required('数据字典')} className='!mb-0'>
              <Input.TextArea
                spellCheck={false}
                className='!min-h-[420px] !resize-y !font-mono !text-[13px] !leading-6'
                placeholder={
                  '## 字段说明\n\n| 字段 | 类型 | 含义 |\n| --- | --- | --- |\n| contract_id | string | 合同编号 |'
                }
              />
            </Form.Item>
          </div>
          <div className='min-w-0'>
            <div className='mb-3 flex items-center justify-between gap-3'>
              <div>
                <h3 className='m-0 text-sm font-medium text-gray-900 dark:text-gray-100'>
                  <span className='mr-1 text-red-500'>*</span>业务语义文档
                </h3>
                <p className='mt-1 mb-0 text-xs text-gray-500'>业务关系、口径、默认解释和问数边界</p>
              </div>
              <Upload accept='.md' showUploadList={false} beforeUpload={importDocument('business_semantics_md')}>
                <Button icon={<UploadOutlined />}>上传</Button>
              </Upload>
            </div>
            <Form.Item name='business_semantics_md' rules={required('业务语义文档')} className='!mb-0'>
              <Input.TextArea
                spellCheck={false}
                className='!min-h-[420px] !resize-y !font-mono !text-[13px] !leading-6'
                placeholder={
                  '## 业务范围\n\n- 一行表示一个项目及其合同补充信息。\n\n## 业务关系\n\n- 项目联系人负责日常实施和跟进；项目负责人是制度责任人。\n\n## 业务口径\n\n- “某人的项目”默认按项目联系人查询。\n- 金额为空时不可按零处理。\n\n## 边界\n\n- 不推断未同步或缺失的数据。'
                }
              />
            </Form.Item>
          </div>
        </div>

        <div className='flex flex-wrap items-center justify-end gap-3 border-t border-gray-100 bg-gray-50/70 px-5 py-4 dark:border-white/10 dark:bg-[#111217] md:px-6'>
          <Button href='/ask-data/scenes' disabled={submitting}>
            取消
          </Button>
          <Button type='primary' htmlType='submit' icon={submitIcon} loading={submitting}>
            {submitLabel}
          </Button>
        </div>
      </section>
    </Form>
  );
}
