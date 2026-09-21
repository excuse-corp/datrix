import { useInterfaceStyle } from '@/hooks/use-interface-style';
import { isDashboardStyle } from '@/utils/interface-style';
import {
  ControlOutlined,
  EditOutlined,
  HistoryOutlined,
  RightOutlined,
  RobotOutlined,
  SettingOutlined,
} from '@ant-design/icons';
import { Typography } from 'antd';
import Link from 'next/link';
import type { ReactNode } from 'react';

type SettingItem = {
  href: string;
  icon: ReactNode;
  label: string;
};

const items: SettingItem[] = [
  { href: '/construct/models', icon: <RobotOutlined />, label: '模型管理' },
  { href: '/construct/prompt', icon: <EditOutlined />, label: '提示词' },
];

const askDataItems: SettingItem[] = [
  { href: '/ask-data/scenes', icon: <ControlOutlined />, label: '场景管理' },
  { href: '/ask-data/runs', icon: <HistoryOutlined />, label: '运行记录' },
  { href: '/ask-data/config', icon: <SettingOutlined />, label: '问数配置' },
];

function SettingsGrid({ items }: { items: SettingItem[] }) {
  return (
    <div className='grid gap-3 sm:grid-cols-2'>
      {items.map(item => (
        <Link
          key={item.href}
          href={item.href}
          className='flex h-14 items-center gap-3 border border-gray-200 bg-white px-4 text-gray-800 transition-colors hover:border-blue-300 hover:bg-blue-50 dark:border-gray-700 dark:bg-[#1e293b] dark:text-gray-100 dark:hover:border-blue-700 dark:hover:bg-blue-950'
        >
          <span className='flex h-8 w-8 items-center justify-center text-lg text-blue-600 dark:text-blue-400'>
            {item.icon}
          </span>
          <span className='flex-1 text-sm font-medium'>{item.label}</span>
          <RightOutlined className='text-xs text-gray-400' />
        </Link>
      ))}
    </div>
  );
}

export default function SettingsPage() {
  const interfaceStyle = useInterfaceStyle();
  const isDashboard = isDashboardStyle(interfaceStyle);

  return (
    <main className='dashboard-main-canvas dashboard-settings-page h-full overflow-auto bg-[#f7f9fc] px-4 py-6 dark:bg-[#111827] md:px-6'>
      <div className='mx-auto max-w-5xl'>
        {isDashboard ? (
          <div className='dashboard-page-header'>
            <div>
              <div className='dashboard-page-title'>设置</div>
              <div className='dashboard-page-subtitle'>管理模型、提示词和问数运行策略</div>
            </div>
            <SettingOutlined className='text-xl text-blue-600 dark:text-blue-400' />
          </div>
        ) : (
          <div className='mb-6 flex items-center gap-3'>
            <SettingOutlined className='text-xl text-blue-600 dark:text-blue-400' />
            <Typography.Title level={2} className='!mb-0'>
              设置
            </Typography.Title>
          </div>
        )}
        <SettingsGrid items={items} />

        <section className='mt-8'>
          <Typography.Title level={4}>问数管理</Typography.Title>
          <Typography.Paragraph type='secondary'>管理业务场景、查询运行记录和受控问数策略。</Typography.Paragraph>
          <SettingsGrid items={askDataItems} />
        </section>
      </div>
    </main>
  );
}
