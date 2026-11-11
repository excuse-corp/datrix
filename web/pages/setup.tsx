import { Button, Card, Form, Input, Typography, message } from 'antd';
import { useRouter } from 'next/router';
import { useEffect, useState } from 'react';

type SetupValues = {
  token: string;
  username: string;
  password: string;
  display_name?: string;
};

export default function SetupPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    fetch('/api/v1/auth/setup/status')
      .then(response => response.json())
      .then(status => {
        if (status.initialized) router.replace('/login');
      })
      .catch(() => undefined);
  }, [router]);

  const initialize = async (values: SetupValues) => {
    setLoading(true);
    try {
      const response = await fetch('/api/v1/auth/setup', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(values),
      });
      const body = await response.json();
      if (!response.ok) {
        const errors: Record<string, string> = {
          AUTH_SETUP_TOKEN_INVALID: '初始化令牌无效，请确认从当前服务器读取了令牌。',
          AUTH_SETUP_ALREADY_COMPLETED: '系统已完成初始化，请前往登录。',
          AUTH_CROSS_ORIGIN_REQUEST: '请求来源无效，请从当前站点打开初始化页面。',
        };
        throw new Error(errors[body.detail] || body.detail || '初始化失败');
      }
      message.success('管理员创建成功');
      await router.replace('/');
    } catch (error) {
      message.error(error instanceof Error ? error.message : '初始化失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className='flex min-h-screen items-center justify-center bg-[#f7f9fc] px-4 dark:bg-[#111827]'>
      <Card className='w-full max-w-md shadow-sm'>
        <Typography.Title level={3}>首次启动设置</Typography.Title>
        <Typography.Paragraph type='secondary'>
          请从服务器上的 `dataman_setup_token` 文件取得一次性初始化令牌，然后创建首位平台管理员。
        </Typography.Paragraph>
        <Form layout='vertical' onFinish={initialize}>
          <Form.Item label='初始化令牌' name='token' rules={[{ required: true, message: '请输入初始化令牌' }]}>
            <Input.Password autoComplete='off' />
          </Form.Item>
          <Form.Item label='管理员用户名' name='username' rules={[{ required: true, message: '请输入用户名' }]}>
            <Input autoComplete='username' />
          </Form.Item>
          <Form.Item
            label='管理员密码'
            name='password'
            rules={[{ required: true, min: 12, message: '密码至少 12 个字符' }]}
          >
            <Input.Password autoComplete='new-password' />
          </Form.Item>
          <Form.Item label='显示名称' name='display_name'>
            <Input />
          </Form.Item>
          <Button block type='primary' htmlType='submit' loading={loading}>
            创建管理员并登录
          </Button>
        </Form>
      </Card>
    </main>
  );
}
