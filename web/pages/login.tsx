import { LockOutlined, UserOutlined } from '@ant-design/icons';
import { Button, Form, Input, message } from 'antd';
import { useRouter } from 'next/router';
import { useEffect, useState } from 'react';
import './login.css';

export default function LoginPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    fetch('/api/v1/auth/setup/status')
      .then(response => response.json())
      .then(status => {
        if (!status.initialized) {
          router.replace('/setup');
        }
      })
      .catch(() => undefined);
  }, [router]);

  const loginLocal = async (values: { username: string; password: string }) => {
    setLoading(true);
    try {
      const response = await fetch('/api/v1/auth/local/login', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(values),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || '登录失败');
      await router.replace(typeof router.query.return_to === 'string' ? router.query.return_to : '/');
    } catch (error) {
      message.error(error instanceof Error ? error.message : '登录失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className='dataman-login-page'>
      <aside className='dataman-login-visual'>
        <div className='dataman-login-visual-art' aria-hidden>
          <img src='/login-illustration.png' alt='' />
        </div>
        <h1 className='dataman-login-visual-title'>
          让数据自己说话，
          <br />
          由本体与多智能体驱动
        </h1>
      </aside>

      <section className='dataman-login-panel'>
        <div className='dataman-login-body'>
          <div className='dataman-login-logo'>
            <img src='/datrix-brand.png' alt='Datrix' width={48} height={48} />
          </div>
          <h2 className='dataman-login-title'>登录 Datrix</h2>
          <p className='dataman-login-subtitle'>数据查询与分析平台</p>

          <Form layout='vertical' onFinish={loginLocal} requiredMark={false} className='dataman-login-form'>
            <Form.Item
              name='username'
              rules={[{ required: true, message: '请输入用户名' }]}
              style={{ marginBottom: 16 }}
            >
              <Input
                size='large'
                prefix={<UserOutlined style={{ color: '#9bb8ae' }} />}
                placeholder='用户名'
                autoComplete='username'
              />
            </Form.Item>
            <Form.Item name='password' rules={[{ required: true, message: '请输入密码' }]} style={{ marginBottom: 20 }}>
              <Input.Password
                size='large'
                prefix={<LockOutlined style={{ color: '#9bb8ae' }} />}
                placeholder='密码'
                autoComplete='current-password'
              />
            </Form.Item>
            <Button block type='primary' htmlType='submit' loading={loading} className='dataman-login-submit'>
              登录
            </Button>
          </Form>
        </div>

        <div className='dataman-login-footer'>© 2026 Datrix · Data intelligence through coordinated agents</div>
      </section>
    </main>
  );
}
