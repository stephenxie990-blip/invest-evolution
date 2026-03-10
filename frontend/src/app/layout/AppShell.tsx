import { NavLink, Outlet } from 'react-router-dom'

const navItems = [
  { to: '/dashboard', label: '仪表盘', icon: '📊', testId: 'nav-dashboard' },
  { to: '/training-lab', label: '训练中心', icon: '🧬', testId: 'nav-training-lab' },
  { to: '/models', label: '模型策略', icon: '🧠', testId: 'nav-models' },
  { to: '/data', label: '数据控制台', icon: '🗄️', testId: 'nav-data' },
  { to: '/settings', label: '配置中心', icon: '⚙️', testId: 'nav-settings' },
]

export function AppShell() {
  return (
    <div className="app-shell" data-testid="app-shell">
      <aside className="app-shell__sidebar">
        <div className="app-shell__brand">
          <h1>投资进化系统</h1>
          <p>Standalone Frontend Workspace</p>
        </div>
        <nav className="app-shell__nav">
          {navItems.map((item) => (
            <NavLink
              className={({ isActive }) => `nav-link${isActive ? ' nav-link--active' : ''}`}
              data-testid={item.testId}
              key={item.to}
              to={item.to}
            >
              <span>{item.icon}</span>
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="app-shell__sidebar-footer">
          <span className="app-shell__status-dot" />
          <span>契约驱动 /app 新前端</span>
        </div>
      </aside>
      <main className="app-shell__main">
        <header className="app-shell__header">
          <div>
            <h2>前端升级工作台</h2>
            <p>独立消费 `/api/*` 与 `/api/events`，不依赖 Flask 内嵌页面逻辑。</p>
          </div>
        </header>
        <Outlet />
      </main>
    </div>
  )
}
