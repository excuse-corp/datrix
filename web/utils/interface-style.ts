import { STORAGE_INTERFACE_STYLE_KEY } from '@/utils/constants/index';

export type InterfaceStyle = 'default' | 'industrial' | 'terminal' | 'dashboard' | 'workspace';

const INTERFACE_STYLES: InterfaceStyle[] = ['dashboard', 'workspace'];

export function isDashboardStyle(style: InterfaceStyle): boolean {
  return style === 'dashboard' || style === 'workspace';
}

export function getInterfaceStyle(): InterfaceStyle {
  if (typeof window === 'undefined') return 'dashboard';
  const stored = window.localStorage.getItem(STORAGE_INTERFACE_STYLE_KEY);
  return INTERFACE_STYLES.includes(stored as InterfaceStyle) ? (stored as InterfaceStyle) : 'dashboard';
}

export function applyInterfaceStyle(style: InterfaceStyle) {
  document.documentElement.dataset.interfaceStyle = style;
  window.localStorage.setItem(STORAGE_INTERFACE_STYLE_KEY, style);
  window.dispatchEvent(new Event('dataman-interface-style-change'));
}

export function getNextInterfaceStyle(style: InterfaceStyle): InterfaceStyle {
  const index = INTERFACE_STYLES.indexOf(style);
  return INTERFACE_STYLES[(index + 1) % INTERFACE_STYLES.length];
}
