import { ChatContext, ChatContextProvider } from '@/app/chat-context';
import SideBar from '@/components/layout/side-bar';
import { STORAGE_LANG_KEY, STORAGE_USERINFO_KEY, STORAGE_USERINFO_VALID_TIME_KEY } from '@/utils/constants/index';
import { getInterfaceStyle, isDashboardStyle, type InterfaceStyle } from '@/utils/interface-style';
import { App, ConfigProvider, MappingAlgorithm, theme } from 'antd';
import enUS from 'antd/locale/en_US';
import zhCN from 'antd/locale/zh_CN';
import classNames from 'classnames';
import type { AppProps } from 'next/app';
import Head from 'next/head';
import { useRouter } from 'next/router';
import React, { useContext, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import 'reactflow/dist/style.css';
import '../app/i18n';
import '../nprogress.css';
import '../styles/globals.css';
// import TopProgressBar from '@/components/layout/top-progress-bar';

const antdDarkTheme: MappingAlgorithm = (seedToken, mapToken) => {
  return {
    ...theme.darkAlgorithm(seedToken, mapToken),
    colorBgBase: '#232734',
    colorBorder: '#828282',
    colorBgContainer: '#232734',
  };
};

function CssWrapper({ children }: { children: React.ReactElement }) {
  const { mode } = useContext(ChatContext);
  const { i18n } = useTranslation();

  useEffect(() => {
    const interfaceStyle = getInterfaceStyle();
    const effectiveMode = interfaceStyle === 'terminal' || isDashboardStyle(interfaceStyle) ? 'light' : mode;
    document.body.classList.toggle('dark', effectiveMode === 'dark');
    document.body.classList.toggle('light', effectiveMode !== 'dark');
  }, [mode]);

  useEffect(() => {
    i18n.changeLanguage?.(window.localStorage.getItem(STORAGE_LANG_KEY) || 'zh');
  }, [i18n]);

  useEffect(() => {
    const syncInterfaceStyle = () => {
      const interfaceStyle = getInterfaceStyle();
      document.documentElement.dataset.interfaceStyle = interfaceStyle;
      document.body.classList.toggle('terminal', interfaceStyle === 'terminal');
      document.body.classList.toggle('dashboard', isDashboardStyle(interfaceStyle));
      document.body.classList.toggle('workspace', interfaceStyle === 'workspace');
      const forceLight = interfaceStyle === 'terminal' || isDashboardStyle(interfaceStyle);
      document.body.classList.toggle('dark', !forceLight && mode === 'dark');
      document.body.classList.toggle('light', forceLight || mode !== 'dark');
    };
    syncInterfaceStyle();
    window.addEventListener('dataman-interface-style-change', syncInterfaceStyle);
    return () => window.removeEventListener('dataman-interface-style-change', syncInterfaceStyle);
  }, [mode]);

  return (
    <div className='app-root'>
      {/* <TopProgressBar /> */}
      {children}
    </div>
  );
}

function LayoutWrapper({ children }: { children: React.ReactNode }) {
  const { isMenuExpand, mode } = useContext(ChatContext);
  const { i18n } = useTranslation();
  const [isLogin, setIsLogin] = useState(false);
  const [interfaceStyle, setInterfaceStyle] = useState<InterfaceStyle>('dashboard');

  const router = useRouter();

  // 登录检测
  const handleAuth = async () => {
    setIsLogin(false);
    // 如果已有登录信息，直接展示首页
    // if (localStorage.getItem(STORAGE_USERINFO_KEY)) {
    //   setIsLogin(true);
    //   return;
    // }

    // MOCK User info
    const user = {
      user_channel: `dbgpt`,
      user_no: `001`,
      nick_name: `dbgpt`,
    };
    if (user) {
      localStorage.setItem(STORAGE_USERINFO_KEY, JSON.stringify(user));
      localStorage.setItem(STORAGE_USERINFO_VALID_TIME_KEY, Date.now().toString());
      setIsLogin(true);
    }
  };

  useEffect(() => {
    handleAuth();
  }, []);

  useEffect(() => {
    const syncInterfaceStyle = () => setInterfaceStyle(getInterfaceStyle());
    syncInterfaceStyle();
    window.addEventListener('dataman-interface-style-change', syncInterfaceStyle);
    return () => window.removeEventListener('dataman-interface-style-change', syncInterfaceStyle);
  }, []);

  if (!isLogin && !router.pathname.startsWith('/share')) {
    return null;
  }

  const renderContent = () => {
    // Hide sidebar for mobile, share pages, and task replay mode (from_task)
    const hideSidebar =
      router.pathname.includes('mobile') || router.pathname.startsWith('/share') || !!router.query.from_task;

    if (router.pathname.includes('mobile') || router.pathname.startsWith('/share')) {
      return <>{children}</>;
    }
    return (
      <div className='flex h-full min-h-0 w-full overflow-hidden'>
        <Head>
          <meta name='viewport' content='initial-scale=1.0, width=device-width, maximum-scale=1' />
        </Head>
        {router.pathname !== '/construct/app/extra' && !hideSidebar && (
          <div
            className={classNames(
              'app-sidebar-slot transition-[width]',
              isMenuExpand ? 'w-60' : 'w-20',
              isMenuExpand ? 'dashboard-sidebar-slot-expanded' : 'dashboard-sidebar-slot-collapsed',
              'hidden',
              'md:block',
            )}
          >
            <SideBar />
          </div>
        )}
        <div
          className={classNames('dataman-main-workspace flex min-h-0 flex-1 flex-col overflow-hidden relative', {
            'dashboard-chat-host': router.pathname === '/' || router.pathname === '/chat',
          })}
        >
          {children}
        </div>
      </div>
    );
  };

  return (
    <ConfigProvider
      locale={i18n.language === 'en' ? enUS : zhCN}
      theme={{
        token: {
          colorPrimary:
            interfaceStyle === 'terminal' ? '#292824' : isDashboardStyle(interfaceStyle) ? '#209F85' : '#0C75FC',
          colorText:
            interfaceStyle === 'terminal' ? '#292824' : isDashboardStyle(interfaceStyle) ? '#1B2B27' : undefined,
          colorTextSecondary:
            interfaceStyle === 'terminal' ? '#716B60' : isDashboardStyle(interfaceStyle) ? '#6F8D83' : undefined,
          colorTextPlaceholder:
            interfaceStyle === 'terminal' ? '#8A8174' : isDashboardStyle(interfaceStyle) ? '#9BB8AE' : undefined,
          colorBgBase:
            interfaceStyle === 'terminal' ? '#F3EDE2' : isDashboardStyle(interfaceStyle) ? '#F2F8F1' : undefined,
          colorBgContainer:
            interfaceStyle === 'terminal' ? '#FAF6EE' : isDashboardStyle(interfaceStyle) ? '#FFFFFF' : undefined,
          colorBgElevated:
            interfaceStyle === 'terminal' ? '#FAF6EE' : isDashboardStyle(interfaceStyle) ? '#FFFFFF' : undefined,
          colorBorder:
            interfaceStyle === 'terminal' ? '#C9C0B1' : isDashboardStyle(interfaceStyle) ? '#E7F1EA' : undefined,
          colorBorderSecondary:
            interfaceStyle === 'terminal' ? '#DED5C7' : isDashboardStyle(interfaceStyle) ? '#E7F1EA' : undefined,
          borderRadius: interfaceStyle === 'terminal' ? 0 : isDashboardStyle(interfaceStyle) ? 14 : 4,
          fontFamily:
            interfaceStyle === 'terminal'
              ? '"JetBrains Mono", "SFMono-Regular", Consolas, "Noto Sans SC", "Microsoft YaHei", monospace'
              : isDashboardStyle(interfaceStyle)
                ? '"Manrope", "Inter", "Noto Sans SC", "Microsoft YaHei", system-ui, sans-serif'
                : undefined,
        },
        algorithm:
          interfaceStyle === 'terminal' || isDashboardStyle(interfaceStyle)
            ? undefined
            : mode === 'dark'
              ? antdDarkTheme
              : undefined,
        components:
          interfaceStyle === 'terminal'
            ? {
                Button: { borderRadius: 0, controlHeight: 32 },
                Input: { borderRadius: 0, controlHeight: 34 },
                Select: { borderRadius: 0, controlHeight: 34 },
                Modal: { borderRadius: 0 },
                Card: { borderRadius: 0 },
                Dropdown: { borderRadius: 0 },
              }
            : isDashboardStyle(interfaceStyle)
              ? {
                  Button: { borderRadius: 12, controlHeight: 36 },
                  Input: { borderRadius: 12, controlHeight: 38 },
                  Select: { borderRadius: 12, controlHeight: 38 },
                  Modal: { borderRadius: 20 },
                  Card: { borderRadius: 20 },
                  Dropdown: { borderRadius: 12 },
                  Tabs: { itemSelectedColor: '#209F85', inkBarColor: '#209F85' },
                }
              : undefined,
      }}
    >
      <App className='app-provider-root'>{renderContent()}</App>
    </ConfigProvider>
  );
}

function MyApp({ Component, pageProps }: AppProps) {
  return (
    <ChatContextProvider>
      <CssWrapper>
        <LayoutWrapper>
          <Component {...pageProps} />
        </LayoutWrapper>
      </CssWrapper>
    </ChatContextProvider>
  );
}

export default MyApp;
