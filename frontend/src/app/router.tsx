import { createBrowserRouter, Navigate } from 'react-router-dom'

import { AppShell } from '@/app/layout/AppShell'
import { DashboardPage } from '@/pages/dashboard'
import { TrainingLabPage } from '@/pages/training-lab'
import { ModelsPage } from '@/pages/models'
import { DataPage } from '@/pages/data'
import { SettingsPage } from '@/pages/settings'

export const router = createBrowserRouter([
  {
    path: '/',
    element: <AppShell />,
    children: [
      { index: true, element: <Navigate to="/dashboard" replace /> },
      { path: 'dashboard', element: <DashboardPage /> },
      { path: 'training-lab', element: <TrainingLabPage /> },
      { path: 'models', element: <ModelsPage /> },
      { path: 'data', element: <DataPage /> },
      { path: 'settings', element: <SettingsPage /> },
    ],
  },
], {
  basename: '/app',
})
