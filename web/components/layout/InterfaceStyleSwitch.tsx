import { BorderOutlined, CodeOutlined, DashboardOutlined, LayoutOutlined, SkinOutlined } from '@ant-design/icons';
import { Tooltip } from 'antd';
import { useCallback, useEffect, useState, type ReactNode } from 'react';

import {
  applyInterfaceStyle,
  getInterfaceStyle,
  getNextInterfaceStyle,
  type InterfaceStyle,
} from '@/utils/interface-style';

const InterfaceStyleSwitch = ({ compact = false }: { compact?: boolean }) => {
  const [style, setStyle] = useState<InterfaceStyle>('dashboard');

  useEffect(() => {
    const syncStyle = () => setStyle(getInterfaceStyle());
    syncStyle();
    window.addEventListener('dataman-interface-style-change', syncStyle);
    return () => window.removeEventListener('dataman-interface-style-change', syncStyle);
  }, []);

  const toggleStyle = useCallback(() => {
    applyInterfaceStyle(getNextInterfaceStyle(style));
  }, [style]);

  const nextStyle = getNextInterfaceStyle(style);
  const labels: Record<InterfaceStyle, string> = {
    default: '标准界面',
    industrial: '工业归档',
    terminal: '终端界面',
    dashboard: 'Dashboard',
    workspace: '工作区',
  };
  const icons: Record<InterfaceStyle, ReactNode> = {
    default: <LayoutOutlined />,
    industrial: <SkinOutlined />,
    terminal: <CodeOutlined />,
    dashboard: <DashboardOutlined />,
    workspace: <BorderOutlined />,
  };
  const label = `切换为${labels[nextStyle]}`;

  return (
    <Tooltip title={label}>
      <button
        type='button'
        onClick={toggleStyle}
        aria-label={label}
        className={compact ? 'interface-style-switch interface-style-switch-compact' : 'interface-style-switch'}
      >
        {icons[style]}
        {!compact && <span>{labels[style]}</span>}
      </button>
    </Tooltip>
  );
};

export default InterfaceStyleSwitch;
