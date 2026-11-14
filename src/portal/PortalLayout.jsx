import React, { useState } from 'react';
import { NavLink, useLocation, useNavigate } from 'react-router-dom';
import { AnimatePresence, motion } from 'framer-motion';
import { Activity, ChevronRight, LogOut, Menu, Moon, PanelLeftClose, PanelLeftOpen, Sun, X } from 'lucide-react';
import { applyTheme, useAuth } from './AuthContext';

const initials = name => (name || '?').replace(/^Dr\.?\s*/i, '').split(/\s+/).filter(Boolean).slice(0, 2).map(p => p[0]).join('').toUpperCase();

export default function PortalLayout({ nav, area, children }) {
  const { user, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [theme, setTheme] = useState(() => document.documentElement.dataset.theme || 'light');
  const current = nav.flatMap(g => g.items).find(i => i.end ? location.pathname === i.to : location.pathname.startsWith(i.to));
  const toggleTheme = () => { const t = theme === 'light' ? 'dark' : 'light'; setTheme(t); applyTheme(t); };
  const signOut = async () => { await logout(); navigate('/login', { replace: true, state: { reason: 'signed_out' } }); };
  return <div className="app-shell">
    <aside className={`sidebar ${collapsed ? 'collapsed' : ''} ${mobileOpen ? 'mobile-open' : ''}`}>
      <div className="brand"><div className="brand-mark"><Activity size={18} /></div>{!collapsed && <div><b>MedAudit<span> AI</span></b><small>{area.toUpperCase()}</small></div>}
        <button className="icon-button collapse-toggle" onClick={() => setCollapsed(!collapsed)} aria-label="Toggle sidebar">{collapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}</button></div>
      <nav className="side-nav" aria-label={`${area} navigation`}>{nav.map(group => <div className="nav-group" key={group.label}><span className="nav-label">{group.label}</span>
        {group.items.map(({ to, label, icon: Icon, end }) => <NavLink key={to} to={to} end={end} title={collapsed ? label : ''}
          className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`} onClick={() => setMobileOpen(false)}>
          {({ isActive }) => <><span className="nav-symbol"><Icon size={15} /></span><span>{label}</span>{isActive && <i />}</>}</NavLink>)}</div>)}</nav>
      <div className="sidebar-bottom">
        <div className="user-card static"><div className="avatar">{initials(user.display_name)}</div><div><b>{user.display_name}</b><small>{user.login_id || user.email} · {user.role === 'admin' ? 'Administrator' : user.designation || 'Doctor'}</small></div></div>
        <button className="button signout" onClick={signOut}><LogOut size={14} /> {!collapsed && 'Sign out'}</button>
      </div>
    </aside>
    <main className="main-area">
      <header className="topbar">
        <button className="icon-button mobile-menu" onClick={() => setMobileOpen(!mobileOpen)} aria-label="Open navigation">{mobileOpen ? <X /> : <Menu />}</button>
        <div className="breadcrumbs"><span>{area}</span><ChevronRight size={14} /><b>{current?.label || 'Overview'}</b></div>
        <div className="header-actions">
          <button className="icon-button" aria-label="Toggle theme" onClick={toggleTheme}>{theme === 'light' ? <Moon size={18} /> : <Sun size={18} />}</button>
          <div className="header-user"><div className="avatar small">{initials(user.display_name)}</div></div>
        </div>
      </header>
      <div className="page-content"><AnimatePresence mode="wait"><motion.div key={location.pathname} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -5 }} transition={{ duration: .18 }}>{children}</motion.div></AnimatePresence></div>
      <footer className="app-footer"><span>MedAudit AI <b>2.0</b> · {area}</span><span><span className="status-dot" /> Signed in as {user.login_id || user.email} · Activity is audited</span></footer>
    </main>
    {mobileOpen && <button className="mobile-scrim" onClick={() => setMobileOpen(false)} aria-label="Close navigation" />}
  </div>;
}
