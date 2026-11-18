import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { useAuth } from './auth/useAuth'
import { AppShell } from './components/AppShell'
import { Login } from './pages/Login'
import { Register } from './pages/Register'
import { PrepareDocument } from './pages/PrepareDocument'
import { Dashboard } from './pages/Dashboard'
import { GuestSignView } from './pages/GuestSignView'

function ProtectedRoutes() {
  const { user, loading } = useAuth()
  const location = useLocation()
  if (loading) return <div className="loading-state centered"><span className="spinner" /> Loading your workspace…</div>
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />
  return <AppShell />
}

export default function App() {
  const { user, loading } = useAuth()
  if (loading) return <div className="loading-state centered"><span className="spinner" /> Loading Papertrail…</div>

  return (
    <>
      <Routes>
        <Route path="/sign" element={<GuestSignView />} />
        <Route path="/login" element={user ? <Navigate to="/dashboard" replace /> : <Login />} />
        <Route path="/register" element={user ? <Navigate to="/dashboard" replace /> : <Register />} />
        <Route element={<ProtectedRoutes />}>
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/docs/:id/prepare" element={<PrepareDocument />} />
        </Route>
        <Route path="*" element={<Navigate to={user ? '/dashboard' : '/login'} replace />} />
      </Routes>
    </>
  )
}
