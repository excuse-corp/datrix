import { apiInterceptors, getDefaultModel, getModelList, setDefaultModel, startModel, stopModel } from '@/client/api';
import ModelForm from '@/components/model/model-form';
import { useInterfaceStyle } from '@/hooks/use-interface-style';
import BlurredCard, { InnerDropdown } from '@/new-components/common/blurredCard';
import ConstructLayout from '@/new-components/layout/Construct';
import { IModelData } from '@/types/model';
import { getModelIcon } from '@/utils/constants';
import { isDashboardStyle } from '@/utils/interface-style';
import { EditOutlined, PlusOutlined, StarOutlined } from '@ant-design/icons';
import { Button, Modal, Tag, message } from 'antd';
import moment from 'moment';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

function Models() {
  const { t } = useTranslation();
  const [models, setModels] = useState<Array<IModelData>>([]);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingModel, setEditingModel] = useState<IModelData | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [defaultModel, setDefaultModelName] = useState<string | null>(null);
  const interfaceStyle = useInterfaceStyle();
  const isDashboard = isDashboardStyle(interfaceStyle);

  async function getModels() {
    const [, res] = await apiInterceptors(getModelList());
    setModels(res ?? []);
    const [, current] = await apiInterceptors(getDefaultModel());
    setDefaultModelName(current?.model_name ?? null);
  }
  async function makeDefault(name: string) {
    if (loading) return;
    setLoading(true);
    try {
      const [, result] = await apiInterceptors(setDefaultModel(name));
      if (result?.model_name) {
        setDefaultModelName(result.model_name);
        message.success('默认模型已设置');
      }
    } finally {
      setLoading(false);
    }
  }

  async function startTheModel(info: IModelData) {
    if (loading) return;
    const content = t(`confirm_start_model`) + info.model_name;

    showConfirm(t('start_model'), content, async () => {
      setLoading(true);
      const [, , res] = await apiInterceptors(
        startModel({
          host: info.host,
          port: info.port,
          model: info.model_name,
          worker_type: info.worker_type,
          delete_after: false,
          params: {},
        }),
      );
      setLoading(false);
      if (res?.success) {
        message.success(t('start_model_success'));
        await getModels();
      }
    });
  }

  async function stopTheModel(info: IModelData, delete_after = false) {
    if (loading) return;

    const action = delete_after ? 'stop_and_delete' : 'stop';
    const content = t(`confirm_${action}_model`) + info.model_name;
    showConfirm(t(`${action}_model`), content, async () => {
      setLoading(true);
      const [, , res] = await apiInterceptors(
        stopModel({
          host: info.host,
          port: info.port,
          model: info.model_name,
          worker_type: info.worker_type,
          delete_after: delete_after,
          params: {},
        }),
      );
      setLoading(false);
      if (res?.success === true) {
        message.success(t(`${action}_model_success`));
        await getModels();
      }
    });
  }

  const showConfirm = (title: string, content: string, onOk: () => Promise<void>) => {
    Modal.confirm({
      title,
      content,
      onOk: async () => {
        await onOk();
      },
      okButtonProps: {
        className: 'bg-button-gradient',
      },
    });
  };

  useEffect(() => {
    getModels();
  }, []);

  // TODO: unuesed function
  // const onSearch = useDebounceFn(
  //   async (e: any) => {
  //     const v = e.target.value;
  //     await modelSearch({ model_name: v });
  //   },
  //   { wait: 500 },
  // ).run;

  const returnLogo = (name: string) => {
    return getModelIcon(name);
  };

  const healthyCount = models.filter(item => item.healthy).length;
  const unhealthyCount = models.length - healthyCount;

  return (
    <ConstructLayout>
      <div className='dashboard-models-page px-6 overflow-y-auto'>
        {isDashboard ? (
          <>
            <div className='dashboard-page-header'>
              <div>
                <div className='dashboard-page-title'>模型管理</div>
                <div className='dashboard-page-subtitle'>查看模型健康状态、默认模型和运行地址</div>
              </div>
              <div className='flex items-center gap-4'>
                <Button
                  className='border-none text-white bg-button-gradient'
                  icon={<PlusOutlined />}
                  onClick={() => {
                    setEditingModel(null);
                    setIsModalOpen(true);
                  }}
                >
                  {t('create_model')}
                </Button>
              </div>
            </div>

            <div className='dashboard-summary-strip'>
              <div className='dashboard-summary-item'>
                <div className='dashboard-summary-label'>总模型</div>
                <div className='dashboard-summary-value'>{models.length}</div>
              </div>
              <div className='dashboard-summary-item'>
                <div className='dashboard-summary-label'>Healthy</div>
                <div className='dashboard-summary-value'>{healthyCount}</div>
              </div>
              <div className='dashboard-summary-item'>
                <div className='dashboard-summary-label'>Unhealthy</div>
                <div className='dashboard-summary-value'>{unhealthyCount}</div>
              </div>
              <div className='dashboard-summary-item'>
                <div className='dashboard-summary-label'>默认模型</div>
                <div className='dashboard-summary-value truncate' title={defaultModel ?? undefined}>
                  {defaultModel || '未设置'}
                </div>
              </div>
            </div>
          </>
        ) : (
          <div className='flex justify-between items-center mb-6'>
            <div className='flex items-center gap-4' />
            <div className='flex items-center gap-4'>
              <Button
                className='border-none text-white bg-button-gradient'
                icon={<PlusOutlined />}
                onClick={() => {
                  setEditingModel(null);
                  setIsModalOpen(true);
                }}
              >
                {t('create_model')}
              </Button>
            </div>
          </div>
        )}

        <div className='dashboard-model-grid flex flex-wrap mx-[-8px]'>
          {models.map(item => (
            <BlurredCard
              className='dashboard-model-card'
              logo={returnLogo(item.model_name)}
              description={
                <div className='flex flex-col gap-1 relative text-xs bottom-4'>
                  <div className='flex overflow-hidden'>
                    <p className='w-28 text-gray-500 mr-2'>Host:</p>
                    <p className='flex-1 text-ellipsis'>{item.host}</p>
                  </div>
                  <div className='flex overflow-hidden'>
                    <p className='w-28 text-gray-500 mr-2'>Manage Host:</p>
                    <p className='flex-1 text-ellipsis'>
                      {item.manager_host}:{item.manager_port}
                    </p>
                  </div>
                  <div className='flex overflow-hidden'>
                    <p className='w-28 text-gray-500 mr-2'>Last Heart Beat:</p>
                    <p className='flex-1 text-ellipsis'>{moment(item.last_heartbeat).format('YYYY-MM-DD HH:mm:ss')}</p>
                  </div>
                </div>
              }
              name={item.model_name}
              key={item.model_name}
              RightTop={
                <InnerDropdown
                  menu={{
                    items: [
                      {
                        key: 'default_model',
                        disabled: !item.healthy || item.worker_type !== 'llm' || defaultModel === item.model_name,
                        label: (
                          <span
                            onClick={() => {
                              if (item.healthy && item.worker_type === 'llm' && defaultModel !== item.model_name) {
                                makeDefault(item.model_name);
                              }
                            }}
                          >
                            设为默认模型
                          </span>
                        ),
                      },
                      {
                        key: 'edit_model',
                        label: (
                          <span
                            onClick={() => {
                              setEditingModel(item);
                              setIsModalOpen(true);
                            }}
                          >
                            {t('edit_model')}
                          </span>
                        ),
                      },
                      {
                        key: 'stop_model',
                        label: (
                          <span className='text-red-400' onClick={() => stopTheModel(item)}>
                            {t('stop_model')}
                          </span>
                        ),
                      },
                      {
                        key: 'start_model',
                        label: (
                          <span className='text-green-400' onClick={() => startTheModel(item)}>
                            {t('start_model')}
                          </span>
                        ),
                      },
                      {
                        key: 'stop_and_delete_model',
                        label: (
                          <span className='text-red-400' onClick={() => stopTheModel(item, true)}>
                            {t('stop_and_delete_model')}
                          </span>
                        ),
                      },
                    ],
                  }}
                />
              }
              rightTopHover={false}
              RightBottom={
                <div className='flex gap-2'>
                  {item.running === false && (
                    <Button
                      size='small'
                      type='primary'
                      onClick={e => {
                        e.stopPropagation();
                        startTheModel(item);
                      }}
                    >
                      {t('start_model')}
                    </Button>
                  )}
                  {item.worker_type === 'llm' && defaultModel !== item.model_name && (
                    <Button
                      size='small'
                      icon={<StarOutlined />}
                      disabled={!item.healthy || loading}
                      title={!item.healthy ? (item.health_reason ?? '模型不可用') : undefined}
                      onClick={e => {
                        e.stopPropagation();
                        makeDefault(item.model_name);
                      }}
                    >
                      设为默认
                    </Button>
                  )}
                  <Button
                    size='small'
                    icon={<EditOutlined />}
                    onClick={e => {
                      e.stopPropagation();
                      setEditingModel(item);
                      setIsModalOpen(true);
                    }}
                  >
                    {t('edit_model')}
                  </Button>
                </div>
              }
              Tags={
                <div>
                  <Tag
                    color={item.running === false ? 'default' : item.healthy ? 'green' : 'red'}
                    title={item.health_reason ?? undefined}
                  >
                    {item.running === false ? '已停止' : item.healthy ? 'Healthy' : 'Unhealthy'}
                  </Tag>
                  <Tag className='model-worker-type-tag'>{item.worker_type}</Tag>
                  {defaultModel === item.model_name && <Tag color='blue'>默认</Tag>}
                </div>
              }
            />
          ))}
        </div>
        <Modal
          width={800}
          open={isModalOpen}
          title={editingModel ? t('edit_model') : t('create_model')}
          onCancel={() => {
            setIsModalOpen(false);
            setEditingModel(null);
          }}
          footer={null}
          destroyOnClose
        >
          <ModelForm
            mode={editingModel ? 'edit' : 'create'}
            initialModel={editingModel ?? undefined}
            onCancel={() => {
              setIsModalOpen(false);
              setEditingModel(null);
            }}
            onSuccess={() => {
              setIsModalOpen(false);
              setEditingModel(null);
              getModels();
            }}
          />
        </Modal>
      </div>
    </ConstructLayout>
  );
}

export default Models;
