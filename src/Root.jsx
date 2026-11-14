import React from 'react';
import { Route, Routes } from 'react-router-dom';
import App from './App.jsx';
import AdminPortal from './portal/AdminPortal';
import { AccountStatePage, ActivatePage, ForgotPasswordPage, LoginPage, ResetPasswordPage, UnauthorizedPage } from './portal/AuthPages';
import { AuthProvider, RequireAuth } from './portal/AuthContext';
import DoctorPortal from './portal/DoctorPortal';
import { ToastProvider } from './portal/ui';
import './portal/portal.css';

// Route map. Guards here only shape navigation; the backend authorises every request independently.
export default function Root() {
  return <AuthProvider><ToastProvider><Routes>
    <Route path="/login" element={<LoginPage />} />
    <Route path="/activate" element={<ActivatePage />} />
    <Route path="/forgot-password" element={<ForgotPasswordPage />} />
    <Route path="/reset-password" element={<ResetPasswordPage />} />
    <Route path="/unauthorized" element={<UnauthorizedPage />} />
    <Route path="/account-status" element={<AccountStatePage />} />
    <Route path="/admin/*" element={<RequireAuth roles={['admin']}><AdminPortal /></RequireAuth>} />
    <Route path="/doctor/*" element={<RequireAuth roles={['doctor']}><DoctorPortal /></RequireAuth>} />
    {/* Clinical audit workspace: any clinical role; administrators are kept out of clinical views. */}
    <Route path="/*" element={<RequireAuth deny={['admin']}><App /></RequireAuth>} />
  </Routes></ToastProvider></AuthProvider>;
}
