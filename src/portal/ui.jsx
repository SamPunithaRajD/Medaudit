// UI primitives matching the existing MedAudit design system (same class names and CSS tokens as App.jsx).
import React, { createContext, useCallback, useContext, useEffect, useId, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { AlertTriangle, Check, ChevronLeft, ChevronRight, Copy, Loader2, X } from 'lucide-react';

export function Heading({ eyebrow, title, description, action }) {
  return <div className="page-heading"><div>{eyebrow && <div className="eyebrow">{eyebrow}</div>}<h1>{title}</h1>{description && <p>{description}</p>}</div>{action && <div className="heading-action">{action}</div>}</div>;
}

export function Button({ children, variant = 'secondary', icon: Icon, busy, className = '', ...rest }) {
  return <button className={`button ${variant} ${className}`} disabled={busy || rest.disabled} {...rest}>
    {busy ? <Loader2 size={15} className="spin" /> : Icon && <Icon size={15} />}{children}</button>;
}

export function Panel({ title, subtitle, action, children, className = '' }) {
  return <section className={`panel ${className}`}>{(title || action) && <div className="panel-header"><div>{title && <h3>{title}</h3>}{subtitle && <p>{subtitle}</p>}</div>{action}</div>}{children}</section>;
}

export function Stat({ label, value, change, icon: Icon, tone = 'rose' }) {
  return <div className="stat-card"><div className={`stat-icon ${tone}`}><Icon size={18} /></div><div className="stat-value">{value ?? '—'}</div><div className="stat-label">{label}</div>{change && <div className="stat-change">{change}</div>}</div>;
}

const STATUS_LABEL = { pending_activation: 'Pending activation', active: 'Active', suspended: 'Suspended', deactivated: 'Deactivated',
  archived: 'Archived', activated: 'Activated', pending: 'Pending', code_expired: 'Code expired', no_active_code: 'No active code',
  success: 'Success', failure: 'Failure', denied: 'Denied' };
export function StatusPill({ value, label }) {
  const k = String(value || 'unknown').toLowerCase().replaceAll('_', '-');
  return <span className={`status-pill acct-${k}`}>{label || STATUS_LABEL[value] || String(value || '—').replaceAll('_', ' ')}</span>;
}

export function Field({ label, error, hint, children, id: idProp }) {
  const gen = useId();
  const id = idProp || gen;
  const child = React.Children.only(children);
  return <div className={`form-field ${error ? 'has-error' : ''}`}>
    <label htmlFor={id}>{label}</label>
    {React.cloneElement(child, { id, 'aria-invalid': !!error, 'aria-describedby': error || hint ? `${id}-msg` : undefined })}
    {(error || hint) && <small id={`${id}-msg`} className={error ? 'field-error' : 'field-hint'}>{error || hint}</small>}
  </div>;
}

export function Alert({ tone = 'error', children }) {
  return <div className={`portal-alert ${tone}`} role={tone === 'error' ? 'alert' : 'status'}>{tone === 'error' ? <AlertTriangle size={15} /> : <Check size={15} />}<span>{children}</span></div>;
}

export function EmptyState({ icon: Icon, title, children }) {
  return <div className="empty-state">{Icon && <Icon size={22} />}<b>{title}</b>{children && <span>{children}</span>}</div>;
}

export function Loading({ label = 'Loading…' }) {
  return <div className="portal-loading" role="status"><Loader2 size={16} className="spin" />{label}</div>;
}

export function Modal({ open, title, onClose, children, footer, wide, dismissible = true }) {
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const prev = document.activeElement;
    const t = setTimeout(() => ref.current?.querySelector('input,select,textarea,button')?.focus(), 30);
    const onKey = e => { if (e.key === 'Escape' && dismissible) onClose?.(); };
    window.addEventListener('keydown', onKey);
    return () => { clearTimeout(t); window.removeEventListener('keydown', onKey); prev?.focus?.(); };
  }, [open, onClose, dismissible]);
  return <AnimatePresence>{open && <motion.div className="modal-scrim" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onMouseDown={e => dismissible && e.target === e.currentTarget && onClose?.()}>
    <motion.div ref={ref} className={`modal-card ${wide ? 'wide' : ''}`} role="dialog" aria-modal="true" aria-label={title}
      initial={{ opacity: 0, y: 14, scale: .98 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: 8 }} transition={{ duration: .16 }}>
      <div className="modal-head"><h3>{title}</h3>{onClose && dismissible && <button className="icon-button" onClick={onClose} aria-label="Close"><X size={17} /></button>}</div>
      <div className="modal-body">{children}</div>{footer && <div className="modal-foot">{footer}</div>}
    </motion.div></motion.div>}</AnimatePresence>;
}

// Confirmation for destructive / security-sensitive actions. Optional reason and type-to-confirm.
export function ConfirmDialog({ open, title, message, confirmLabel = 'Confirm', danger, requireReason, typeToConfirm, onConfirm, onClose }) {
  const [reason, setReason] = useState('');
  const [typed, setTyped] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { if (open) { setReason(''); setTyped(''); setError(''); } }, [open]);
  const ok = (!requireReason || reason.trim().length >= 3) && (!typeToConfirm || typed.trim().toUpperCase() === typeToConfirm.toUpperCase());
  const run = async () => { setBusy(true); setError(''); try { await onConfirm({ reason: reason.trim(), typed: typed.trim() }); onClose(); } catch (e) { setError(e.message); } finally { setBusy(false); } };
  return <Modal open={open} title={title} onClose={busy ? undefined : onClose} footer={<>
    <Button onClick={onClose} disabled={busy}>Cancel</Button>
    <Button variant={danger ? 'danger' : 'primary'} onClick={run} disabled={!ok} busy={busy}>{confirmLabel}</Button></>}>
    <p className="modal-text">{message}</p>
    {requireReason && <Field label="Reason (recorded in the audit log)"><textarea rows={3} value={reason} onChange={e => setReason(e.target.value)} maxLength={500} /></Field>}
    {typeToConfirm && <Field label={`Type ${typeToConfirm} to confirm`}><input value={typed} onChange={e => setTyped(e.target.value)} autoComplete="off" /></Field>}
    {error && <Alert>{error}</Alert>}
  </Modal>;
}

// One-time secret display (activation / reset codes). Never stored by the client.
export function SecretReveal({ open, title, intro, secret, rows = [], onClose }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => { try { await navigator.clipboard.writeText(secret); setCopied(true); setTimeout(() => setCopied(false), 2000); } catch { /* clipboard blocked */ } };
  return <Modal open={open} title={title} onClose={onClose} dismissible={false} footer={<Button variant="primary" onClick={onClose}>I have recorded it</Button>}>
    <p className="modal-text">{intro}</p>
    <div className="secret-box"><code>{secret}</code><button className="icon-button" onClick={copy} aria-label="Copy code">{copied ? <Check size={16} /> : <Copy size={16} />}</button></div>
    <dl className="detail-list">{rows.map(([k, v]) => <React.Fragment key={k}><dt>{k}</dt><dd>{v}</dd></React.Fragment>)}</dl>
    <Alert tone="info">This code is shown only once and is not stored in readable form. Deliver it through a verified hospital channel.</Alert>
  </Modal>;
}

export function Pagination({ pagination, onPage }) {
  if (!pagination || pagination.total_pages <= 1) return null;
  return <div className="pager"><span>Page {pagination.page} of {pagination.total_pages} · {pagination.total_items} records</span>
    <div><button className="icon-button" disabled={!pagination.previous_page} onClick={() => onPage(pagination.previous_page)} aria-label="Previous page"><ChevronLeft size={16} /></button>
      <button className="icon-button" disabled={!pagination.next_page} onClick={() => onPage(pagination.next_page)} aria-label="Next page"><ChevronRight size={16} /></button></div></div>;
}

const ToastCtx = createContext(() => {});
export const useToast = () => useContext(ToastCtx);
export function ToastProvider({ children }) {
  const [toast, setToast] = useState(null);
  const show = useCallback((message, tone = 'success') => setToast({ message, tone, id: Date.now() }), []);
  useEffect(() => { if (!toast) return undefined; const t = setTimeout(() => setToast(null), 4200); return () => clearTimeout(t); }, [toast]);
  return <ToastCtx.Provider value={show}>{children}<AnimatePresence>{toast && <motion.div key={toast.id} className={`toast ${toast.tone}`} role="status" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 8 }}>{toast.tone === 'error' ? <AlertTriangle size={16} /> : <Check size={16} />}{toast.message}</motion.div>}</AnimatePresence></ToastCtx.Provider>;
}

export const fmtDate = v => v ? new Date(v).toLocaleString(undefined, { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—';
export const fmtDay = v => v ? new Date(v).toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric' }) : '—';

// Small data hook: load(fn) with loading/error state and manual reload.
export function useLoad(fn, deps = []) {
  const [state, setState] = useState({ loading: true, data: null, error: null });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    setState(s => ({ ...s, loading: true, error: null }));
    fn().then(data => alive && setState({ loading: false, data, error: null }))
      .catch(error => alive && setState({ loading: false, data: null, error }));
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { ...state, reload: () => setTick(t => t + 1) };
}
