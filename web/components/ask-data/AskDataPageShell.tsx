import type { ReactNode } from 'react';

type AskDataPageShellProps = {
  title: ReactNode;
  description: string;
  actions?: ReactNode;
  children: ReactNode;
  maxWidth?: 'max-w-6xl' | 'max-w-7xl' | 'max-w-5xl';
};

export default function AskDataPageShell({
  title,
  description,
  actions,
  children,
  maxWidth = 'max-w-7xl',
}: AskDataPageShellProps) {
  return (
    <main className='ask-data-page-scroll h-full min-h-0 flex-1 overflow-y-auto bg-[#f7f7f9] px-4 py-6 pb-24 text-[#1a1b1e] dark:bg-[#0f1012] dark:text-gray-100 md:px-6'>
      <div className={`mx-auto flex ${maxWidth} flex-col gap-5`}>
        <header className='flex flex-wrap items-center justify-between gap-4'>
          <div className='min-w-0'>
            <h1 className='m-0 text-[16px] font-medium leading-5 text-gray-900 dark:text-gray-100'>{title}</h1>
            <p className='mt-1 mb-0 text-sm text-gray-500 dark:text-gray-400'>{description}</p>
          </div>
          {actions && <div className='flex shrink-0 items-center gap-2'>{actions}</div>}
        </header>
        {children}
      </div>
    </main>
  );
}
