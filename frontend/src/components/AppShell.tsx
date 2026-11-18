import { Link, NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/useAuth'

export function AppShell() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  function signOut() {
    logout()
    navigate('/login')
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <Link className="brand" to="/dashboard" aria-label="Papertrail home">
          <span className="brand-mark">P</span>
          <span>papertrail</span>
        </Link>
        <nav className="topnav" aria-label="Main navigation">
          <NavLink to="/dashboard">Documents</NavLink>
          <span className="user-chip">{user?.name}</span>
          <button className="button button-quiet button-small" onClick={signOut}>
            Sign out
          </button>
        </nav>
      </header>
      <main className="page-content">
        <Outlet />
      </main>
      <footer className="site-footer">
        <span>Papertrail</span>
        <span>Simple, thoughtful document signing</span>
      </footer>
    </div>
  )
}
