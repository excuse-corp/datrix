import { ReadOutlined } from '@ant-design/icons';
import { Tooltip } from 'antd';
import { useRouter } from 'next/router';

import React from 'react';
import { useTranslation } from 'react-i18next';

const Header: React.FC = () => {
  const { t } = useTranslation();
  const router = useRouter();

  if (router.pathname === '/chat' && router.asPath !== '/chat') {
    return null;
  }

  return (
    <header className='flex items-center justify-end fixed top-0 right-0 h-14 pr-11 bg-transparent'>
      <a href='htt://docs.dbgpt.cn' target='_blank' className='flex items-center h-full mr-4' rel='noreferrer'>
        <Tooltip title={t('docs')}>
          <ReadOutlined />
        </Tooltip>
      </a>

      <Tooltip>
        <span className='text-sm'>帮助中心</span>
      </Tooltip>
      {/* <UserBar /> */}
    </header>
  );
};

export default Header;
