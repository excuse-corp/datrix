import AskDataPageShell from '@/components/ask-data/AskDataPageShell';
import { listCapabilities, type Capability } from '@/utils/ask-data';
import { ArrowRightOutlined, SearchOutlined } from '@ant-design/icons';
import { Button, Card, Empty, Input, Spin, Typography, message } from 'antd';
import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';

export default function CapabilitiesPage() {
  const [items, setItems] = useState<Capability[]>([]);
  const [loading, setLoading] = useState(true);
  const [keyword, setKeyword] = useState('');
  useEffect(() => {
    void listCapabilities()
      .then(payload => setItems(payload.data?.items ?? []))
      .catch(() => message.error('加载能力目录失败'))
      .finally(() => setLoading(false));
  }, []);
  const filtered = useMemo(
    () =>
      items.filter(item =>
        `${item.name} ${item.description} ${item.keywords.join(' ')}`.toLowerCase().includes(keyword.toLowerCase()),
      ),
    [items, keyword],
  );
  return (
    <AskDataPageShell
      title='场景目录'
      description='浏览已授权的业务场景，并在首页聊天中直接发起问数。'
      maxWidth='max-w-6xl'
      actions={
        <Button type='primary' href='/' icon={<ArrowRightOutlined />}>
          前往首页聊天
        </Button>
      }
    >
      <section className='rounded-2xl border border-gray-200/80 bg-white p-3 shadow-sm dark:border-white/10 dark:bg-[#1a1b1e]'>
        <Input
          allowClear
          prefix={<SearchOutlined className='text-gray-400' />}
          placeholder='搜索场景、描述或关键词'
          onChange={event => setKeyword(event.target.value)}
          className='!rounded-xl !border-0 !bg-gray-50 !px-3 !py-2 !shadow-none dark:!bg-[#111217]'
        />
      </section>

      {loading ? (
        <div className='flex min-h-[280px] items-center justify-center rounded-2xl border border-gray-200/80 bg-white dark:border-white/10 dark:bg-[#1a1b1e]'>
          <Spin />
        </div>
      ) : filtered.length ? (
        <section className='grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3'>
          {filtered.map(item => {
            const primaryQuestion = item.name;
            const detailHref = `/ask-data/scenes/detail?scene_id=${encodeURIComponent(item.scene_id)}`;
            return (
              <Card
                key={item.scene_id}
                className='h-full !rounded-2xl !border-gray-200/80 !shadow-sm transition hover:!-translate-y-0.5 hover:!shadow-md dark:!border-white/10 dark:!bg-[#1a1b1e]'
                bodyStyle={{ height: '100%' }}
              >
                <div className='flex h-full min-h-[220px] flex-col'>
                  <div className='mb-3 flex items-start justify-between gap-3'>
                    <div className='min-w-0'>
                      <Link
                        href={detailHref}
                        className='text-base font-semibold text-gray-900 hover:text-blue-600 dark:text-gray-100'
                      >
                        {item.name}
                      </Link>
                      <Typography.Paragraph
                        className='!mt-2 !mb-0 !text-sm !leading-6 !text-gray-500 dark:!text-gray-400'
                        ellipsis={{ rows: 3 }}
                      >
                        {item.description}
                      </Typography.Paragraph>
                    </div>
                  </div>

                  <div className='mt-auto flex items-center justify-between border-t border-gray-100 pt-3 dark:border-white/10'>
                    <Link href={detailHref} className='text-sm text-blue-600 hover:text-blue-700'>
                      查看详情
                    </Link>
                    <Button type='primary' size='small' href={`/?q=${encodeURIComponent(primaryQuestion)}`}>
                      提问
                    </Button>
                  </div>
                </div>
              </Card>
            );
          })}
        </section>
      ) : (
        <div className='rounded-2xl border border-gray-200/80 bg-white py-16 dark:border-white/10 dark:bg-[#1a1b1e]'>
          <Empty description='没有匹配的场景' />
        </div>
      )}
    </AskDataPageShell>
  );
}
