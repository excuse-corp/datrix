import AskDataPageShell from '@/components/ask-data/AskDataPageShell';
import SceneEditorForm from '@/components/ask-data/SceneEditorForm';
import { getScene, getSemanticMarkdown, updateScene } from '@/utils/ask-data';
import { buildSemanticMarkdown, parseSemanticMarkdown, type SceneEditorValues } from '@/utils/ask-data-semantic';
import { ArrowLeftOutlined, SaveOutlined } from '@ant-design/icons';
import { Alert, Button, Form, Spin, message } from 'antd';
import { useRouter } from 'next/router';
import { useEffect, useState } from 'react';

export default function EditScenePage() {
  const router = useRouter();
  const sceneId = typeof router.query.scene_id === 'string' ? router.query.scene_id : '';
  const [form] = Form.useForm<SceneEditorValues>();
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);

  useEffect(() => {
    if (!sceneId) return;
    setLoading(true);
    setLoadFailed(false);
    void Promise.all([getScene(sceneId), getSemanticMarkdown(sceneId)])
      .then(([scene, markdown]) => {
        form.setFieldsValue({
          ...parseSemanticMarkdown(markdown.data?.semantic_md ?? ''),
          name: scene.data?.name ?? '',
          description: scene.data?.description ?? '',
          data_source_name: scene.data?.data_source_name ?? '',
          view_name: scene.data?.view_name ?? '',
        });
      })
      .catch(error => {
        setLoadFailed(true);
        message.error(error instanceof Error ? error.message : '加载场景失败');
      })
      .finally(() => setLoading(false));
  }, [form, sceneId]);

  const submit = async (values: SceneEditorValues) => {
    setSubmitting(true);
    try {
      await updateScene(sceneId, {
        name: values.name,
        description: values.description,
        data_source_name: values.data_source_name,
        view_name: values.view_name,
        semantic_md: buildSemanticMarkdown(sceneId, values),
      });
      message.success('草稿已保存，请返回详情页校验后启用');
      await router.push(`/ask-data/scenes/detail?scene_id=${encodeURIComponent(sceneId)}`);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '保存失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AskDataPageShell
      title='编辑场景'
      description='停用状态下维护场景介绍、绑定对象、数据字典和业务语义；保存后需校验启用。'
      maxWidth='max-w-6xl'
      actions={
        <Button
          href={sceneId ? `/ask-data/scenes/detail?scene_id=${encodeURIComponent(sceneId)}` : '/ask-data/scenes'}
          icon={<ArrowLeftOutlined />}
        >
          返回场景详情
        </Button>
      }
    >
      {loading ? (
        <div className='flex min-h-[360px] items-center justify-center rounded-2xl border border-gray-200/80 bg-white dark:border-white/10 dark:bg-[#1a1b1e]'>
          <Spin />
        </div>
      ) : loadFailed ? (
        <Alert type='error' showIcon message='场景加载失败，请返回场景管理后重试。' />
      ) : (
        <SceneEditorForm
          form={form}
          submitting={submitting}
          submitLabel='保存草稿'
          submitIcon={<SaveOutlined />}
          onSubmit={values => void submit(values)}
        />
      )}
    </AskDataPageShell>
  );
}
