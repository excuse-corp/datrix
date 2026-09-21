import { ChatContext } from '@/app/chat-context';
import { delDialogue, getDialogueList } from '@/client/api/request';
import { apiInterceptors } from '@/client/api/tools/interceptors';
import InterfaceStyleSwitch from '@/components/layout/InterfaceStyleSwitch';
import type { IChatDialogueSchema } from '@/types/chat';
import {
  ApartmentOutlined,
  AppstoreOutlined,
  BarChartOutlined,
  DeleteOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  MessageOutlined,
  PlusOutlined,
  RightOutlined,
  SettingOutlined,
} from '@ant-design/icons';
import { Skeleton, Tooltip, message } from 'antd';
import cls from 'classnames';
import moment from 'moment';
import 'moment/locale/zh-cn';
import Image from 'next/image';
import Link from 'next/link';
import { useRouter } from 'next/router';
import { useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

type RouteItem = {
  key: string;
  name: string;
  icon?: ReactNode;
  iconSrc?: string;
  activeIconSrc?: string;
  path: string;
  isActive?: boolean;
};

const CHAT_DIALOGUE_UPSERT_EVENT = 'dataman:chat-dialogue-upsert';
const CHAT_DIALOGUE_REFRESH_EVENT = 'dataman:chat-dialogue-refresh';
const CHAT_NEW_TASK_EVENT = 'dataman:chat-new-task';

function smallMenuItemStyle(active?: boolean) {
  return `dashboard-sidebar-compact-item flex items-center justify-center mx-auto rounded w-14 h-14 text-xl hover:bg-blue-50/50 dark:hover:bg-blue-900/10 transition-colors cursor-pointer ${
    active ? 'bg-blue-50 text-blue-600 dark:bg-blue-900/20 dark:text-blue-400 shadow-sm' : ''
  }`;
}

function SidebarPictureIcon({
  src,
  activeSrc,
  active,
  alt,
  size = 32,
}: {
  src: string;
  activeSrc?: string;
  active?: boolean;
  alt: string;
  size?: number;
}) {
  return <Image src={active && activeSrc ? activeSrc : src} alt={alt} width={size} height={size} />;
}

function SideBar() {
  const { isMenuExpand, setIsMenuExpand } = useContext(ChatContext);
  const router = useRouter();
  const { pathname, query } = router;
  const activeDialogueId = Array.isArray(query.id) ? query.id[0] : query.id;
  const isSettingsActive =
    pathname.startsWith('/settings') ||
    pathname.startsWith('/construct/prompt') ||
    pathname.startsWith('/construct/models');
  const { t, i18n } = useTranslation();
  const logo = '/datrix-brand.png';
  const [dialogueList, setDialogueList] = useState<IChatDialogueSchema[]>([]);
  const [loadingDialogues, setLoadingDialogues] = useState(false);
  const optimisticDialoguesRef = useRef<Record<string, IChatDialogueSchema>>({});

  const fetchDialogueList = useCallback(async () => {
    setLoadingDialogues(true);
    try {
      const [, data] = await apiInterceptors(getDialogueList());
      if (data && Array.isArray(data)) {
        const fetched = data.filter(item => item.chat_mode === 'chat_react_agent');
        const fetchedIds = new Set(fetched.map(item => item.conv_uid));
        fetchedIds.forEach(convUid => {
          delete optimisticDialoguesRef.current[convUid];
        });
        const optimistic = Object.values(optimisticDialoguesRef.current).filter(
          item => item.chat_mode === 'chat_react_agent' && !fetchedIds.has(item.conv_uid),
        );
        setDialogueList([...optimistic, ...fetched]);
      }
    } catch (e) {
      console.error('Failed to fetch dialogue list', e);
    } finally {
      setLoadingDialogues(false);
    }
  }, []);

  const upsertDialogue = useCallback((dialogue: IChatDialogueSchema) => {
    if (!dialogue.conv_uid) return;
    optimisticDialoguesRef.current[dialogue.conv_uid] = dialogue;
    setDialogueList(prev => {
      const rest = prev.filter(item => item.conv_uid !== dialogue.conv_uid);
      return [dialogue, ...rest];
    });
  }, []);

  const handleDeleteDialogue = useCallback(async (e: React.MouseEvent, convUid: string) => {
    e.stopPropagation();
    e.preventDefault();
    try {
      const [err] = await apiInterceptors(delDialogue(convUid));
      if (!err) {
        delete optimisticDialoguesRef.current[convUid];
        setDialogueList(prev => prev.filter(d => d.conv_uid !== convUid));
        message.success('已删除');
      }
    } catch (error) {
      console.error('Failed to delete dialogue', error);
    }
  }, []);

  const handleToggleMenu = useCallback(() => {
    setIsMenuExpand(!isMenuExpand);
  }, [isMenuExpand, setIsMenuExpand]);

  const handleNewTask = useCallback(
    (e: React.MouseEvent<HTMLAnchorElement>) => {
      e.preventDefault();
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent(CHAT_NEW_TASK_EVENT));
      }
      void router.push('/');
    },
    [router],
  );

  const functions = useMemo(() => {
    const items: RouteItem[] = [
      {
        key: 'ask-data',
        name: t('scene_catalog'),
        isActive: pathname.startsWith('/ask-data'),
        icon: <BarChartOutlined className='text-[26px]' />,
        path: '/ask-data/capabilities',
      },
      {
        key: 'ontology',
        name: t('ontology') || '全局本体',
        isActive: pathname.startsWith('/ontology'),
        icon: <ApartmentOutlined className='text-[24px]' />,
        path: '/ontology',
      },
      {
        key: 'skills',
        name: t('skills'),
        isActive: pathname.startsWith('/construct/skills'),
        icon: <AppstoreOutlined className='text-[26px]' />,
        path: '/construct/skills',
      },
    ];
    return items;
  }, [t, pathname]);

  useEffect(() => {
    const language = i18n.language;
    if (language === 'zh') moment.locale('zh-cn');
    if (language === 'en') moment.locale('en');
  }, [i18n.language]);

  useEffect(() => {
    fetchDialogueList();
  }, [fetchDialogueList]);

  useEffect(() => {
    const handleUpsert = (event: Event) => {
      const detail = (event as CustomEvent<Partial<IChatDialogueSchema>>).detail;
      if (!detail?.conv_uid) return;
      upsertDialogue({
        conv_uid: detail.conv_uid,
        user_input: detail.user_input || 'New Conversation',
        user_name: detail.user_name || '',
        chat_mode: detail.chat_mode || 'chat_react_agent',
        select_param: detail.select_param || '',
        app_code: detail.app_code || 'chat_react_agent',
        gmt_created: detail.gmt_created,
        gmt_modified: detail.gmt_modified,
      });
    };
    const handleRefresh = () => {
      void fetchDialogueList();
    };
    window.addEventListener(CHAT_DIALOGUE_UPSERT_EVENT, handleUpsert);
    window.addEventListener(CHAT_DIALOGUE_REFRESH_EVENT, handleRefresh);
    return () => {
      window.removeEventListener(CHAT_DIALOGUE_UPSERT_EVENT, handleUpsert);
      window.removeEventListener(CHAT_DIALOGUE_REFRESH_EVENT, handleRefresh);
    };
  }, [fetchDialogueList, upsertDialogue]);

  // ============ COLLAPSED SIDEBAR ============
  if (!isMenuExpand) {
    return (
      <div className='dashboard-sidebar dashboard-sidebar-compact flex h-full flex-col justify-between bg-bar pt-4 dark:bg-[#232734] animate-fade animate-duration-300'>
        <div className='dashboard-sidebar-compact-top'>
          <div className='flex flex-col items-center pb-2'>
            <Link href='/' className='flex justify-center items-center pb-2'>
              <Image src={logo} alt='Datrix' width={28} height={28} />
            </Link>
            <Tooltip title={t('Show_Sidebar') || '展开侧栏'} placement='right'>
              <div
                onClick={handleToggleMenu}
                className='dashboard-sidebar-compact-toggle flex items-center justify-center w-7 h-7 rounded-md text-gray-400 hover:text-gray-600 hover:bg-gray-200 dark:hover:bg-gray-700 dark:hover:text-gray-300 cursor-pointer transition-colors'
              >
                <MenuUnfoldOutlined style={{ fontSize: 14 }} />
              </div>
            </Tooltip>
          </div>
          <div className='dashboard-sidebar-compact-menu flex flex-col gap-4 items-center'>
            <Link href='/' onClick={handleNewTask}>
              <Tooltip title={t('new_task')} placement='right'>
                <div className={smallMenuItemStyle(false)}>
                  <PlusOutlined />
                </div>
              </Tooltip>
            </Link>
            {functions.map(item => (
              <Link key={item.key} className='h-12 flex items-center' href={item.path}>
                <Tooltip title={item.name} placement='right'>
                  <div className={smallMenuItemStyle(item.isActive)}>
                    {item.icon ?? (
                      <SidebarPictureIcon
                        src={item.iconSrc!}
                        activeSrc={item.activeIconSrc}
                        active={item.isActive}
                        alt={`${item.key}_icon`}
                      />
                    )}
                  </div>
                </Tooltip>
              </Link>
            ))}
          </div>
        </div>
        <div className='dashboard-sidebar-compact-bottom py-4'>
          <div className='flex flex-col items-center gap-3'>
            <Link href='/settings'>
              <Tooltip title={t('settings')} placement='right'>
                <div className={smallMenuItemStyle(isSettingsActive)}>
                  <SettingOutlined />
                </div>
              </Tooltip>
            </Link>
            <InterfaceStyleSwitch compact />
          </div>
        </div>
      </div>
    );
  }

  // ============ EXPANDED SIDEBAR ============
  return (
    <div className='dashboard-sidebar dataman-sidebar flex h-full w-[240px] min-w-[240px] flex-col bg-bar px-4 pt-4 dark:bg-[#232734] animate-fade animate-duration-300'>
      {/* LOGO + Collapse Toggle */}
      <div className='flex items-center justify-between p-2 pb-4'>
        <Link href='/' className='sidebar-brand flex items-center'>
          <Image src={logo} alt='Datrix' width={28} height={28} />
          <span className='sidebar-brand-name ml-2 text-lg font-semibold text-gray-900 dark:text-gray-100'>Datrix</span>
        </Link>
        <Tooltip title={t('Close_Sidebar') || '收起侧栏'}>
          <div
            onClick={handleToggleMenu}
            className='flex items-center justify-center w-7 h-7 rounded-md text-gray-400 hover:text-gray-600 hover:bg-gray-200 dark:hover:bg-gray-700 dark:hover:text-gray-300 cursor-pointer transition-colors'
          >
            <MenuFoldOutlined style={{ fontSize: 14 }} />
          </div>
        </Tooltip>
      </div>

      {/* New Task Button */}
      <Link href='/' onClick={handleNewTask}>
        <div className='industrial-new-task flex items-center justify-center gap-2 px-4 py-2.5 mb-4 bg-black dark:bg-white dark:text-black text-white rounded-xl text-sm font-medium hover:opacity-90 transition-opacity cursor-pointer'>
          <PlusOutlined className='text-xs' />
          <span>{t('new_task')}</span>
        </div>
      </Link>

      {/* Functions */}
      <div className='flex flex-col gap-1'>
        {functions.map(item => (
          <Link
            href={item.path}
            className={cls(
              'industrial-nav-item flex items-center w-full h-12 px-4 cursor-pointer hover:bg-blue-50/50 dark:hover:bg-blue-900/10 hover:rounded-xl',
              {
                'industrial-nav-item-active bg-blue-50 rounded-xl text-blue-600 dark:bg-blue-900/20 dark:text-blue-400':
                  item.isActive,
              },
            )}
            key={item.key}
          >
            <div className='mr-3 flex h-8 w-8 items-center justify-center'>
              {item.icon ?? (
                <SidebarPictureIcon
                  src={item.iconSrc!}
                  activeSrc={item.activeIconSrc}
                  active={item.isActive}
                  alt={`${item.key}_icon`}
                />
              )}
            </div>
            <span className='text-sm leading-5'>{item.name}</span>
          </Link>
        ))}
      </div>

      {/* All Tasks Section */}
      <div className='mt-3 mb-2 px-1'>
        <div className='flex items-center justify-start gap-2'>
          <span className='sidebar-section-label text-sm font-normal leading-5 text-gray-400'>{t('all_tasks')}</span>
          <Link href='/conversations' className='inline-flex items-center'>
            <Tooltip title={t('view_all')}>
              <RightOutlined className='text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 cursor-pointer transition-colors text-xs leading-none' />
            </Tooltip>
          </Link>
        </div>
      </div>
      <div className='flex-1 overflow-y-auto min-h-0'>
        {loadingDialogues ? (
          <div className='px-2 pt-2'>
            <Skeleton active title={false} paragraph={{ rows: 4, width: '100%' }} />
          </div>
        ) : dialogueList.length > 0 ? (
          <div className='space-y-1'>
            {dialogueList.map(conv => (
              <Link
                key={conv.conv_uid}
                href={`/?id=${conv.conv_uid}`}
                className={cls(
                  'flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer text-sm transition-colors group hover:bg-[#F1F5F9] dark:hover:bg-theme-dark',
                  {
                    'bg-[#F1F5F9] dark:bg-theme-dark': activeDialogueId === conv.conv_uid,
                  },
                )}
              >
                <MessageOutlined className='text-gray-400 flex-shrink-0 text-xs' />
                <div className='flex-1 min-w-0'>
                  <div className='font-normal truncate leading-5 text-gray-700 dark:text-gray-300'>
                    {typeof conv.user_input === 'string'
                      ? conv.user_input.slice(0, 40) || 'New Conversation'
                      : 'New Conversation'}
                  </div>
                </div>
                <Tooltip title='删除'>
                  <DeleteOutlined
                    onClick={e => handleDeleteDialogue(e, conv.conv_uid)}
                    className='text-gray-300 hover:text-red-500 opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0 mt-1'
                  />
                </Tooltip>
              </Link>
            ))}
          </div>
        ) : (
          <div className='px-3 py-8 text-center'>
            <div className='text-gray-300 dark:text-gray-600 mb-2'>
              <MessageOutlined style={{ fontSize: 24 }} />
            </div>
            <p className='text-xs text-gray-400'>{t('no_tasks')}</p>
          </div>
        )}
      </div>

      {/* Bottom controls */}
      <div className='pt-4 pb-2'>
        <div className='dashboard-sidebar-bottom-controls flex items-center justify-center gap-4 py-4 border-t border-dashed border-gray-200 dark:border-gray-700'>
          <div className='dashboard-sidebar-bottom-control'>
            <InterfaceStyleSwitch compact />
          </div>
          <Link href='/settings' className='dashboard-sidebar-bottom-control'>
            <Tooltip title={t('settings')}>
              <div
                className={cls('cursor-pointer text-xl', {
                  'text-blue-600 dark:text-blue-400': isSettingsActive,
                })}
              >
                <SettingOutlined />
              </div>
            </Tooltip>
          </Link>
        </div>
      </div>
    </div>
  );
}

export default SideBar;
