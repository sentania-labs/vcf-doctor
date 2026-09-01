import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { AppStateProvider } from '@/state/AppState'
import { AssistantStateProvider } from '@/state/AssistantState'
import { Shell } from '@/components/layout/Shell'
import OverviewPage from '@/pages/Overview'
import HealthPage from '@/pages/Health'
import ChangesPage from '@/pages/Changes'
import InventoryPage from '@/pages/Inventory'
import SnapshotsPage from '@/pages/Snapshots'
import AssistantPage from '@/pages/Assistant'
import ConnectionsPage from '@/pages/Connections'
import SettingsPage from '@/pages/Settings'

export default function App() {
  return (
    <BrowserRouter>
      <AppStateProvider>
        <AssistantStateProvider>
          <Routes>
            <Route element={<Shell />}>
              <Route index element={<OverviewPage />} />
              <Route path="health" element={<HealthPage />} />
              <Route path="changes" element={<ChangesPage />} />
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
    </BrowserRouter>
  )
}
