import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { AppStateProvider } from '@/state/AppState'
import { AssistantStateProvider } from '@/state/AssistantState'
import { AuthProvider } from '@/state/AuthState'
import { AuthGate } from '@/components/auth/AuthGate'
import { Shell } from '@/components/layout/Shell'
import LoginPage from '@/pages/Login'
import OverviewPage from '@/pages/Overview'
import HealthPage from '@/pages/Health'
import ChangesPage from '@/pages/Changes'
import EnvironmentPage from '@/pages/Environment'
import EventsPage from '@/pages/Events'
import InventoryPage from '@/pages/Inventory'
import SnapshotsPage from '@/pages/Snapshots'
import AssistantPage from '@/pages/Assistant'
import ConnectionsPage from '@/pages/Connections'
import SettingsPage from '@/pages/Settings'

// The console (and its data providers, which start polling on mount) only
// mounts once the auth gate says we are allowed in.
function Console() {
  return (
    <AuthGate>
      <AppStateProvider>
        <AssistantStateProvider>
          <Routes>
            <Route element={<Shell />}>
              <Route index element={<OverviewPage />} />
              <Route path="health" element={<HealthPage />} />
              <Route path="changes" element={<ChangesPage />} />
              <Route path="environment" element={<EnvironmentPage />} />
              <Route path="events" element={<EventsPage />} />
              <Route path="inventory" element={<InventoryPage />} />
              <Route path="snapshots" element={<SnapshotsPage />} />
              <Route path="assistant" element={<AssistantPage />} />
              <Route path="connections" element={<ConnectionsPage />} />
              <Route path="settings" element={<SettingsPage />} />
              <Route path="*" element={<OverviewPage />} />
            </Route>
          </Routes>
        </AssistantStateProvider>
      </AppStateProvider>
    </AuthGate>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/*" element={<Console />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}
