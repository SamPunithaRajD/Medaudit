// Authenticated API client for the account portals. Always talks to the real backend:
// authentication is never mocked. The session lives in an HttpOnly cookie the browser manages;
// the CSRF token is kept in memory only (never localStorage) and sent on state-changing requests.
import axios from 'axios';

let csrfToken = null;
let onUnauthenticated = () => {};

export const setCsrfToken = t => { csrfToken = t || null; };
export const setUnauthenticatedHandler = fn => { onUnauthenticated = fn; };

export const portalApi = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? '',
  withCredentials: true,
  timeout: 30000,
});

portalApi.interceptors.request.use(cfg => {
  const m = (cfg.method || 'get').toUpperCase();
  if (csrfToken && ['POST', 'PUT', 'PATCH', 'DELETE'].includes(m)) cfg.headers['X-CSRF-Token'] = csrfToken;
  return cfg;
});

portalApi.interceptors.response.use(r => r, err => {
  const status = err.response?.status || 0;
  const data = err.response?.data;
  const e = {
    status,
    code: data?.error?.code || (status ? 'HTTP_ERROR' : 'NETWORK_ERROR'),
    message: data?.error?.message || data?.message || (status ? 'Request failed' : 'Cannot reach the MedAudit service. Check that the backend is running.'),
    details: data?.error?.details || [],
    requestId: data?.error?.request_id,
    retryAfter: err.response?.headers?.['retry-after'],
  };
  if (status === 401 && !err.config?.skipAuthRedirect) onUnauthenticated(e);
  return Promise.reject(e);
});

const get = (url, params, cfg) => portalApi.get(url, { params, ...cfg }).then(r => r.data);
const send = (method, url, body, cfg) => portalApi.request({ method, url, data: body, ...cfg }).then(r => r.data);

export const auth = {
  me: () => get('/api/v1/auth/me', null, { skipAuthRedirect: true }),
  login: body => send('post', '/api/v1/auth/login', body, { skipAuthRedirect: true }),
  activate: body => send('post', '/api/v1/auth/doctor/activate', body, { skipAuthRedirect: true }),
  forgot: body => send('post', '/api/v1/auth/password/forgot', body, { skipAuthRedirect: true }),
  reset: body => send('post', '/api/v1/auth/password/reset', body, { skipAuthRedirect: true }),
  logout: () => send('post', '/api/v1/auth/logout'),
  changePassword: body => send('post', '/api/v1/auth/password/change', body),
  sessions: () => get('/api/v1/auth/sessions'),
  revokeSession: id => send('delete', `/api/v1/auth/sessions/${encodeURIComponent(id)}`),
  revokeOthers: () => send('post', '/api/v1/auth/sessions/revoke-others'),
  activity: params => get('/api/v1/auth/activity', params),
};

const d = id => `/api/v1/admin/doctors/${encodeURIComponent(id)}`;
export const admin = {
  dashboard: () => get('/api/v1/admin/dashboard'),
  doctors: params => get('/api/v1/admin/doctors', params),
  doctor: id => get(d(id)),
  create: body => send('post', '/api/v1/admin/doctors', body),
  update: (id, body) => send('patch', d(id), body),
  suspend: (id, reason) => send('post', `${d(id)}/suspend`, { reason }),
  deactivate: (id, reason) => send('post', `${d(id)}/deactivate`, { reason }),
  reactivate: (id, reason) => send('post', `${d(id)}/reactivate`, reason ? { reason } : {}),
  archive: (id, reason) => send('delete', d(id), null, { params: { reason } }),
  resendActivation: id => send('post', `${d(id)}/resend-activation`),
  resetActivation: (id, reason, confirm_doctor_id) => send('post', `${d(id)}/reset-activation`, { reason, confirm_doctor_id }),
  passwordReset: (id, revoke_sessions = true) => send('post', `${d(id)}/password-reset`, { revoke_sessions }),
  revokeSessions: id => send('delete', `${d(id)}/sessions`),
  exportCsv: params => portalApi.get('/api/v1/admin/doctors/export', { params, responseType: 'blob' }).then(r => r.data),
  template: () => portalApi.get('/api/v1/admin/doctors/import-template', { responseType: 'blob' }).then(r => r.data),
  importCsv: (file, commit) => { const f = new FormData(); f.append('file', file); return send('post', '/api/v1/admin/doctors/import', f, { params: { commit } }); },
  auditLogs: params => get('/api/v1/admin/audit-logs', params),
  verifyLogs: () => get('/api/v1/admin/audit-logs/verify'),
  settings: () => get('/api/v1/admin/settings'),
  updateSettings: body => send('patch', '/api/v1/admin/settings', body),
};

const p = id => `/api/v1/doctor/patients/${encodeURIComponent(id)}`;
export const doctor = {
  dashboard: () => get('/api/v1/doctor/dashboard'),
  profile: () => get('/api/v1/doctor/profile'),
  updateProfile: body => send('patch', '/api/v1/doctor/profile', body),
  preferences: () => get('/api/v1/doctor/preferences'),
  updatePreferences: body => send('patch', '/api/v1/doctor/preferences', body),
  patients: params => get('/api/v1/doctor/patients', params),
  patient: id => get(p(id)),
  createPatient: body => send('post', '/api/v1/doctor/patients', body),
  updatePatient: (id, body) => send('patch', p(id), body),
  upload: (id, file) => { const f = new FormData(); f.append('file', file); return send('post', `${p(id)}/documents`, f, { timeout: 300000 }); },
  share: (id, body) => send('post', `${p(id)}/share`, body),
  unshare: (id, doctorId) => send('delete', `${p(id)}/share/${encodeURIComponent(doctorId)}`),
  cases: params => get('/api/v1/doctor/cases', params),
};

const c = id => `/api/v1/cases/${encodeURIComponent(id)}`;
export const clinical = {
  caseDetail: id => get(c(id)),
  status: id => get(`${c(id)}/status`),
  timeline: (id, params) => get(`${c(id)}/timeline`, params),
  findings: (id, params) => get(`${c(id)}/findings`, params),
  report: id => get(`${c(id)}/audit-report`),
  reportPdf: id => portalApi.get(`${c(id)}/audit-report/export`, { params: { format: 'pdf' }, responseType: 'blob' }).then(r => r.data),
};

export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = Object.assign(document.createElement('a'), { href: url, download: filename });
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export const fieldErrors = err => Object.fromEntries((err?.details || []).filter(x => x.field).map(x => [x.field.split('.').pop(), x.issue]));
