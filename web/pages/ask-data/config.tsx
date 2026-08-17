import AskDataPageShell from '@/components/ask-data/AskDataPageShell';
import { getConfig, type AskDataConfig } from '@/utils/ask-data';
import { SafetyCertificateOutlined } from '@ant-design/icons';
import { Alert, Card, Spin, Statistic, message } from 'antd';
import { useEffect, useState } from 'react';

export default function AskDataConfigPage() {
  const [config, setConfig] = useState<AskDataConfig | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    void getConfig()
      .then(payload => setConfig(payload.data ?? null))
      .catch(error => message.error(error instanceof Error ? error.message : '无权读取运行配置'))
      .finally(() => setLoading(false));
  }, []);
  return (
    <AskDataPageShell
      title='问数配置'
      description='查看服务端受控策略与资源限制；数据库连接、SQL 和运行时密钥不会在此暴露。'
      maxWidth='max-w-5xl'
    >
      {loading ? (
        <div className='flex min-h-[280px] items-center justify-center rounded-2xl border border-gray-200/80 bg-white dark:border-white/10 dark:bg-[#1a1b1e]'>
          <Spin />
        </div>
      ) : config ? (
        <>
          <section className='grid gap-3 sm:grid-cols-3'>
            <Card className='!rounded-2xl !border-gray-200/80 !shadow-sm dark:!border-white/10 dark:!bg-[#1a1b1e]'>
              <Statistic title='最大并发请求' value={config.max_concurrent_queries} />
            </Card>
            <Card className='!rounded-2xl !border-gray-200/80 !shadow-sm dark:!border-white/10 dark:!bg-[#1a1b1e]'>
              <Statistic title='单请求并行任务' value={config.max_parallel_tasks} />
            </Card>
            <Card className='!rounded-2xl !border-gray-200/80 !shadow-sm dark:!border-white/10 dark:!bg-[#1a1b1e]'>
              <Statistic title='单数据源并发数' value={config.max_parallel_per_datasource} />
            </Card>
          </section>

          <section className='grid gap-5 lg:grid-cols-[1.1fr_0.9fr]'>
            <Card
              title='超时策略'
              className='!rounded-2xl !border-gray-200/80 !shadow-sm dark:!border-white/10 dark:!bg-[#1a1b1e]'
            >
              <div className='grid gap-3 sm:grid-cols-3'>
                {[
                  ['排队超时', config.query_queue_timeout_seconds],
                  ['子任务超时', config.task_timeout_seconds],
                  ['总请求超时', config.total_timeout_seconds],
                ].map(([label, value]) => (
                  <div key={String(label)} className='rounded-xl bg-gray-50 p-3 dark:bg-[#111217]'>
                    <p className='m-0 text-xs text-gray-400'>{label}</p>
                    <p className='mt-1 mb-0 text-lg font-semibold text-gray-800 dark:text-gray-100'>{value} 秒</p>
                  </div>
                ))}
              </div>
            </Card>
            <Card
              title='安全边界'
              className='!rounded-2xl !border-gray-200/80 !shadow-sm dark:!border-white/10 dark:!bg-[#1a1b1e]'
            >
              <div className='flex items-start gap-3'>
                <SafetyCertificateOutlined className='mt-1 text-lg text-green-600' />
                <div>
                  <p className='m-0 text-sm font-medium text-gray-800 dark:text-gray-100'>运行时敏感信息已隔离</p>
                  <p className='mt-1 mb-0 text-sm leading-6 text-gray-500 dark:text-gray-400'>
                    数据库连接、SQL 与密钥仅由服务端策略管理，页面不会展示或下发这些内容。
                  </p>
                </div>
              </div>
            </Card>
          </section>
        </>
      ) : (
        <Alert className='!rounded-xl' type='info' showIcon message='配置不可用或当前账号没有管理员权限' />
      )}
    </AskDataPageShell>
  );
}
