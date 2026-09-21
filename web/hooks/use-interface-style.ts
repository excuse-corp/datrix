import { InterfaceStyle, getInterfaceStyle } from '@/utils/interface-style';
import { useEffect, useState } from 'react';

export function useInterfaceStyle() {
  const [style, setStyle] = useState<InterfaceStyle>('dashboard');
  useEffect(() => {
    const update = () => setStyle(getInterfaceStyle());
    update();
    window.addEventListener('dataman-interface-style-change', update);
    window.addEventListener('storage', update);
    return () => {
      window.removeEventListener('dataman-interface-style-change', update);
      window.removeEventListener('storage', update);
    };
  }, []);
  return style;
}
