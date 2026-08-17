import { LayoutOutlined, SkinOutlined } from '@ant-design/icons';
import { Tooltip } from 'antd';
import { useCallback, useEffect, useState } from 'react';

import { STORAGE_INTERFACE_STYLE_KEY } from '@/utils/constants/index';

export type InterfaceStyle = 'default' | 'industrial';

const getInterfaceStyle = (): InterfaceStyle => {
  if (typeof window === 'undefined') return 'default';
  return window.localStorage.getItem(STORAGE_INTERFACE_STYLE_KEY) === 'industrial' ? 'industrial' : 'default';
};

const applyInterfaceStyle = (style: InterfaceStyle) => {
  document.documentElement.dataset.interfaceStyle = style;
  window.localStorage.setItem(STORAGE_INTERFACE_STYLE_KEY, style);
  window.dispatchEvent(new Event('dataman-interface-style-change'));
};

const InterfaceStyleSwitch = ({ compact = false }: { compact?: boolean }) => {
  const [style, setStyle] = useState<InterfaceStyle>('default');

  useEffect(() => {
    const syncStyle = () => setStyle(getInterfaceStyle());
    syncStyle();
    window.addEventListener('dataman-interface-style-change', syncStyle);
    return () => window.removeEventListener('dataman-interface-style-change', syncStyle);
  }, []);

  const toggleStyle = useCallback(() => {
    applyInterfaceStyle(style === 'industrial' ? 'default' : 'industrial');
  }, [style]);

  const industrial = style === 'industrial';
  const label = industrial ? '切换为标准界面' : '切换为工业归档界面';

  return (
    <Tooltip title={label}>
      <button
        type='button'
        onClick={toggleStyle}
        aria-label={label}
        className={compact ? 'interface-style-switch interface-style-switch-compact' : 'interface-style-switch'}
      >
        {industrial ? <LayoutOutlined /> : <SkinOutlined />}
        {!compact && <span>{industrial ? '标准界面' : '工业归档'}</span>}
      </button>
    </Tooltip>
  );
};

export default InterfaceStyleSwitch;
