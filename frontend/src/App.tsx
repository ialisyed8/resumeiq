import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import AppShell from '@/layouts/AppShell'
import { Card, SkeletonRows } from '@/components/ui'
import { useAuth } from '@/hooks/useAuth'

// Routes are lazily loaded so the initial bundle stays small.
const Login = lazy(() => import('@/pages/Login'))
const Dashboard = lazy(() => import('@/pages/Dashboard'))
const NewScreening = lazy(() => import('@/pages/NewScreening'))
const Results = lazy(() => import('@/pages/Results'))
const CandidateDetail = lazy(() => import('@/pages/CandidateDetail'))
const Compare = lazy(() => import('@/pages/Compare'))
const History = lazy(() => import('@/pages/History'))
const Reports = lazy(() => import('@/pages/Reports'))
const Settings = lazy(() => import('@/pages/Settings'))
const Register = lazy(() => import('@/pages/Register'))
const ForgotPassword = lazy(() => import('@/pages/ForgotPassword'))

function Loading() {
  return <div className="wrap"><Card><SkeletonRows count={6} /></Card></div>
}

function Protected({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth()
  if (loading) return <Loading />
  if (!user) return <Navigate to="/login" replace />
  return <>{children}</>
}

export default function App() {
  return (
    <Suspense fallback={<Loading />}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
        <Route path="/forgot-password" element={<ForgotPassword />} />
        <Route element={<Protected><AppShell /></Protected>}>
          <Route index element={<Dashboard />} />
          <Route path="screenings/new" element={<NewScreening />} />
          <Route path="screenings/:batchId" element={<Results />} />
          <Route path="screenings/:batchId/candidates/:candidateId" element={<CandidateDetail />} />
          <Route path="screenings/:batchId/compare" element={<Compare />} />
          <Route path="screenings" element={<History />} />
          <Route path="history" element={<History />} />
          <Route path="reports" element={<Reports />} />
          <Route path="settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </Suspense>
  )
}
