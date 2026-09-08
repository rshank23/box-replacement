import { useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';

import { useDashboardStats } from '@/api/hooks';
import { useAuth } from '@/auth/AuthContext';

interface NavItem {
  to: string;
  label: string;
  icon: string;
  badge?: number;
}

const ENVIRONMENT = import.meta.env.VITE_ENVIRONMENT_LABEL ?? 'Local development';

export function AppShell() {
  const [collapsed, setCollapsed] = useState(false);
  const { subject, roles, isAdmin, openAccess, signOut } = useAuth();
  const { data: stats } = useDashboardStats();
  const location = useLocation();

  const items: NavItem[] = [
    { to: '/dashboard', label: 'Dashboard', icon: '▤' },
    { to: '/poc', label: 'Migration POC', icon: '⇉' },
    { to: '/upload', label: 'Upload', icon: '⬆' },
    { to: '/mapping', label: 'Mapping Review', icon: '⇄' },
    { to: '/unclassified', label: 'Unclassified Docs', icon: '⚠', badge: stats?.open_failures },
    { to: '/audit', label: 'Audit Trail', icon: '☰' },
    { to: '/notifications', label: 'Notifications', icon: '🔔', badge: stats?.open_sam_items },
    { to: '/settings', label: 'Settings', icon: '⚙' },
  ];

  const current = items.find((item) => location.pathname.startsWith(item.to));

  return (
    <div className="app-shell" data-collapsed={collapsed}>
      <div className="brand">
        <button
          type="button"
          className="icon-btn"
          style={{ color: '#fff' }}
          onClick={() => setCollapsed((value) => !value)}
          aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
        >
          ☰
        </button>
        <span className="brand-text">Migration Utility</span>
      </div>

      <header className="app-header">
        <div className="page-title">{current?.label ?? 'Document Migration Utility'}</div>
        <div className="header-right">
          <span className="env-chip">{ENVIRONMENT}</span>
          {openAccess ? <span className="badge" data-tone="warn">Auth disabled on API</span> : null}
          <span className="muted">
            Welcome, <strong>{subject || 'operator'}</strong>
            {isAdmin ? ' · Admin' : roles.length ? ` · ${roles.join(', ')}` : ''}
          </span>
          <button type="button" className="btn btn-sm" onClick={signOut}>
            Sign out
          </button>
        </div>
      </header>

      <aside className="sidebar">
        <nav aria-label="Primary">
          {items.map((item) => (
            <NavLink key={item.to} to={item.to} title={item.label}>
              <span className="nav-icon" aria-hidden>
                {item.icon}
              </span>
              <span className="nav-label">{item.label}</span>
              {item.badge ? <span className="nav-badge">{item.badge}</span> : null}
            </NavLink>
          ))}
        </nav>
      </aside>

      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
