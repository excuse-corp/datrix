import { Alert, Button, Card, Form, Input, Modal, Select, Space, Switch, Table, Tag, Typography, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useCallback, useEffect, useState } from 'react';

type ManagedUser = {
  user_id: string;
  username: string;
  display_name: string;
  roles: string[];
  scene_ids: string[];
  is_active: boolean;
  auth_methods: string[];
  created_at: string;
};

type UserPage = { items: ManagedUser[]; total: number; page: number; page_size: number };
type DialogMode = 'create' | 'edit' | 'reset' | null;

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof result.detail === 'string' ? result.detail : '请求失败';
    throw new Error(detail);
  }
  return result as T;
}

export default function UserManagementPage() {
  const [form] = Form.useForm();
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [loading, setLoading] = useState(true);
  const [authorized, setAuthorized] = useState<boolean | null>(null);
  const [dialog, setDialog] = useState<DialogMode>(null);
  const [selectedUser, setSelectedUser] = useState<ManagedUser | null>(null);
  const [saving, setSaving] = useState(false);

  const loadUsers = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(page), page_size: '20', search });
      const result = await apiRequest<UserPage>(`/api/v1/admin/users?${params}`);
      setUsers(result.items);
      setTotal(result.total);
      setAuthorized(true);
    } catch (error) {
      if (error instanceof Error && error.message === 'AUTH_PERMISSION_DENIED') setAuthorized(false);
      else message.error(error instanceof Error ? error.message : '加载用户失败');
    } finally {
      setLoading(false);
    }
  }, [page, search]);

  useEffect(() => {
    loadUsers();
  }, [loadUsers]);

  const openDialog = (mode: Exclude<DialogMode, null>, user?: ManagedUser) => {
    setSelectedUser(user ?? null);
    setDialog(mode);
    if (mode === 'edit' && user) {
      form.setFieldsValue({
        display_name: user.display_name,
        roles: user.roles,
        scene_ids: user.scene_ids,
        is_active: user.is_active,
      });
    } else {
      form.resetFields();
      if (mode === 'create') form.setFieldsValue({ roles: ['normal'], scene_ids: [], is_active: true });
    }
  };

  const closeDialog = () => {
    setDialog(null);
    setSelectedUser(null);
    form.resetFields();
  };

  const submitDialog = async (values: Record<string, unknown>) => {
    if (!dialog) return;
    setSaving(true);
    try {
      if (dialog === 'create') {
        await apiRequest('/api/v1/admin/users', { method: 'POST', body: JSON.stringify(values) });
        message.success('用户已创建');
      } else if (dialog === 'edit' && selectedUser) {
        await apiRequest(`/api/v1/admin/users/${selectedUser.user_id}`, {
          method: 'PATCH',
          body: JSON.stringify(values),
        });
        message.success('用户权限已更新，用户需要重新登录');
      } else if (dialog === 'reset' && selectedUser) {
        await apiRequest(`/api/v1/admin/users/${selectedUser.user_id}/password-reset`, {
          method: 'POST',
          body: JSON.stringify({ password: values.password }),
        });
        message.success('密码已重置，原有会话已撤销');
      }
      closeDialog();
      await loadUsers();
    } catch (error) {
      message.error(error instanceof Error ? error.message : '操作失败');
    } finally {
      setSaving(false);
    }
  };

  const toggleActive = async (user: ManagedUser, isActive: boolean) => {
    try {
      await apiRequest(`/api/v1/admin/users/${user.user_id}`, {
        method: 'PATCH',
        body: JSON.stringify({ is_active: isActive }),
      });
      message.success(isActive ? '用户已启用' : '用户已停用');
      await loadUsers();
    } catch (error) {
      message.error(error instanceof Error ? error.message : '操作失败');
    }
  };

  const columns: ColumnsType<ManagedUser> = [
    { title: '用户名', dataIndex: 'username', key: 'username' },
    { title: '显示名称', dataIndex: 'display_name', key: 'display_name' },
    {
      title: '角色',
      dataIndex: 'roles',
      key: 'roles',
      render: (roles: string[]) => roles.map(role => <Tag key={role}>{role}</Tag>),
    },
    {
      title: '问数场景',
      dataIndex: 'scene_ids',
      key: 'scene_ids',
      render: (sceneIds: string[]) => (sceneIds.length ? sceneIds.join('、') : '未分配'),
    },
    {
      title: '登录方式',
      dataIndex: 'auth_methods',
      key: 'auth_methods',
      render: (methods: string[]) => methods.join('、') || '—',
    },
    {
      title: '状态',
      dataIndex: 'is_active',
      key: 'is_active',
      render: (active: boolean) => <Tag color={active ? 'green' : 'default'}>{active ? '启用' : '停用'}</Tag>,
    },
    {
      title: '操作',
      key: 'actions',
      render: (_, user) => (
        <Space wrap>
          <Button type='link' onClick={() => openDialog('edit', user)}>
            编辑权限
          </Button>
          {user.auth_methods.includes('local') && (
            <Button type='link' onClick={() => openDialog('reset', user)}>
              重置密码
            </Button>
          )}
          <Button type='link' onClick={() => toggleActive(user, !user.is_active)}>
            {user.is_active ? '停用' : '启用'}
          </Button>
        </Space>
      ),
    },
  ];

  const dialogTitle = dialog === 'create' ? '创建用户' : dialog === 'edit' ? '编辑用户权限' : '重置本地密码';

  return (
    <main className='dashboard-main-canvas h-full overflow-auto bg-[#f7f9fc] px-4 py-6 dark:bg-[#111827] md:px-6'>
      <div className='mx-auto max-w-7xl'>
        <div className='mb-5 flex items-center justify-between gap-3'>
          <div>
            <Typography.Title level={2} className='!mb-1'>
              用户管理
            </Typography.Title>
            <Typography.Text type='secondary'>管理 DataMan 账号、角色和问数场景权限</Typography.Text>
          </div>
          <Button type='primary' onClick={() => openDialog('create')}>
            创建用户
          </Button>
        </div>
        {authorized === false ? (
          <Alert type='error' showIcon message='只有平台管理员可以管理用户。' />
        ) : (
          <Card>
            <div className='mb-4 flex max-w-lg gap-2'>
              <Input
                allowClear
                placeholder='搜索用户名或显示名称'
                value={searchInput}
                onChange={event => setSearchInput(event.target.value)}
                onPressEnter={() => {
                  setPage(1);
                  setSearch(searchInput.trim());
                }}
              />
              <Button
                onClick={() => {
                  setPage(1);
                  setSearch(searchInput.trim());
                }}
              >
                搜索
              </Button>
            </div>
            <Table
              rowKey='user_id'
              columns={columns}
              dataSource={users}
              loading={loading}
              pagination={{
                current: page,
                pageSize: 20,
                total,
                onChange: setPage,
                showTotal: count => `共 ${count} 个用户`,
              }}
              scroll={{ x: 980 }}
            />
          </Card>
        )}
      </div>

      <Modal
        title={dialogTitle}
        open={dialog !== null}
        onCancel={closeDialog}
        onOk={() => form.submit()}
        confirmLoading={saving}
        destroyOnClose
      >
        <Form form={form} layout='vertical' onFinish={submitDialog} preserve={false}>
          {dialog === 'create' && (
            <>
              <Form.Item label='用户名' name='username' rules={[{ required: true, whitespace: true }]}>
                <Input maxLength={128} autoComplete='off' />
              </Form.Item>
              <Form.Item label='初始密码（至少 12 位）' name='password' rules={[{ required: true, min: 12 }]}>
                <Input.Password autoComplete='new-password' />
              </Form.Item>
            </>
          )}
          {dialog !== 'reset' && (
            <>
              <Form.Item label='显示名称' name='display_name'>
                <Input maxLength={256} />
              </Form.Item>
              <Form.Item label='角色' name='roles' rules={[{ required: true, type: 'array', min: 1 }]}>
                <Select
                  mode='multiple'
                  options={[
                    { value: 'normal', label: '普通用户' },
                    { value: 'ask_data_admin', label: '问数管理员' },
                    { value: 'admin', label: '平台管理员' },
                  ]}
                />
              </Form.Item>
              <Form.Item
                label='问数场景 ID'
                name='scene_ids'
                extra='输入场景 ID 后按回车添加；平台管理员可访问所有场景。'
              >
                <Select mode='tags' tokenSeparators={[',', '，']} />
              </Form.Item>
              {dialog === 'edit' && (
                <Form.Item label='账号启用' name='is_active' valuePropName='checked'>
                  <Switch />
                </Form.Item>
              )}
            </>
          )}
          {dialog === 'reset' && (
            <>
              <Alert
                className='mb-4'
                type='warning'
                showIcon
                message='只重置 DataMan 本地密码；CAS 账号密码需由身份认证管理员管理。'
              />
              <Form.Item label='新密码（至少 12 位）' name='password' rules={[{ required: true, min: 12 }]}>
                <Input.Password autoComplete='new-password' />
              </Form.Item>
            </>
          )}
        </Form>
      </Modal>
    </main>
  );
}
