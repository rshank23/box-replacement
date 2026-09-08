import { Navigate, Route, Routes, useLocation } from 'react-router-dom';

import { AppShell } from '@/components/AppShell';
import { useAuth } from '@/auth/AuthContext';
import { AuditTrailPage } from '@/pages/AuditTrailPage';
import { DashboardPage } from '@/pages/DashboardPage';
import { LoginPage } from '@/pages/LoginPage';
import { MappingReviewPage } from '@/pages/MappingReviewPage';
import { NotificationsPage } from '@/pages/NotificationsPage';
import { PocPage } from '@/pages/PocPage';
import { SettingsPage } from '@/pages/SettingsPage';
import { UnclassifiedPage } from '@/pages/UnclassifiedPage';
import { UploadPage } from '@/pages/UploadPage';

function RequireAuth({ children }: { children: JSX.Element }) {
  const { isAuthenticated } = useAuth();
  const location = useLocation();
  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return children;
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        }
      >
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/poc" element={<PocPage />} />
        <Route path="/upload" element={<UploadPage />} />
        <Route path="/mapping" element={<MappingReviewPage />} />
        <Route path="/mapping/:correlationId" element={<MappingReviewPage />} />
        <Route path="/unclassified" element={<UnclassifiedPage />} />
        <Route path="/audit" element={<AuditTrailPage />} />
        <Route path="/notifications" element={<NotificationsPage />} />
        <Route path="/settings" element={<SettingsPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}
