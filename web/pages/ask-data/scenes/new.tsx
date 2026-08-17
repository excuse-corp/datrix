import AskDataPageShell from '@/components/ask-data/AskDataPageShell';
import SceneEditorForm from '@/components/ask-data/SceneEditorForm';
import { createScene } from '@/utils/ask-data';
import { buildSemanticMarkdown, defaultSceneEditorValues, type SceneEditorValues } from '@/utils/ask-data-semantic';
import { ArrowLeftOutlined, PlusOutlined } from '@ant-design/icons';
import { Button, Form, message } from 'antd';
import { useRouter } from 'next/router';
import { useState } from 'react';

const createSceneId = () => `scene_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;

export default function NewScenePage() {
  const router = useRouter();
  const [form] = Form.useForm<SceneEditorValues>();
  const [submitting, setSubmitting] = useState(false);

  const submit = async (values: SceneEditorValues) => {
    const sceneId = createSceneId();
    setSubmitting(true);
    try {
      await createScene({
        scene_id: sceneId,
        name: values.name,
        description: values.description,
        data_source_name: values.data_source_name,
        view_name: values.view_name,
        semantic_md: buildSemanticMarkdown(sceneId, values),
      });
      message.success('场景已创建、校验并启用');
      await router.push(`/ask-data/scenes/detail?scene_id=${encodeURIComponent(sceneId)}`);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '创建失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AskDataPageShell
      title='新建场景'
      description='填写场景介绍并上传数据字典、业务语义文档；创建时会自动校验并启用。'
      maxWidth='max-w-6xl'
      actions={
        <Button href='/ask-data/scenes' icon={<ArrowLeftOutlined />}>
          返回场景管理
        </Button>
      }
    >
      <SceneEditorForm
        form={form}
        initialValues={defaultSceneEditorValues}
        submitting={submitting}
        submitLabel='创建并校验'
        submitIcon={<PlusOutlined />}
        onSubmit={values => void submit(values)}
      />
    </AskDataPageShell>
  );
}
