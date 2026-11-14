import React, { useEffect, useState } from 'react';
import { Link, Route, Routes, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Activity, AlertTriangle, Bell, ClipboardList, Download, FileText, FolderOpen, KeyRound, LayoutDashboard, Lock,
  Monitor, Plus, RefreshCw, Search, Settings, Share2, ShieldCheck, Stethoscope, Upload, User, Users } from 'lucide-react';
import { auth, clinical, doctor, downloadBlob, fieldErrors } from './api';
import { applyTheme, useAuth } from './AuthContext';
import PortalLayout from './PortalLayout';
import { SessionTable } from './AdminPortal';
import { Alert, Button, ConfirmDialog, EmptyState, Field, Heading, Loading, Modal, Pagination, Panel, Stat, StatusPill,
  fmtDate, fmtDay, useLoad, useToast } from './ui';

const NAV = [
  { label: 'MY WORKSPACE', items: [
    { to: '/doctor', label: 'Dashboard', icon: LayoutDashboard, end: true },
    { to: '/doctor/patients', label: 'My patients', icon: Users },
    { to: '/doctor/cases', label: 'My cases', icon: ClipboardList },
  ] },
  { label: 'ACCOUNT', items: [
    { to: '/doctor/profile', label: 'My profile', icon: User },
    { to: '/doctor/settings', label: 'Settings & security', icon: Settings },
  ] },
  { label: 'AUDIT TOOLS', items: [{ to: '/overview', label: 'Audit workspace', icon: Stethoscope }] },
];

export default function DoctorPortal() {
  return <PortalLayout nav={NAV} area="Doctor workspace"><Routes>
    <Route index element={<DoctorDashboard />} />
    <Route path="patients" element={<MyPatients />} />
    <Route path="patients/:patientId" element={<PatientDetail />} />
    <Route path="cases" element={<MyCases />} />
    <Route path="cases/:caseId" element={<CaseView />} />
    <Route path="profile" element={<MyProfile />} />
    <Route path="settings" element={<MySettings />} />
    <Route path="*" element={<EmptyState icon={AlertTriangle} title="Page not found" />} />
  </Routes></PortalLayout>;
}

const caseStatus = c => <StatusPill value={c.status} label={String(c.status).replaceAll('_', ' ')} />;

function DoctorDashboard() {
  const { loading, data, error, reload } = useLoad(() => doctor.dashboard());
  if (loading) return <Loading />;
  if (error) return <Panel><Alert>{error.message}</Alert><Button icon={RefreshCw} onClick={reload}>Retry</Button></Panel>;
  const d = data.doctor;
  return <>
    <Heading eyebrow={`${d.doctor_id} · ${d.department || ''}${d.designation ? ' · ' + d.designation : ''}`} title={`Welcome, ${data.welcome_name}`}
      description="Your private workspace. Figures include only patients and cases you own or that were shared with you."
      action={<Link className="button primary" to="/doctor/patients?new=1"><Plus size={15} /> Add patient</Link>} />
    <div className="stats-grid">
      <Stat label="My patients" value={data.total_patients} icon={Users} />
      <Stat label="Active audit cases" value={data.active_audit_cases} icon={ClipboardList} tone="blue" />
      <Stat label="Findings awaiting review" value={data.pending_finding_reviews} change="Preliminary — require verification" icon={AlertTriangle} tone="amber" />
      <Stat label="Reviews assigned to me" value={data.pending_reviews_assigned} icon={ShieldCheck} tone="teal" />
    </div>
    <div className="portal-grid">
      <Panel title="Recent patient activity" action={<Link className="text-button" to="/doctor/patients">All patients</Link>}>
        {data.recent_patient_activity.length ? <ul className="activity-list">{data.recent_patient_activity.map(p => <li key={p.id}><Link to={`/doctor/patients/${p.id}`}><b>{p.patient_ref}</b><small>{p.display_name || 'Name not recorded'} · {p.case_count} case(s)</small></Link><span>{fmtDate(p.updated_at)}</span></li>)}</ul>
          : <EmptyState icon={Users} title="No patients yet">Add a patient to start an audit.</EmptyState>}
      </Panel>
      <Panel title="Recently uploaded documents">
        {data.recent_documents.length ? <ul className="activity-list">{data.recent_documents.map(x => <li key={x.document_id}><Link to={`/doctor/cases/${x.case_id}`}><b>{x.filename}</b><small>{x.page_count ?? '?'} pages · {x.status}</small></Link><span>{fmtDate(x.uploaded_at)}</span></li>)}</ul>
          : <EmptyState icon={FileText} title="No documents uploaded" />}
      </Panel>
    </div>
    <Panel title="Recent AI-generated reports" subtitle="Preliminary until reviewed by the mortality audit committee">
      {data.recent_reports.length ? <ul className="activity-list">{data.recent_reports.map(r => <li key={r.report_id}><Link to={`/doctor/cases/${r.case_id}`}><b>{r.case_id}</b><small>Report v{r.version} · review {r.review_status.replaceAll('_', ' ')}</small></Link><span>{fmtDate(r.generated_at)}</span></li>)}</ul>
        : <EmptyState icon={FileText} title="No reports yet" />}
    </Panel>
  </>;
}

// ---------- patients ----------
function MyPatients() {
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState('');
  const [query, setQuery] = useState({ page: 1, scope: 'all', case_status: '' });
  const [adding, setAdding] = useState(params.get('new') === '1');
  const { loading, data, error, reload } = useLoad(() => doctor.patients({ ...query, q: q || undefined, case_status: query.case_status || undefined, page_size: 15 }), [JSON.stringify(query), q]);
  return <>
    <Heading eyebrow="MY WORKSPACE" title="My patients" description="Patients you own or that colleagues explicitly shared with you."
      action={<Button variant="primary" icon={Plus} onClick={() => setAdding(true)}>Add patient</Button>} />
    <Panel>
      <div className="filter-row portal-filters">
        <div className="tabs">{[['all', 'All'], ['owned', 'Owned by me'], ['shared', 'Shared with me']].map(([k, l]) => <button key={k} className={`tab ${query.scope === k ? 'active' : ''}`} onClick={() => setQuery({ ...query, scope: k, page: 1 })}>{l}</button>)}</div>
        <div className="filter-controls">
          <label className="global-search"><Search size={15} /><input value={q} onChange={e => setQ(e.target.value)} placeholder="Search reference or department" aria-label="Search patients" /></label>
          <select value={query.case_status} onChange={e => setQuery({ ...query, case_status: e.target.value, page: 1 })} aria-label="Filter by case status">
            <option value="">Any case status</option>{['processing', 'pending_review', 'under_review', 'committee_review', 'approved', 'closed', 'failed'].map(s => <option key={s} value={s}>{s.replaceAll('_', ' ')}</option>)}</select>
        </div>
      </div>
      {loading && !data ? <Loading /> : error ? <Alert>{error.message}</Alert> : !data.items.length ? <EmptyState icon={Users} title="No patients found" /> :
        <div className="table-scroll"><table className="data-table responsive"><thead><tr><th>Reference</th><th>Name</th><th>Age / sex</th><th>Department</th><th>Cases</th><th>Latest case</th><th>Access</th><th>Updated</th></tr></thead>
          <tbody>{data.items.map(p => <tr key={p.id}><td data-label="Reference"><Link className="id-link" to={`/doctor/patients/${p.id}`}>{p.patient_ref}</Link></td><td data-label="Name">{p.display_name || '—'}</td>
            <td data-label="Age / sex">{p.age_years ?? '—'} / {p.gender || '—'}</td><td data-label="Department">{p.department || '—'}</td><td data-label="Cases">{p.case_count}</td>
            <td data-label="Latest case">{p.latest_case_status ? <StatusPill value={p.latest_case_status} label={p.latest_case_status.replaceAll('_', ' ')} /> : '—'}</td>
            <td data-label="Access"><StatusPill value={p.access} label={p.access === 'owner' ? 'Owner' : `Shared · ${p.access}`} /></td><td data-label="Updated">{fmtDay(p.updated_at)}</td></tr>)}</tbody></table></div>}
      <Pagination pagination={data?.pagination} onPage={n => setQuery({ ...query, page: n })} />
    </Panel>
    <PatientFormModal open={adding} onClose={() => { setAdding(false); if (params.get('new')) setParams({}); }} onSaved={reload} />
  </>;
}

function PatientFormModal({ open, onClose, onSaved, patient }) {
  const navigate = useNavigate();
  const toast = useToast();
  const blank = { patient_ref: '', display_name: '', age_years: '', gender: '', department: '', notes: '' };
  const [form, setForm] = useState(blank);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (open) { setForm(patient ? { ...blank, ...Object.fromEntries(Object.keys(blank).map(k => [k, patient[k] ?? ''])) } : blank); setError(null); } }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  const fe = fieldErrors(error);
  const set = k => e => setForm({ ...form, [k]: e.target.value });
  const save = async () => {
    setBusy(true); setError(null);
    const body = Object.fromEntries(Object.entries(form).map(([k, v]) => [k, v === '' ? null : k === 'age_years' ? Number(v) : v]));
    try {
      if (patient) { delete body.patient_ref; await doctor.updatePatient(patient.id, body); toast('Patient updated'); onSaved?.(); onClose(); }
      else { const r = await doctor.createPatient(body); toast('Patient added'); onClose(); navigate(`/doctor/patients/${r.patient.id}`); }
    } catch (e) { setError(e); } finally { setBusy(false); }
  };
  return <Modal open={open} title={patient ? 'Edit patient' : 'Add patient'} onClose={busy ? undefined : onClose}
    footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" onClick={save} busy={busy} disabled={!form.patient_ref}>Save</Button></>}>
    <div className="form-grid two">
      <Field label="Hospital reference (MRN / UHID)" error={fe.patient_ref}><input value={form.patient_ref} onChange={set('patient_ref')} disabled={!!patient} /></Field>
      <Field label="Name (optional)" hint="Stored encrypted"><input value={form.display_name} onChange={set('display_name')} /></Field>
      <Field label="Age" error={fe.age_years}><input type="number" min={0} max={130} value={form.age_years} onChange={set('age_years')} /></Field>
      <Field label="Sex"><select value={form.gender} onChange={set('gender')}><option value="">Not recorded</option><option value="female">Female</option><option value="male">Male</option><option value="other">Other</option><option value="unknown">Unknown</option></select></Field>
      <Field label="Department"><input value={form.department} onChange={set('department')} placeholder="Defaults to your department" /></Field>
    </div>
    <Field label="Notes (optional)" hint="Stored encrypted"><textarea rows={3} value={form.notes} onChange={set('notes')} /></Field>
    {error && !Object.keys(fe).length && <Alert>{error.message}</Alert>}
  </Modal>;
}

function PatientDetail() {
  const { patientId } = useParams();
  const { loading, data, error, reload } = useLoad(() => doctor.patient(patientId), [patientId]);
  const [editing, setEditing] = useState(false);
  const [sharing, setSharing] = useState(false);
  const [unshare, setUnshare] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState(null);
  const toast = useToast();
  const navigate = useNavigate();
  if (loading && !data) return <Loading />;
  if (error) return <Panel><Alert>{error.status === 404 ? 'This patient does not exist or is not shared with you.' : error.message}</Alert><Link to="/doctor/patients" className="button">Back to my patients</Link></Panel>;
  const p = data.patient;
  const canWrite = p.access === 'owner' || p.access === 'contribute';
  const upload = async e => {
    const file = e.target.files?.[0]; e.target.value = ''; if (!file) return;
    setUploading(true); setUploadError(null);
    try { const r = await doctor.upload(p.id, file); toast('Upload accepted — processing has started'); navigate(`/doctor/cases/${r.case_id}`); }
    catch (err) { setUploadError(err); } finally { setUploading(false); }
  };
  return <>
    <Heading eyebrow={`PATIENT · ${p.access === 'owner' ? 'OWNED BY YOU' : 'SHARED WITH YOU (' + p.access.toUpperCase() + ')'}`} title={p.patient_ref}
      description={`${p.display_name || 'Name not recorded'} · ${p.age_years ?? '?'} years · ${p.gender || 'sex not recorded'} · ${p.department || 'no department'}`}
      action={<div className="button-row">{canWrite && <Button icon={FolderOpen} onClick={() => setEditing(true)}>Edit</Button>}
        {p.access === 'owner' && <Button icon={Share2} onClick={() => setSharing(true)}>Share</Button>}
        {canWrite && <label className={`button primary ${uploading ? 'disabled' : ''}`}><Upload size={15} />{uploading ? 'Uploading…' : 'Upload case sheet'}<input type="file" accept=".pdf,.zip,application/pdf,application/zip" hidden onChange={upload} disabled={uploading} /></label>}</div>} />
    {uploadError && <Alert>{uploadError.message}{uploadError.details?.length ? ` — ${uploadError.details.map(x => x.issue).join('; ')}` : ''}</Alert>}
    <Panel title="Analysis history" subtitle="Audit cases created from this patient's documents">
      {data.cases.length ? <CaseTable cases={data.cases} /> : <EmptyState icon={FileText} title="No case sheets uploaded yet">Upload a PDF or ZIP of scanned pages to start an audit.</EmptyState>}
    </Panel>
    <div className="portal-grid">
      <Panel title="Notes"><p className="modal-text">{p.notes || 'No notes recorded.'}</p></Panel>
      {p.access === 'owner' && <Panel title="Shared with" subtitle="Colleagues with explicit access">
        {data.shared_with.length ? <ul className="activity-list">{data.shared_with.map(s => <li key={s.doctor_id}><div><b>{s.full_name}</b><small>{s.doctor_id} · {s.access_level}</small></div><Button className="compact" onClick={() => setUnshare(s)}>Revoke</Button></li>)}</ul>
          : <EmptyState icon={Lock} title="Private — not shared" />}
      </Panel>}
    </div>
    <PatientFormModal open={editing} patient={p} onClose={() => setEditing(false)} onSaved={reload} />
    <ShareModal open={sharing} patient={p} onClose={() => setSharing(false)} onSaved={reload} />
    <ConfirmDialog open={!!unshare} title="Revoke access?" message={`${unshare?.full_name} will immediately lose access to this patient and its cases.`} confirmLabel="Revoke access" danger
      onConfirm={async () => { await doctor.unshare(p.id, unshare.doctor_id); toast('Access revoked'); reload(); }} onClose={() => setUnshare(null)} />
  </>;
}

function ShareModal({ open, patient, onClose, onSaved }) {
  const [doctorId, setDoctorId] = useState('');
  const [level, setLevel] = useState('read');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  useEffect(() => { if (open) { setDoctorId(''); setLevel('read'); setError(null); } }, [open]);
  const save = async () => { setBusy(true); setError(null); try { const r = await doctor.share(patient.id, { doctor_id: doctorId, access_level: level }); toast(`Shared with ${r.shared_with.full_name}`); onSaved(); onClose(); } catch (e) { setError(e); } finally { setBusy(false); } };
  return <Modal open={open} title="Share patient with a colleague" onClose={busy ? undefined : onClose}
    footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" onClick={save} busy={busy} disabled={!doctorId}>Share</Button></>}>
    <p className="modal-text">Sharing gives another doctor access to this patient and all of its audit cases. Every share is recorded in the audit log.</p>
    <div className="form-grid two">
      <Field label="Colleague's Doctor ID"><input value={doctorId} onChange={e => setDoctorId(e.target.value)} /></Field>
      <Field label="Access level"><select value={level} onChange={e => setLevel(e.target.value)}><option value="read">Read only</option><option value="contribute">Read and upload</option></select></Field>
    </div>
    {error && <Alert>{error.message}</Alert>}
  </Modal>;
}

function CaseTable({ cases }) {
  return <div className="table-scroll"><table className="data-table responsive"><thead><tr><th>Case ID</th><th>Patient ref</th><th>Case status</th><th>Document processing</th><th>Audit review</th><th>Last updated</th><th /></tr></thead>
    <tbody>{cases.map(c => <tr key={c.case_id}><td data-label="Case ID"><Link className="id-link" to={`/doctor/cases/${c.case_id}`}>{c.case_id}</Link></td><td data-label="Patient ref">{c.patient_ref || '—'}</td>
      <td data-label="Case status">{caseStatus(c)}</td><td data-label="Processing">{c.document_status ? <StatusPill value={c.document_status} label={`${c.document_status}${c.progress_percent != null && c.document_status !== 'completed' ? ` · ${Math.round(c.progress_percent)}%` : ''}`} /> : '—'}</td>
      <td data-label="Review"><StatusPill value={c.review_status} label={c.review_status.replaceAll('_', ' ')} /></td><td data-label="Updated">{fmtDate(c.updated_at)}</td>
      <td><Link className="button compact" to={`/doctor/cases/${c.case_id}`}>Open</Link></td></tr>)}</tbody></table></div>;
}

function MyCases() {
  const [query, setQuery] = useState({ page: 1, status: '', review_status: '' });
  const [q, setQ] = useState('');
  const { loading, data, error } = useLoad(() => doctor.cases({ page: query.page, page_size: 15, status: query.status || undefined, review_status: query.review_status || undefined, q: q || undefined }), [JSON.stringify(query), q]);
  return <>
    <Heading eyebrow="MY WORKSPACE" title="My cases" description="Audit cases you uploaded, were assigned, or can access through a shared patient." />
    <Panel>
      <div className="filter-row portal-filters"><div className="filter-controls">
        <label className="global-search"><Search size={15} /><input value={q} onChange={e => setQ(e.target.value)} placeholder="Search case ID or patient ref" aria-label="Search cases" /></label>
        <select value={query.status} onChange={e => setQuery({ ...query, status: e.target.value, page: 1 })} aria-label="Case status"><option value="">Any case status</option>{['processing', 'pending_review', 'under_review', 'committee_review', 'approved', 'closed', 'failed'].map(s => <option key={s} value={s}>{s.replaceAll('_', ' ')}</option>)}</select>
        <select value={query.review_status} onChange={e => setQuery({ ...query, review_status: e.target.value, page: 1 })} aria-label="Review status"><option value="">Any review status</option>{['pending', 'under_review', 'committee_review', 'approved'].map(s => <option key={s} value={s}>{s.replaceAll('_', ' ')}</option>)}</select>
      </div></div>
      {loading && !data ? <Loading /> : error ? <Alert>{error.message}</Alert> : !data.items.length ? <EmptyState icon={ClipboardList} title="No cases found" /> : <CaseTable cases={data.items} />}
      <Pagination pagination={data?.pagination} onPage={n => setQuery({ ...query, page: n })} />
    </Panel>
  </>;
}

// Case view backed by the real API: processing status, findings, timeline and the report.
function CaseView() {
  const { caseId } = useParams();
  const toast = useToast();
  const detail = useLoad(() => clinical.caseDetail(caseId), [caseId]);
  const [status, setStatus] = useState(null);
  const [tab, setTab] = useState('findings');
  useEffect(() => {
    let alive = true; let delay = 1500; let timer;
    const poll = async () => {
      try { const s = await clinical.status(caseId); if (!alive) return; setStatus(s);
        if (s.status && !['completed', 'failed', 'cancelled'].includes(s.status)) { delay = Math.min(delay * 1.4, 10000); timer = setTimeout(poll, delay); }
        else if (s.status === 'completed') detail.reload();
      } catch { /* surfaced by detail */ }
    };
    poll();
    return () => { alive = false; clearTimeout(timer); };
  }, [caseId]); // eslint-disable-line react-hooks/exhaustive-deps
  if (detail.loading && !detail.data) return <Loading />;
  if (detail.error) return <Panel><Alert>{detail.error.status === 404 ? 'This case does not exist or you do not have access to it.' : detail.error.message}</Alert><Link to="/doctor/cases" className="button">Back to my cases</Link></Panel>;
  const c = detail.data.case;
  const done = status?.status === 'completed';
  const pdf = async () => { try { downloadBlob(await clinical.reportPdf(caseId), `${caseId}-audit-report.pdf`); } catch (e) { toast(e.status === 404 ? 'No report generated yet' : e.message, 'error'); } };
  return <>
    <Heading eyebrow={`AUDIT CASE · ${c.patient_ref || 'no patient ref'}`} title={c.id}
      description={`${c.department || 'Department not documented'} · admitted ${fmtDate(c.admitted_at)} · outcome ${fmtDate(c.outcome_at)}`}
      action={<div className="button-row"><Button icon={Download} onClick={pdf} disabled={!done}>Download report (PDF)</Button></div>} />
    <Panel title="Document processing" subtitle={status ? `${status.status || 'not started'} · ${status.progress_percent ?? 0}%` : 'Checking status…'}>
      {status && <><div className="job-progress"><i style={{ width: `${status.progress_percent || 0}%` }} /></div>
        <div className="job-stage-list">{status.stages.map(s => <span key={s.key} className={`job-stage ${s.status}`}>{s.label}</span>)}</div>
        {status.error && <Alert>{status.error.message}</Alert>}
        {status.warnings?.length > 0 && <ul className="warning-list">{status.warnings.map(w => <li key={w}>{w}</li>)}</ul>}</>}
    </Panel>
    {done && <Panel>
      <div className="tabs">{[['findings', 'Preliminary findings'], ['timeline', 'Clinical timeline']].map(([k, l]) => <button key={k} className={`tab ${tab === k ? 'active' : ''}`} onClick={() => setTab(k)}>{l}</button>)}</div>
      {tab === 'findings' ? <FindingsList caseId={caseId} /> : <TimelineList caseId={caseId} />}
    </Panel>}
  </>;
}

function FindingsList({ caseId }) {
  const { loading, data, error } = useLoad(() => clinical.findings(caseId, { page_size: 50 }), [caseId]);
  if (loading) return <Loading />;
  if (error) return <Alert>{error.message}</Alert>;
  if (!data.items.length) return <EmptyState icon={ShieldCheck} title="No findings generated by the configured rules" />;
  return <div className="pf-finding-list">{data.items.map(f => <article key={f.id} className="pf-finding-item">
    <div className="pf-finding-top"><StatusPill value={f.severity} /><b>{f.title}</b><StatusPill value={f.review_status} label={`review: ${f.review_status}`} /></div>
    <p>{f.description}</p>
    <small>{f.category.replaceAll('_', ' ')} · evidence {f.verification_status} · pages {[...new Set(f.evidence.map(e => e.page_number).filter(Boolean))].join(', ') || '—'} · rule {f.rule.rule_id}</small>
  </article>)}<p className="muted-note">Findings are preliminary decision-support output and require qualified human review.</p></div>;
}

function TimelineList({ caseId }) {
  const { loading, data, error } = useLoad(() => clinical.timeline(caseId, { page_size: 100 }), [caseId]);
  if (loading) return <Loading />;
  if (error) return <Alert>{error.message}</Alert>;
  return <ol className="pf-timeline">{data.items.filter(e => e.type !== 'vital').map(e => <li key={e.id}>
    <time>{e.occurred_at ? fmtDate(e.occurred_at) : 'Time not documented'}</time><div><b>{e.title}</b><span>{e.description}</span><small>Page {e.source_page} · {e.source_status}</small></div></li>)}</ol>;
}

// ---------- profile & settings ----------
function MyProfile() {
  const { loading, data, error, reload } = useLoad(() => doctor.profile());
  const [form, setForm] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const toast = useToast();
  useEffect(() => { if (data) setForm({ phone: data.profile.phone || '', contact_info: data.profile.contact_info || '' }); }, [data]);
  if (loading || !form) return error ? <Alert>{error.message}</Alert> : <Loading />;
  const p = data.profile;
  const save = async e => { e.preventDefault(); setBusy(true); setErr(null); try { await doctor.updateProfile({ phone: form.phone || null, contact_info: form.contact_info || null }); toast('Profile updated'); reload(); } catch (x) { setErr(x); } finally { setBusy(false); } };
  const fe = fieldErrors(err);
  return <>
    <Heading eyebrow="ACCOUNT" title="My profile" description="Identity details are managed by your hospital administrator." />
    <div className="settings-grid">
      <Panel title="Hospital record" subtitle="Read-only">
        <dl className="detail-list"><dt>Doctor ID</dt><dd><code>{p.doctor_id}</code></dd><dt>Name</dt><dd>{p.full_name}</dd><dt>Hospital email</dt><dd>{p.email}</dd>
          <dt>Department</dt><dd>{p.department || '—'}</dd><dt>Designation</dt><dd>{p.designation || '—'}</dd><dt>Activated</dt><dd>{fmtDate(p.activated_at)}</dd></dl>
        <p className="muted-note">To correct these details, contact your administrator.</p>
      </Panel>
      <Panel title="Contact information" subtitle="You can update these">
        <form onSubmit={save} className="portal-form">
          <Field label="Phone" error={fe.phone}><input value={form.phone} onChange={e => setForm({ ...form, phone: e.target.value })} inputMode="tel" /></Field>
          <Field label="Other contact details" error={fe.contact_info} hint="e.g. pager or extension"><textarea rows={3} value={form.contact_info} onChange={e => setForm({ ...form, contact_info: e.target.value })} maxLength={500} /></Field>
          {err && !Object.keys(fe).length && <Alert>{err.message}</Alert>}
          <div className="form-actions"><Button variant="primary" type="submit" busy={busy}>Save contact details</Button></div>
        </form>
      </Panel>
    </div>
  </>;
}

function MySettings() {
  const { setPreferences, logout } = useAuth();
  const navigate = useNavigate();
  const toast = useToast();
  const prefs = useLoad(() => doctor.preferences());
  const sessions = useLoad(() => auth.sessions());
  const activity = useLoad(() => auth.activity({ page_size: 10 }));
  const [pw, setPw] = useState({ current_password: '', new_password: '', confirm_password: '' });
  const [pwErr, setPwErr] = useState(null);
  const [pwBusy, setPwBusy] = useState(false);
  const [revokeOthers, setRevokeOthers] = useState(false);
  const savePref = async patch => {
    try { const r = await doctor.updatePreferences(patch); setPreferences(r.preferences); if (patch.theme) applyTheme(patch.theme); prefs.reload(); toast('Preferences saved'); }
    catch (e) { toast(e.message, 'error'); }
  };
  const changePassword = async e => {
    e.preventDefault(); setPwBusy(true); setPwErr(null);
    try { const r = await auth.changePassword(pw); setPw({ current_password: '', new_password: '', confirm_password: '' }); toast(`Password changed${r.other_sessions_revoked ? ` · ${r.other_sessions_revoked} other session(s) signed out` : ''}`); sessions.reload(); }
    catch (x) { setPwErr(x); } finally { setPwBusy(false); }
  };
  const endSession = async s => {
    try { await auth.revokeSession(s.id); if (s.current) { await logout(); navigate('/login', { replace: true }); } else { toast('Session ended'); sessions.reload(); } }
    catch (e) { toast(e.message, 'error'); }
  };
  const p = prefs.data?.preferences;
  const mismatch = pw.confirm_password && pw.new_password !== pw.confirm_password;
  return <>
    <Heading eyebrow="ACCOUNT" title="Settings & security" description="Personal preferences, password and signed-in devices." />
    <div className="settings-grid">
      <Panel title="Preferences" subtitle="Saved to your account">
        {!p ? <Loading /> : <>
          <div className="setting-row"><div><b>Theme</b><small>Display mode for MedAudit</small></div><select value={p.theme} onChange={e => savePref({ theme: e.target.value })}><option value="system">Follow system</option><option value="light">Light</option><option value="dark">Dark</option></select></div>
          <div className="setting-row"><div><b>Language</b><small>Additional languages are not yet available</small></div><select value={p.language} onChange={e => savePref({ language: e.target.value })}><option value="en">English</option></select></div>
          <div className="setting-row"><div><b>Default case filter</b><small>Dashboard preference</small></div><select value={p.dashboard_preferences.default_case_filter || 'all'} onChange={e => savePref({ dashboard_preferences: { default_case_filter: e.target.value } })}><option value="all">All cases</option><option value="pending_review">Pending review</option><option value="under_review">Under review</option></select></div>
        </>}
      </Panel>
      <Panel title="Notifications" subtitle="In-app notification preferences">
        {!p ? <Loading /> : [['case_processed', 'Case processing finished'], ['review_assigned', 'A review is assigned to me'], ['security_alerts', 'Security alerts for my account']].map(([k, l]) =>
          <label key={k} className="setting-row check-row"><div><b>{l}</b></div><input type="checkbox" checked={!!p.notification_preferences[k]} onChange={e => savePref({ notification_preferences: { [k]: e.target.checked } })} /></label>)}
        <p className="muted-note"><Bell size={12} /> Email delivery requires a configured mail service; preferences are stored now.</p>
      </Panel>
      <Panel title="Change password" subtitle="Other devices are signed out after a change">
        <form onSubmit={changePassword} className="portal-form">
          <Field label="Current password"><input type="password" autoComplete="current-password" value={pw.current_password} onChange={e => setPw({ ...pw, current_password: e.target.value })} /></Field>
          <Field label="New password" hint="At least 12 characters with three character types"><input type="password" autoComplete="new-password" value={pw.new_password} onChange={e => setPw({ ...pw, new_password: e.target.value })} /></Field>
          <Field label="Confirm new password" error={mismatch ? 'Passwords do not match' : null}><input type="password" autoComplete="new-password" value={pw.confirm_password} onChange={e => setPw({ ...pw, confirm_password: e.target.value })} /></Field>
          {pwErr && <Alert>{pwErr.details?.length ? pwErr.details.map(d => d.issue).join('. ') : pwErr.message}</Alert>}
          <div className="form-actions"><Button variant="primary" type="submit" icon={KeyRound} busy={pwBusy} disabled={!pw.current_password || !pw.new_password || mismatch}>Change password</Button></div>
        </form>
      </Panel>
      <Panel title="Two-factor authentication" subtitle="Not configured for this hospital">
        <p className="modal-text">Two-factor authentication is not yet enabled on this MedAudit installation. Ask your administrator about hospital SSO or MFA integration.</p>
      </Panel>
    </div>
    <Panel title="Signed-in devices" subtitle="Active sessions for your account" action={sessions.data?.items.length > 1 && <Button icon={Monitor} onClick={() => setRevokeOthers(true)}>Sign out other devices</Button>}>
      {sessions.loading ? <Loading /> : sessions.error ? <Alert>{sessions.error.message}</Alert> : <SessionTable sessions={sessions.data.items} onRevoke={endSession} />}
    </Panel>
    <Panel title="Recent sign-in activity">
      {activity.loading ? <Loading /> : activity.error ? <Alert>{activity.error.message}</Alert> : !activity.data.items.length ? <EmptyState icon={Activity} title="No activity recorded" /> :
        <ul className="activity-list">{activity.data.items.map(a => <li key={a.event_id}><div><b>{a.event_type.replace('auth.', '').replaceAll('_', ' ')}</b><small>{Object.entries(a.details || {}).map(([k, v]) => `${k}: ${v}`).join(' · ')}</small></div><span><StatusPill value={a.outcome} /> {fmtDate(a.occurred_at)}</span></li>)}</ul>}
    </Panel>
    <ConfirmDialog open={revokeOthers} title="Sign out other devices?" message="Every other session for your account will end immediately." confirmLabel="Sign out others"
      onConfirm={async () => { const r = await auth.revokeOthers(); toast(`${r.revoked} session(s) ended`); sessions.reload(); }} onClose={() => setRevokeOthers(false)} />
  </>;
}
