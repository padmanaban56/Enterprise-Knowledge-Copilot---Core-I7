import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import './index.css'
import { AuthProvider } from './context/AuthContext'
import ProtectedRoute from './components/ProtectedRoute'
import Layout from './components/layout/Layout'
import Landing from './pages/Landing'
import Login from './pages/Login'
import ChangePassword from './pages/ChangePassword'
import Dashboard from './pages/Dashboard'
import Repositories from './pages/Repositories'
import Documents from './pages/Documents'
import DocumentDetail from './pages/DocumentDetail'
import Tickets from './pages/Tickets'
import Chat from './pages/Chat'
import Admin from './pages/Admin'
import AccessRequests from './pages/AccessRequests'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          {/* Public */}
          <Route path="/" element={<Landing />} />
          <Route path="/login" element={<Login />} />

          {/* Change password — requires auth but not full layout */}
          <Route path="/change-password" element={
            <ProtectedRoute><ChangePassword /></ProtectedRoute>
          } />

          {/* Protected app shell */}
          <Route element={
            <ProtectedRoute><Layout /></ProtectedRoute>
          }>
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/repositories" element={<Repositories />} />
            <Route path="/documents" element={<Documents />} />
            <Route path="/documents/:docId" element={<DocumentDetail />} />
            <Route path="/tickets" element={<Tickets />} />
            <Route path="/chat" element={<Chat />} />
            <Route path="/admin" element={<Admin />} />
            <Route path="/access-requests" element={<AccessRequests />} />
          </Route>

          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>
)