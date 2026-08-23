import { useState } from 'react'
import { Link, NavLink, Outlet, useNavigate } from 'react-router-dom'
import { Button } from '@/components/ui'
import { useAuth } from '@/hooks/useAuth'

const NAV = [
  ['/', 'Dashboard'],
  ['/screenings', 'Screening'],
  ['/reports', 'Reports'],
  ['/history', 'History'],
  ['/settings', 'Settings'],
] as const

export default function AppShell() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const [menuOpen, setMenuOpen] = useState(false)
  const initials = user?.full_name?.split(' ').map((w) => w[0]).join('') ?? '—'

  return (
    <div className="shell">
      <header className="nav">
        <div className="nav-in">
          <button className="icon-btn burger" onClick={() => setMenuOpen(!menuOpen)} aria-label="Menu">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
              <path d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>
          <Link to="/" className="brand">
            <div className="brand-mark">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 8V5a2 2 0 0 1 2-2h3M16 3h3a2 2 0 0 1 2 2v3M21 16v3a2 2 0 0 1-2 2h-3M8 21H5a2 2 0 0 1-2-2v-3" />
                <path d="M8 12h8M8 9h5M8 15h6" />
              </svg>
            </div>
            <span className="brand-name">ResumeIQ</span>
          </Link>
          <nav className="nav-links" aria-label="Main">
            {NAV.map(([to, label]) => (
              <NavLink key={to} to={to} end={to === '/'}
                className={({ isActive }) => `nav-link ${isActive ? 'on' : ''}`}>
                {label}
              </NavLink>
            ))}
          </nav>
          <div className="nav-right">
            <Button variant="pri" size="sm" onClick={() => navigate('/screenings/new')}>
              New screening
            </Button>
            <button className="who" onClick={logout} title="Sign out">
              <span className="who-txt">
                <span className="who-name">{user?.full_name}</span><br />
                <span className="who-role">{user?.job_title ?? user?.role}</span>
              </span>
              <span className="av">{initials}</span>
            </button>
          </div>
        </div>
      </header>

      {menuOpen && (
        <>
          <div className="drawer-scrim" onClick={() => setMenuOpen(false)} />
          <div className="drawer" style={{ left: 0, right: 'auto' }}>
            <div className="sec-head"><h3>Menu</h3></div>
            <div className="drawer-body">
              <div className="set-nav">
                {NAV.map(([to, label]) => (
                  <NavLink key={to} to={to} end={to === '/'} onClick={() => setMenuOpen(false)}
                    className={({ isActive }) => `set-link ${isActive ? 'on' : ''}`}>
                    {label}
                  </NavLink>
                ))}
              </div>
            </div>
          </div>
        </>
      )}

      <main className="main"><Outlet /></main>
    </div>
  )
}
