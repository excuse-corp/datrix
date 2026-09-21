import { ModelSvg } from '@/components/icons';
import { useInterfaceStyle } from '@/hooks/use-interface-style';
import { isDashboardStyle } from '@/utils/interface-style';
import Icon, { ConsoleSqlOutlined, MessageOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { ConfigProvider, Tabs } from 'antd';
import { t } from 'i18next';
import { useRouter } from 'next/router';
import React from 'react';
import './style.css';

function ConstructLayout({ children, className }: { children: React.ReactNode; className?: string }) {
  const items = [
    {
      key: 'models',
      name: t('model_manage'),
      path: '/models',
      icon: <Icon component={ModelSvg} />,
    },
    {
      key: 'database',
      name: t('Database'),
      icon: <ConsoleSqlOutlined />,
      path: '/database',
    },
    // {
    //   key: 'agent',
    //   name: t('Plugins'),
    //   path: '/agent',
    //   icon: <BuildOutlined />,
    // },
    {
      key: 'prompt',
      name: t('Prompt'),
      icon: <MessageOutlined />,
      path: '/prompt',
    },
    {
      key: 'skills',
      name: t('skills') || '技能',
      path: '/skills',
      icon: <ThunderboltOutlined />,
    },
  ];
  const router = useRouter();
  const interfaceStyle = useInterfaceStyle();
  const isDashboard = isDashboardStyle(interfaceStyle);
  const activeKey = router.pathname.split('/')[2];
  // const isDarkMode = window.matchMedia('(prefers-color-scheme: dark)').matches; // unused

  return (
    <div className='dashboard-construct-layout flex flex-col h-full w-full dark:bg-gradient-dark bg-gradient-light bg-cover bg-center'>
      <ConfigProvider
        theme={{
          components: {
            Button: {
              // defaultBorderColor: 'white',
            },
            Segmented: {
              itemSelectedBg: '#2867f5',
              itemSelectedColor: 'white',
            },
          },
        }}
      >
        <Tabs
          className={className}
          // tabBarStyle={{
          //   background: '#edf8fb',
          //   border: 'none',
          //   height: '3.5rem',
          //   padding: '0 1.5rem',
          //   color: !isDarkMode ? 'white' : 'black',
          // }}
          activeKey={activeKey}
          items={items.map(items => {
            return {
              key: items.key,
              label: items.name,
              children: isDashboard ? <div className='dashboard-main-canvas'>{children}</div> : children,
              icon: items.icon,
            };
          })}
          onTabClick={key => {
            router.push(`/construct/${key}`);
          }}
          // tabBarExtraContent={
          //   <Button
          //     className='border-none text-white bg-button-gradient h-full flex items-center'
          //     icon={<PlusOutlined className='text-base' />}
          //     // onClick={handleCreate}
          //   >
          //     {t('create_app')}
          //   </Button>
          // }
        />
      </ConfigProvider>
    </div>
  );
}

export default ConstructLayout;
