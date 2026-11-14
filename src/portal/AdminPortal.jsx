import React, { useEffect, useRef, useState } from 'react';
import { Link, Route, Routes, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Activity, AlertTriangle, Ban, CheckCircle2, ClipboardList, Download, Eye, FileUp, History, KeyRound, LayoutDashboard,
  MoreHorizontal, Pencil, RefreshCw, RotateCcw, Search, Settings, ShieldAlert, ShieldCheck, Trash2, Upload, UserCheck,
  UserPlus, Users, UserX } from 'lucide-react';
import { admin, downloadBlob, fieldErrors } from './api';
import PortalLayout from './PortalLayout';
import { Alert, Button, ConfirmDialog, EmptyState, Field, Heading, Loading, Modal, Pagination, Panel, SecretReveal, Stat,
  StatusPill, fmtDate, fmtDay, useLoad, useToast } from './ui';

const NAV = [
  { label: 'ADMINISTRATION', items: [
    { to: '/admin', label: 'Dashboard', icon: LayoutDashboard, end: true },
    { to: '/admin/doctors', label: 'Doctor directory', icon: Users },
    { to: '/admin/doctors/new', label: 'Add doctor', icon: UserPlus, end: true },
  ] },
  { label: 'SECURITY', items: [
    { to: '/admin/security', label: 'Security logs', icon: ShieldCheck },
    { to: '/admin/settings', label: 'Authentication settings', icon: Settings },
  ] },
];
const FILTERS = [['all', 'All doctors'], ['active', 'Active'], ['pending_activation', 'Pending activation'], ['suspended', 'Suspended'], ['deactivated', 'Deactivated'], ['archived', 'Archived']];

export default function AdminPortal() {
  return <PortalLayout nav={NAV} area="Hospital administration"><Routes>
    <Route index element={<AdminDashboard />} />
    <Route path="doctors" element={<DoctorDirectory />} />
    <Route path="doctors/new" element={<DoctorForm />} />
    <Route path="doctors/:doctorId" element={<DoctorDetail />} />
    <Route path="doctors/:doctorId/edit" element={<DoctorForm edit />} />
    <Route path="security" element={<SecurityLogs />} />
    <Route path="settings" element={<AdminSettings />} />
    <Route path="*" element={<EmptyState icon={AlertTriangle} title="Page not found" />} />
  </Routes></PortalLayout>;
}

function ErrorPanel({ error, onRetry }) {
  return <Panel><Alert>{error.message}</Alert>{onRetry && <Button icon={RefreshCw} onClick={onRetry}>Retry</Button>}</Panel>;
}

// ---------- dashboard ----------
function AdminDashboard() {
  const { loading, data, error, reload } = useLoad(() => admin.dashboard());
  if (loading) return <Loading />;
  if (error) return <ErrorPanel error={error} onRetry={reload} />;
  return <>
    <Heading eyebrow="HOSPITAL ADMINISTRATION" title="Account overview" description="Doctor account lifecycle and security activity across the hospital."
      action={<Link to="/admin/doctors/new" className="button primary"><UserPlus size={15} /> Add doctor</Link>} />
    <div className="stats-grid">
      <Stat label="Doctor IDs registered" value={data.total_doctor_ids} change={`${data.activated_accounts} activated`} icon={Users} />
      <Stat label="Active doctors" value={data.active_doctors} change={`${data.logins_7d} sign-ins in 7 days`} icon={UserCheck} tone="teal" />
      <Stat label="Pending activation" value={data.pending_activations} change="Awaiting first sign-in" icon={KeyRound} tone="blue" />
      <Stat label="Suspended / deactivated" value={data.suspended_accounts + data.deactivated_accounts} change={`${data.failed_logins_7d} failed sign-ins in 7 days`} icon={UserX} tone="amber" />
    </div>
    {data.password_reset_requests > 0 && <Alert tone="info">{data.password_reset_requests} doctor(s) requested a password reset. Open the directory to verify identity and issue a reset code.</Alert>}
    <div className="portal-grid">
      <Panel title="Recent activations" subtitle="Doctors who completed first-time activation">
        {data.recent_registrations.length ? <ul className="activity-list">{data.recent_registrations.map(r => <li key={r.doctor_id}><Link to={`/admin/doctors/${r.doctor_id}`}><b>{r.full_name}</b><small>{r.doctor_id}</small></Link><span>{fmtDate(r.activated_at)}</span></li>)}</ul>
          : <EmptyState icon={UserCheck} title="No activations yet" />}
      </Panel>
      <Panel title="Recent sign-ins" subtitle="Latest successful doctor sign-ins">
        {data.recent_logins.length ? <ul className="activity-list">{data.recent_logins.map(r => <li key={r.doctor_id}><Link to={`/admin/doctors/${r.doctor_id}`}><b>{r.full_name}</b><small>{r.doctor_id}</small></Link><span>{fmtDate(r.last_login_at)}</span></li>)}</ul>
          : <EmptyState icon={History} title="No sign-ins yet" />}
      </Panel>
    </div>
    <Panel title="Account security events" subtitle="Failures, denials and administrative account changes" action={<Link className="text-button" to="/admin/security">All logs</Link>}>
      <EventTable events={data.security_events} />
    </Panel>
  </>;
}

function EventTable({ events }) {
  if (!events?.length) return <EmptyState icon={ShieldCheck} title="No events recorded" />;
  return <div className="table-scroll"><table className="data-table"><thead><tr><th>Time</th><th>Event</th><th>Outcome</th><th>Actor</th><th>Target</th><th>Details</th></tr></thead>
    <tbody>{events.map(e => <tr key={e.event_id}><td>{fmtDate(e.occurred_at)}</td><td><code>{e.event_type}</code></td><td><StatusPill value={e.outcome} /></td>
      <td>{e.actor_role ? `${e.actor_role}` : 'system / anonymous'}</td><td>{e.resource_id || e.target_user_id || '—'}</td>
      <td className="wrap">{Object.entries(e.metadata || {}).map(([k, v]) => `${k}: ${v}`).join(' · ') || '—'}</td></tr>)}</tbody></table></div>;
}

// ---------- directory ----------
function DoctorDirectory() {
  const [params, setParams] = useSearchParams();
  const status = params.get('status') || 'all';
  const page = Number(params.get('page') || 1);
  const sort = params.get('sort') || 'created_at:desc';
  const [q, setQ] = useState(params.get('q') || '');
  const [department, setDepartment] = useState(params.get('department') || '');
  const [importOpen, setImportOpen] = useState(false);
  const toast = useToast();
  const update = patch => setParams(Object.fromEntries(Object.entries({ status, page, sort, q: params.get('q') || '', department, ...patch }).filter(([, v]) => v && v !== 'all')));
  const { loading, data, error, reload } = useLoad(() => admin.doctors({ status, page, page_size: 15, sort, q: params.get('q') || undefined, department: department || undefined }), [status, page, sort, params.get('q'), department]);
  useEffect(() => { const t = setTimeout(() => { if (q !== (params.get('q') || '')) update({ q, page: 1 }); }, 350); return () => clearTimeout(t); }, [q]); // eslint-disable-line react-hooks/exhaustive-deps
  const sortBy = field => update({ sort: sort === `${field}:asc` ? `${field}:desc` : `${field}:asc`, page: 1 });
  const arrow = field => sort.startsWith(field) ? (sort.endsWith('asc') ? ' ↑' : ' ↓') : '';
  const exportCsv = async () => { try { downloadBlob(await admin.exportCsv({ status, q: q || undefined }), 'doctor-directory.csv'); } catch (e) { toast(e.message, 'error'); } };
  return <>
    <Heading eyebrow="ADMINISTRATION" title="Doctor directory" description="Provisioned Doctor IDs, activation state and account status."
      action={<div className="button-row"><Button icon={FileUp} onClick={() => setImportOpen(true)}>Bulk import</Button><Button icon={Download} onClick={exportCsv}>Export CSV</Button><Link to="/admin/doctors/new" className="button primary"><UserPlus size={15} /> Add doctor</Link></div>} />
    <Panel>
      <div className="filter-row portal-filters">
        <div className="tabs" role="tablist">{FILTERS.map(([k, label]) => <button key={k} role="tab" aria-selected={status === k} className={`tab ${status === k ? 'active' : ''}`} onClick={() => update({ status: k, page: 1 })}>{label}</button>)}</div>
        <div className="filter-controls">
          <label className="global-search"><Search size={15} /><input value={q} onChange={e => setQ(e.target.value)} placeholder="Search ID, name, department, email" aria-label="Search doctors" /></label>
          <select value={department} onChange={e => { setDepartment(e.target.value); update({ department: e.target.value, page: 1 }); }} aria-label="Filter by department"><option value="">All departments</option>{(data?.departments || []).map(d => <option key={d}>{d}</option>)}</select>
        </div>
      </div>
      {loading && !data ? <Loading /> : error ? <Alert>{error.message}</Alert> : !data.items.length ? <EmptyState icon={Users} title="No doctors match these filters" /> :
        <div className="table-scroll"><table className="data-table responsive"><thead><tr>
          <th><button onClick={() => sortBy('doctor_id')}>Doctor ID{arrow('doctor_id')}</button></th><th><button onClick={() => sortBy('full_name')}>Name{arrow('full_name')}</button></th>
          <th><button onClick={() => sortBy('department')}>Department{arrow('department')}</button></th><th>Designation</th><th>Email</th><th>Activation</th>
          <th><button onClick={() => sortBy('status')}>Status{arrow('status')}</button></th><th><button onClick={() => sortBy('created_at')}>Added{arrow('created_at')}</button></th>
          <th><button onClick={() => sortBy('last_login_at')}>Last sign-in{arrow('last_login_at')}</button></th><th><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>{data.items.map(d => <tr key={d.doctor_id}>
            <td data-label="Doctor ID"><Link to={`/admin/doctors/${d.doctor_id}`} className="id-link">{d.doctor_id}</Link>{d.password_reset_requested_at && <span className="dot-badge" title="Password reset requested" />}</td>
            <td data-label="Name"><b>{d.full_name}</b></td><td data-label="Department">{d.department || '—'}</td><td data-label="Designation">{d.designation || '—'}</td>
            <td data-label="Email">{d.email}</td><td data-label="Activation"><StatusPill value={d.is_activated ? 'activated' : d.activation_status} /></td>
            <td data-label="Status"><StatusPill value={d.account_status} /></td><td data-label="Added">{fmtDay(d.created_at)}</td><td data-label="Last sign-in">{fmtDate(d.last_login_at)}</td>
            <td><DoctorActions doctor={d} onChanged={reload} /></td></tr>)}</tbody></table></div>}
      <Pagination pagination={data?.pagination} onPage={n => update({ page: n })} />
    </Panel>
    <ImportDialog open={importOpen} onClose={() => setImportOpen(false)} onDone={reload} />
  </>;
}

// Row action menu; every action calls the backend and is confirmed when sensitive.
function DoctorActions({ doctor: d, onChanged, inline }) {
  const navigate = useNavigate();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState(null);
  const [secret, setSecret] = useState(null);
  const ref = useRef(null);
  useEffect(() => { const close = e => { if (!ref.current?.contains(e.target)) setOpen(false); }; document.addEventListener('mousedown', close); return () => document.removeEventListener('mousedown', close); }, []);
  const done = msg => { toast(msg); onChanged?.(); };
  const id = d.doctor_id;
  const actions = [
    { label: 'View profile', icon: Eye, run: () => navigate(`/admin/doctors/${id}`) },
    { label: 'Edit details', icon: Pencil, run: () => navigate(`/admin/doctors/${id}/edit`), hide: d.account_status === 'archived' },
    { label: 'Resend activation code', icon: KeyRound, hide: d.is_activated || d.account_status !== 'pending_activation',
      run: async () => { const r = await admin.resendActivation(id); setSecret({ title: 'New activation code', code: r.activation.activation_code, info: r.activation, doctor: r.doctor }); onChanged?.(); } },
    { label: 'Issue password reset', icon: RotateCcw, hide: !d.is_activated || d.account_status !== 'active',
      confirm: { title: 'Issue a password reset code?', message: `A one-time reset code will be generated for ${d.full_name}. Their current sessions will be signed out. Verify the doctor’s identity before sharing the code.`, confirmLabel: 'Generate reset code' },
      run: async () => { const r = await admin.passwordReset(id, true); setSecret({ title: 'Password reset code', code: r.password_reset.reset_code, info: { expires_at: r.password_reset.expires_at, activation_url: r.password_reset.reset_url }, doctor: r.doctor }); onChanged?.(); } },
    { label: 'Suspend account', icon: Ban, danger: true, hide: !['active', 'pending_activation'].includes(d.account_status),
      confirm: { title: `Suspend ${id}?`, message: 'The doctor will be signed out immediately and cannot sign in until reactivated. Records are retained.', requireReason: true, danger: true, confirmLabel: 'Suspend' },
      run: async ({ reason }) => { await admin.suspend(id, reason); done(`${id} suspended`); } },
    { label: 'Reactivate account', icon: CheckCircle2, hide: !['suspended', 'deactivated', 'archived'].includes(d.account_status),
      confirm: { title: `Reactivate ${id}?`, message: 'The doctor will be able to sign in again.', confirmLabel: 'Reactivate' },
      run: async () => { await admin.reactivate(id); done(`${id} reactivated`); } },
    { label: 'Deactivate account', icon: UserX, danger: true, hide: ['deactivated', 'archived'].includes(d.account_status),
      confirm: { title: `Deactivate ${id}?`, message: 'Use when a doctor leaves the hospital. Sessions end immediately; records are retained.', requireReason: true, danger: true, confirmLabel: 'Deactivate' },
      run: async ({ reason }) => { await admin.deactivate(id, reason); done(`${id} deactivated`); } },
    { label: 'Reset activation', icon: ShieldAlert, danger: true, hide: !d.is_activated || d.account_status === 'archived',
      confirm: { title: `Reset activation for ${id}?`, message: 'This clears the password, ends all sessions and returns the account to pending activation with a new code. Use only after identity verification.', requireReason: true, typeToConfirm: id, danger: true, confirmLabel: 'Reset activation' },
      run: async ({ reason, typed }) => { const r = await admin.resetActivation(id, reason, typed); setSecret({ title: 'New activation code', code: r.activation.activation_code, info: r.activation, doctor: r.doctor }); onChanged?.(); } },
    { label: 'Archive account', icon: Trash2, danger: true, hide: d.account_status === 'archived',
      confirm: { title: `Archive ${id}?`, message: 'Archived accounts cannot sign in and are hidden from the default directory. Patients, cases and audit history are preserved, not deleted.', requireReason: true, typeToConfirm: id, danger: true, confirmLabel: 'Archive' },
      run: async ({ reason }) => { await admin.archive(id, reason); done(`${id} archived`); } },
  ].filter(a => !a.hide);
  const trigger = async a => { setOpen(false); if (a.confirm) setConfirm(a); else { try { await a.run({}); } catch (e) { toast(e.message, 'error'); } } };
  return <div className="row-actions" ref={ref}>
    {inline ? <div className="button-row wrap">{actions.filter(a => a.label !== 'View profile').map(a => <Button key={a.label} icon={a.icon} variant={a.danger ? 'danger-soft' : 'secondary'} onClick={() => trigger(a)}>{a.label}</Button>)}</div>
      : <><button className="icon-button" aria-haspopup="menu" aria-expanded={open} aria-label={`Actions for ${id}`} onClick={() => setOpen(!open)}><MoreHorizontal size={17} /></button>
        {open && <div className="action-menu" role="menu">{actions.map(a => <button key={a.label} role="menuitem" className={a.danger ? 'danger' : ''} onClick={() => trigger(a)}><a.icon size={14} />{a.label}</button>)}</div>}</>}
    <ConfirmDialog open={!!confirm} {...(confirm?.confirm || {})} onConfirm={v => confirm.run(v)} onClose={() => setConfirm(null)} />
    <SecretReveal open={!!secret} title={secret?.title} secret={secret?.code}
      intro={`Give this code to ${secret?.doctor?.full_name} (${secret?.doctor?.doctor_id}) through a verified hospital channel.`}
      rows={[['Doctor ID', secret?.doctor?.doctor_id], ['Registered email', secret?.doctor?.email], ['Expires', fmtDate(secret?.info?.expires_at)], ['Link', secret?.info?.activation_url]]}
      onClose={() => setSecret(null)} />
  </div>;
}

// ---------- add / edit ----------
function DoctorForm({ edit }) {
  const { doctorId } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const empty = { doctor_id: '', full_name: '', email: '', phone: '', department: '', designation: '', employee_ref: '', valid_until: '' };
  const [form, setForm] = useState(empty);
  const [autoId, setAutoId] = useState(false);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState(null);
  const [loaded, setLoaded] = useState(!edit);
  useEffect(() => {
    if (!edit) return;
    admin.doctor(doctorId).then(r => { const d = r.doctor; setForm({ ...empty, ...Object.fromEntries(Object.keys(empty).map(k => [k, d[k] ?? ''])), updated_at: d.updated_at }); setLoaded(true); }).catch(setError);
  }, [edit, doctorId]); // eslint-disable-line react-hooks/exhaustive-deps
  const fe = fieldErrors(error);
  const set = k => e => setForm({ ...form, [k]: e.target.value });
  const submit = async e => {
    e.preventDefault(); setBusy(true); setError(null);
    const clean = Object.fromEntries(Object.entries(form).filter(([k]) => k in empty).map(([k, v]) => [k, v === '' ? null : v]));
    try {
      if (edit) {
        delete clean.doctor_id;
        await admin.update(doctorId, { ...clean, expected_updated_at: form.updated_at });
        toast('Doctor details saved'); navigate(`/admin/doctors/${doctorId}`);
      } else {
        if (autoId) delete clean.doctor_id;
        setCreated(await admin.create(clean));
      }
    } catch (err) { setError(err); } finally { setBusy(false); }
  };
  if (!loaded) return error ? <ErrorPanel error={error} /> : <Loading />;
  return <>
    <Heading eyebrow={edit ? `EDIT ${doctorId}` : 'PROVISION A DOCTOR ID'} title={edit ? 'Edit doctor details' : 'Add a doctor'}
      description={edit ? 'Doctor ID cannot be changed. Changes are recorded in the audit log.' : 'The doctor will activate this ID themselves with a one-time code. No password is created by the administrator.'} />
    <Panel>
      <form className="portal-form" onSubmit={submit} noValidate>
        <div className="form-grid two">
          {!edit && <div className="form-field"><label htmlFor="did">Doctor ID</label>
            <input id="did" value={autoId ? '' : form.doctor_id} onChange={set('doctor_id')} placeholder={autoId ? 'Generated on save' : 'e.g. DOC1001'} disabled={autoId} aria-invalid={!!fe.doctor_id} />
            <label className="check"><input type="checkbox" checked={autoId} onChange={e => setAutoId(e.target.checked)} /> Generate a unique ID automatically</label>
            {fe.doctor_id && <small className="field-error">{fe.doctor_id}</small>}</div>}
          <Field label="Full name" error={fe.full_name}><input value={form.full_name} onChange={set('full_name')} required /></Field>
          <Field label="Hospital email" error={fe.email} hint="Used to verify activation"><input type="email" value={form.email} onChange={set('email')} required /></Field>
          <Field label="Phone (optional)" error={fe.phone}><input value={form.phone} onChange={set('phone')} inputMode="tel" /></Field>
          <Field label="Department" error={fe.department}><input value={form.department} onChange={set('department')} required /></Field>
          <Field label="Designation" error={fe.designation}><input value={form.designation} onChange={set('designation')} placeholder="e.g. Senior Consultant" /></Field>
          <Field label="Employee / registration reference" error={fe.employee_ref}><input value={form.employee_ref} onChange={set('employee_ref')} /></Field>
          <Field label="Account valid until (optional)" error={fe.valid_until} hint="Activation is refused after this date"><input type="date" value={form.valid_until || ''} onChange={set('valid_until')} /></Field>
        </div>
        {!edit && <Alert tone="info">Activation policy: one-time code + registered email match. Codes expire per the authentication settings and can be re-issued.</Alert>}
        {error && !Object.keys(fe).length && <Alert>{error.message}</Alert>}
        <div className="form-actions"><Button type="button" onClick={() => navigate(-1)}>Cancel</Button><Button variant="primary" type="submit" busy={busy} disabled={!form.full_name || !form.email || !form.department || (!edit && !autoId && !form.doctor_id)}>{edit ? 'Save changes' : 'Create Doctor ID'}</Button></div>
      </form>
    </Panel>
    <SecretReveal open={!!created} title="Doctor ID created — activation code" secret={created?.activation.activation_code}
      intro={`${created?.doctor.full_name} is registered as ${created?.doctor.doctor_id} with status Pending activation.`}
      rows={[['Doctor ID', created?.doctor.doctor_id], ['Registered email', created?.doctor.email], ['Code expires', fmtDate(created?.activation.expires_at)], ['Activation link', created?.activation.activation_url]]}
      onClose={() => navigate(`/admin/doctors/${created.doctor.doctor_id}`)} />
  </>;
}

// ---------- detail ----------
function DoctorDetail() {
  const { doctorId } = useParams();
  const { loading, data, error, reload } = useLoad(() => admin.doctor(doctorId), [doctorId]);
  const toast = useToast();
  if (loading && !data) return <Loading />;
  if (error) return <ErrorPanel error={error} onRetry={reload} />;
  const d = data.doctor;
  const revoke = async () => { try { const r = await admin.revokeSessions(d.doctor_id); toast(`${r.revoked} session(s) ended`); reload(); } catch (e) { toast(e.message, 'error'); } };
  return <>
    <Heading eyebrow={`DOCTOR ACCOUNT · ${d.doctor_id}`} title={d.full_name} description={`${d.designation || 'Doctor'} · ${d.department || 'No department'}`}
      action={<Link to="/admin/doctors" className="button">Back to directory</Link>} />
    {d.password_reset_requested_at && <Alert tone="info">Password reset requested on {fmtDate(d.password_reset_requested_at)}. Verify identity, then issue a reset code.</Alert>}
    <Panel title="Account actions"><DoctorActions doctor={d} onChanged={reload} inline /></Panel>
    <div className="portal-grid">
      <Panel title="Profile">
        <dl className="detail-list">
          <dt>Doctor ID</dt><dd><code>{d.doctor_id}</code></dd><dt>Email</dt><dd>{d.email}</dd><dt>Phone</dt><dd>{d.phone || '—'}</dd>
          <dt>Department</dt><dd>{d.department || '—'}</dd><dt>Designation</dt><dd>{d.designation || '—'}</dd><dt>Employee ref</dt><dd>{d.employee_ref || '—'}</dd>
          <dt>Valid until</dt><dd>{fmtDay(d.valid_until)}</dd><dt>Contact</dt><dd>{d.contact_info || '—'}</dd>
        </dl>
      </Panel>
      <Panel title="Account status">
        <dl className="detail-list">
          <dt>Status</dt><dd><StatusPill value={d.account_status} /></dd><dt>Reason</dt><dd>{d.status_reason || '—'}</dd>
          <dt>Activation</dt><dd><StatusPill value={d.is_activated ? 'activated' : d.activation_status} /></dd><dt>Activated</dt><dd>{fmtDate(d.activated_at)}</dd>
          <dt>Created</dt><dd>{fmtDate(d.created_at)}</dd><dt>Last sign-in</dt><dd>{fmtDate(d.last_login_at)}</dd>
        </dl>
        <p className="muted-note">Administrators manage accounts only. Clinical records are not visible from this portal.</p>
      </Panel>
    </div>
    <Panel title="Active sessions" subtitle="Signed-in devices" action={data.active_sessions.length > 0 && <Button icon={Ban} variant="danger-soft" onClick={revoke}>End all sessions</Button>}>
      {data.active_sessions.length ? <SessionTable sessions={data.active_sessions} /> : <EmptyState icon={Activity} title="No active sessions" />}
    </Panel>
    <Panel title="Activation & reset history">
      {data.activation_history.length ? <div className="table-scroll"><table className="data-table"><thead><tr><th>Type</th><th>Issued</th><th>Expires</th><th>State</th><th>Failed attempts</th></tr></thead>
        <tbody>{data.activation_history.map(t => <tr key={t.id}><td>{t.purpose === 'activation' ? 'Activation code' : 'Password reset code'}</td><td>{fmtDate(t.created_at)}</td><td>{fmtDate(t.expires_at)}</td><td><StatusPill value={t.state} /></td><td>{t.failed_attempts}</td></tr>)}</tbody></table></div>
        : <EmptyState icon={KeyRound} title="No codes issued" />}
    </Panel>
    <div className="portal-grid">
      <Panel title="Security events"><EventTable events={data.security_events} /></Panel>
      <Panel title="Administrative changes"><EventTable events={data.administrative_changes} /></Panel>
    </div>
  </>;
}

export function SessionTable({ sessions, onRevoke }) {
  return <div className="table-scroll"><table className="data-table"><thead><tr><th>Device</th><th>Started</th><th>Last activity</th><th>Expires</th><th /></tr></thead>
    <tbody>{sessions.map(s => <tr key={s.id}><td className="wrap">{s.user_agent || 'Unknown device'}{s.current && <StatusPill value="current" label="This device" />}{s.remember && <small> · remembered</small>}</td>
      <td>{fmtDate(s.created_at)}</td><td>{fmtDate(s.last_activity_at)}</td><td>{fmtDate(s.expires_at)}</td>
      <td>{onRevoke && <Button className="compact" onClick={() => onRevoke(s)}>{s.current ? 'Sign out' : 'End session'}</Button>}</td></tr>)}</tbody></table></div>;
}

// ---------- import ----------
function ImportDialog({ open, onClose, onDone }) {
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (open) { setFile(null); setPreview(null); setResult(null); setError(null); } }, [open]);
  const validate = async () => { setBusy(true); setError(null); try { setPreview(await admin.importCsv(file, false)); } catch (e) { setError(e); } finally { setBusy(false); } };
  const commit = async () => { setBusy(true); setError(null); try { const r = await admin.importCsv(file, true); if (r.committed) { setResult(r); onDone(); } else setPreview(r); } catch (e) { setError(e); } finally { setBusy(false); } };
  const exportCodes = () => downloadBlob(new Blob([['doctor_id,full_name,activation_code,expires_at', ...result.created.map(c => [c.doctor_id, `"${c.full_name}"`, c.activation_code, c.expires_at].join(','))].join('\n')], { type: 'text/csv' }), 'activation-codes.csv');
  return <Modal open={open} wide title="Bulk import doctors" onClose={busy ? undefined : onClose} footer={result
    ? <><Button icon={Download} onClick={exportCodes}>Download activation codes</Button><Button variant="primary" onClick={onClose}>Done</Button></>
    : <><Button onClick={onClose}>Cancel</Button><Button icon={Search} onClick={validate} disabled={!file} busy={busy && !preview}>Validate</Button>
      <Button variant="primary" icon={Upload} onClick={commit} disabled={!preview || preview.invalid_rows > 0} busy={busy && !!preview}>Import {preview ? preview.valid_rows : ''} doctors</Button></>}>
    {result ? <>
      <Alert tone="info">{result.created.length} Doctor IDs created with status Pending activation. Activation codes are shown only now — download them and distribute securely, then delete the file.</Alert>
      <div className="table-scroll"><table className="data-table"><thead><tr><th>Doctor ID</th><th>Name</th><th>Activation code</th></tr></thead><tbody>{result.created.map(c => <tr key={c.doctor_id}><td>{c.doctor_id}</td><td>{c.full_name}</td><td><code>{c.activation_code}</code></td></tr>)}</tbody></table></div>
    </> : <>
      <p className="modal-text">Upload a UTF-8 CSV with columns <code>doctor_id, full_name, email, phone, department, designation, employee_ref, valid_until</code>. Leave doctor_id blank to generate one. Every row is validated first; nothing is created unless all rows are valid.</p>
      <div className="button-row"><input type="file" accept=".csv,text/csv" onChange={e => { setFile(e.target.files[0]); setPreview(null); }} aria-label="CSV file" />
        <Button icon={Download} onClick={async () => downloadBlob(await admin.template(), 'doctor-import-template.csv')}>Template</Button></div>
      {error && <Alert>{error.message}</Alert>}
      {preview && <>
        <Alert tone={preview.invalid_rows ? 'error' : 'info'}>{preview.valid_rows} of {preview.total_rows} rows valid{preview.invalid_rows ? ` · fix ${preview.invalid_rows} row(s) and re-upload` : ' · ready to import'}</Alert>
        <div className="table-scroll short"><table className="data-table"><thead><tr><th>Row</th><th>Doctor ID</th><th>Name</th><th>Email</th><th>Department</th><th>Result</th></tr></thead>
          <tbody>{preview.rows.map(r => <tr key={r.row} className={r.valid ? '' : 'row-error'}><td>{r.row}</td><td>{r.data.doctor_id || 'auto'}</td><td>{r.data.full_name}</td><td>{r.data.email}</td><td>{r.data.department}</td><td className="wrap">{r.valid ? <StatusPill value="active" label="Valid" /> : r.errors.join('; ')}</td></tr>)}</tbody></table></div>
      </>}
    </>}
  </Modal>;
}

// ---------- security logs ----------
function SecurityLogs() {
  const [filters, setFilters] = useState({ category: '', outcome: '', doctor_id: '', page: 1 });
  const { loading, data, error } = useLoad(() => admin.auditLogs({ ...Object.fromEntries(Object.entries(filters).filter(([, v]) => v)), page_size: 25 }), [JSON.stringify(filters)]);
  const verify = useLoad(() => admin.verifyLogs(), []);
  const set = k => e => setFilters({ ...filters, [k]: e.target.value, page: 1 });
  return <>
    <Heading eyebrow="SECURITY" title="Security & audit logs" description="Append-only record of sign-ins, account changes and sensitive access. Logs contain no passwords, codes or session tokens." />
    <Panel title="Log integrity" subtitle="Each entry is HMAC-chained to the previous one; any modification breaks verification.">
      {verify.loading ? <Loading label="Verifying…" /> : verify.error ? <Alert>{verify.error.message}</Alert> :
        <Alert tone={verify.data.valid ? 'info' : 'error'}>{verify.data.valid ? `Chain verified · ${verify.data.checked} entries checked` : `Integrity check failed at event ${verify.data.broken_at_event}`}</Alert>}
    </Panel>
    <Panel>
      <div className="filter-row portal-filters"><div className="filter-controls">
        <select value={filters.category} onChange={set('category')} aria-label="Event category"><option value="">All categories</option><option value="auth">Authentication</option><option value="doctor">Doctor accounts</option><option value="admin">Administration</option><option value="clinical">Emergency clinical access</option><option value="case">Case activity</option><option value="document">Document access</option></select>
        <select value={filters.outcome} onChange={set('outcome')} aria-label="Outcome"><option value="">All outcomes</option><option value="success">Success</option><option value="failure">Failure</option><option value="denied">Denied</option></select>
        <label className="global-search"><Search size={15} /><input value={filters.doctor_id} onChange={set('doctor_id')} placeholder="Filter by Doctor ID" aria-label="Doctor ID" /></label>
      </div></div>
      {loading && !data ? <Loading /> : error ? <Alert>{error.message}</Alert> : <EventTable events={data.items} />}
      <Pagination pagination={data?.pagination} onPage={n => setFilters({ ...filters, page: n })} />
    </Panel>
  </>;
}

// ---------- settings ----------
function AdminSettings() {
  const { loading, data, error, reload } = useLoad(() => admin.settings());
  const [form, setForm] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const toast = useToast();
  useEffect(() => { if (data) setForm(data); }, [data]);
  if (loading || !form) return error ? <ErrorPanel error={error} onRetry={reload} /> : <Loading />;
  const num = k => e => setForm({ ...form, [k]: e.target.value === '' ? '' : Number(e.target.value) });
  const bool = k => e => setForm({ ...form, [k]: e.target.checked });
  const save = async e => {
    e.preventDefault(); setBusy(true); setErr(null);
    const keys = ['activation_token_hours', 'reset_token_hours', 'max_failed_logins', 'lockout_minutes', 'max_activation_attempts', 'remember_me_days', 'allow_forgot_password_requests', 'allow_emergency_access', 'emergency_access_max_minutes'];
    try { setForm(await admin.updateSettings(Object.fromEntries(keys.map(k => [k, form[k]])))); toast('Authentication settings saved'); } catch (x) { setErr(x); } finally { setBusy(false); }
  };
  const fe = fieldErrors(err);
  return <>
    <Heading eyebrow="SECURITY" title="Authentication settings" description="Hospital-level policy for activation, sign-in protection and emergency access." />
    <form onSubmit={save} className="settings-grid">
      <Panel title="Activation & recovery">
        <Field label="Activation code validity (hours)" error={fe.activation_token_hours}><input type="number" min={1} max={720} value={form.activation_token_hours} onChange={num('activation_token_hours')} /></Field>
        <Field label="Password reset code validity (hours)" error={fe.reset_token_hours}><input type="number" min={1} max={168} value={form.reset_token_hours} onChange={num('reset_token_hours')} /></Field>
        <Field label="Wrong-code attempts before a code is invalidated" error={fe.max_activation_attempts}><input type="number" min={3} max={20} value={form.max_activation_attempts} onChange={num('max_activation_attempts')} /></Field>
        <label className="check"><input type="checkbox" checked={form.allow_forgot_password_requests} onChange={bool('allow_forgot_password_requests')} /> Allow doctors to request a reset from the sign-in page</label>
      </Panel>
      <Panel title="Sign-in protection">
        <Field label="Failed sign-ins before temporary lockout" error={fe.max_failed_logins}><input type="number" min={3} max={20} value={form.max_failed_logins} onChange={num('max_failed_logins')} /></Field>
        <Field label="Lockout duration (minutes)" error={fe.lockout_minutes}><input type="number" min={1} max={1440} value={form.lockout_minutes} onChange={num('lockout_minutes')} /></Field>
        <Field label="'Keep me signed in' duration (days)" error={fe.remember_me_days}><input type="number" min={1} max={30} value={form.remember_me_days} onChange={num('remember_me_days')} /></Field>
      </Panel>
      <Panel title="Emergency clinical access" subtitle="Time-limited, justified and audited access for authorised staff">
        <label className="check"><input type="checkbox" checked={form.allow_emergency_access} onChange={bool('allow_emergency_access')} /> Allow emergency access requests</label>
        <Field label="Maximum duration (minutes)" error={fe.emergency_access_max_minutes}><input type="number" min={15} max={1440} value={form.emergency_access_max_minutes} onChange={num('emergency_access_max_minutes')} /></Field>
      </Panel>
      <Panel title="Server configuration" subtitle={form.environment.note}>
        <dl className="detail-list"><dt>Session length</dt><dd>{form.environment.session_ttl_minutes} min</dd><dt>Idle timeout</dt><dd>{form.environment.session_idle_timeout_minutes} min</dd>
          <dt>Minimum password length</dt><dd>{form.environment.password_min_length}</dd><dt>Secure cookies</dt><dd>{form.environment.secure_cookies ? 'Enabled (HTTPS)' : 'Disabled — enable for production'}</dd></dl>
      </Panel>
      <div className="form-actions span-all">{err && !Object.keys(fe).length && <Alert>{err.message}</Alert>}<Button variant="primary" type="submit" busy={busy} icon={ClipboardList}>Save settings</Button></div>
    </form>
  </>;
}
