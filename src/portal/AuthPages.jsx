import React, { useState } from 'react';
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { motion } from 'framer-motion';
import { Activity, ArrowRight, Eye, EyeOff, KeyRound, LockKeyhole, ShieldAlert, ShieldCheck, UserRoundCheck } from 'lucide-react';
import { auth, fieldErrors } from './api';
import { Splash, useAuth } from './AuthContext';
import { Alert, Button, Field } from './ui';

function AuthShell({ eyebrow, title, subtitle, children, footer }) {
  return <div className="auth-shell">
    <aside className="auth-aside">
      <div className="brand"><div className="brand-mark"><Activity size={18} /></div><div><b>MedAudit<span> AI</span></b><small>CLINICAL INTELLIGENCE</small></div></div>
      <div className="auth-aside-copy">
        <span className="eyebrow">MORTALITY AUDIT · QUALITY INTELLIGENCE</span>
        <h2>Evidence-linked case review for hospital audit committees.</h2>
        <ul>
          <li><ShieldCheck size={15} /> Accounts are issued by your hospital administrator</li>
          <li><LockKeyhole size={15} /> Each clinician works in a private, access-controlled workspace</li>
          <li><UserRoundCheck size={15} /> Every sign-in and administrative action is audited</li>
        </ul>
      </div>
      <small className="auth-aside-foot">Decision support for qualified reviewers. Not a substitute for clinical judgement.</small>
    </aside>
    <main className="auth-main">
      <motion.div className="auth-card" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: .22 }}>
        <span className="eyebrow">{eyebrow}</span><h1>{title}</h1>{subtitle && <p className="auth-sub">{subtitle}</p>}
        {children}
      </motion.div>
      {footer && <div className="auth-footer">{footer}</div>}
    </main>
  </div>;
}

function PasswordInput({ value, onChange, autoComplete = 'current-password', ...rest }) {
  const [show, setShow] = useState(false);
  return <div className="input-affix"><input type={show ? 'text' : 'password'} value={value} onChange={onChange} autoComplete={autoComplete} {...rest} />
    <button type="button" className="icon-button" onClick={() => setShow(!show)} aria-label={show ? 'Hide password' : 'Show password'}>{show ? <EyeOff size={15} /> : <Eye size={15} />}</button></div>;
}

const PolicyHint = 'At least 12 characters, with three of: lowercase, uppercase, digit, symbol. Must not contain your Doctor ID.';

export function LoginPage() {
  const { login, status, user } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [form, setForm] = useState({ login_id: '', password: '', remember_me: false });
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  if (status === 'loading') return <Splash />;
  if (status === 'authenticated') return <Navigate to={user.home} replace />;
  const reason = location.state?.reason;
  const submit = async e => {
    e.preventDefault(); setBusy(true); setError(null);
    try {
      const res = await login(form);
      const from = location.state?.from;
      const allowed = from && (res.user.role === 'admin' ? from.startsWith('/admin') : !from.startsWith('/admin'));
      navigate(allowed ? from : res.redirect_to, { replace: true });
    } catch (err) {
      if (err.code === 'ACCOUNT_SUSPENDED' || err.code === 'ACCOUNT_INACTIVE') navigate('/account-status', { state: { code: err.code, message: err.message } });
      else setError(err);
    } finally { setBusy(false); }
  };
  return <AuthShell eyebrow="SECURE SIGN-IN" title="Sign in to MedAudit" subtitle="Use the Doctor ID issued by your hospital administrator."
    footer={<>First time here? <Link to="/activate">Activate your account</Link></>}>
    {reason === 'expired' && <Alert tone="info">Your session ended. Please sign in again.</Alert>}
    {reason === 'signed_out' && <Alert tone="info">You have signed out.</Alert>}
    <form onSubmit={submit} className="auth-form" noValidate>
      <Field label="Doctor ID"><input value={form.login_id} onChange={e => setForm({ ...form, login_id: e.target.value })} autoComplete="username" placeholder="e.g. DOC1001" required autoFocus /></Field>
      <Field label="Password"><PasswordInput value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} required /></Field>
      <div className="auth-row">
        <label className="check"><input type="checkbox" checked={form.remember_me} onChange={e => setForm({ ...form, remember_me: e.target.checked })} /> Keep me signed in on this device</label>
        <Link to="/forgot-password" className="text-link">Forgot password?</Link>
      </div>
      {error && <Alert>{error.message}{error.retryAfter ? ` Retry in ${error.retryAfter}s.` : ''}</Alert>}
      <Button variant="primary" type="submit" busy={busy} disabled={!form.login_id || !form.password} className="block">Sign in <ArrowRight size={15} /></Button>
    </form>
    <p className="auth-note">Administrators sign in here with their admin ID. Only use "keep me signed in" on a personal, secured device.</p>
  </AuthShell>;
}

export function ActivatePage() {
  const { activate, status, user } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [form, setForm] = useState({ doctor_id: params.get('doctor_id') || '', email: '', activation_code: '', password: '', confirm_password: '' });
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  if (status === 'authenticated') return <Navigate to={user.home} replace />;
  const fe = fieldErrors(error);
  const mismatch = form.confirm_password && form.password !== form.confirm_password;
  const set = k => e => setForm({ ...form, [k]: e.target.value });
  const submit = async e => {
    e.preventDefault(); if (mismatch) return; setBusy(true); setError(null);
    try { const res = await activate(form); navigate(res.redirect_to, { replace: true }); }
    catch (err) { setError(err); } finally { setBusy(false); }
  };
  return <AuthShell eyebrow="FIRST-TIME ACCOUNT ACTIVATION" title="Activate your Doctor ID"
    subtitle="Your administrator must have registered your Doctor ID and given you a one-time activation code."
    footer={<>Already activated? <Link to="/login">Sign in</Link></>}>
    <form onSubmit={submit} className="auth-form" noValidate>
      <div className="form-grid two">
        <Field label="Doctor ID"><input value={form.doctor_id} onChange={set('doctor_id')} autoComplete="username" required /></Field>
        <Field label="Activation code" hint="Format XXXX-XXXX-XXXX"><input value={form.activation_code} onChange={set('activation_code')} autoComplete="one-time-code" required /></Field>
      </div>
      <Field label="Registered hospital email"><input type="email" value={form.email} onChange={set('email')} autoComplete="email" required /></Field>
      <Field label="New password" hint={PolicyHint} error={fe.password}><PasswordInput value={form.password} onChange={set('password')} autoComplete="new-password" required /></Field>
      <Field label="Confirm password" error={mismatch ? 'Passwords do not match' : null}><PasswordInput value={form.confirm_password} onChange={set('confirm_password')} autoComplete="new-password" required /></Field>
      {error && <Alert>{error.details?.length && error.code === 'VALIDATION_ERROR' ? error.details.map(d => d.issue).join('. ') : error.message}</Alert>}
      <Button variant="primary" type="submit" busy={busy} className="block" disabled={!form.doctor_id || !form.email || !form.activation_code || !form.password || mismatch}>
        <KeyRound size={15} /> Activate and continue</Button>
    </form>
  </AuthShell>;
}

export function ForgotPasswordPage() {
  const [loginId, setLoginId] = useState('');
  const [done, setDone] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const submit = async e => {
    e.preventDefault(); setBusy(true); setError(null);
    try { setDone((await auth.forgot({ login_id: loginId })).message); } catch (err) { setError(err); } finally { setBusy(false); }
  };
  return <AuthShell eyebrow="ACCOUNT RECOVERY" title="Forgot your password?"
    subtitle="Password resets are issued by your hospital administrator after identity verification."
    footer={<><Link to="/reset-password">I have a reset code</Link> · <Link to="/login">Back to sign in</Link></>}>
    {done ? <Alert tone="info">{done}</Alert> : <form onSubmit={submit} className="auth-form">
      <Field label="Doctor ID"><input value={loginId} onChange={e => setLoginId(e.target.value)} autoComplete="username" required autoFocus /></Field>
      {error && <Alert>{error.message}</Alert>}
      <Button variant="primary" type="submit" busy={busy} disabled={!loginId} className="block">Request a password reset</Button>
    </form>}
  </AuthShell>;
}

export function ResetPasswordPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [form, setForm] = useState({ login_id: params.get('login_id') || '', reset_code: '', new_password: '', confirm_password: '' });
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const mismatch = form.confirm_password && form.new_password !== form.confirm_password;
  const set = k => e => setForm({ ...form, [k]: e.target.value });
  const submit = async e => {
    e.preventDefault(); if (mismatch) return; setBusy(true); setError(null);
    try { await auth.reset(form); navigate('/login', { replace: true, state: { reason: 'password_reset' } }); }
    catch (err) { setError(err); } finally { setBusy(false); }
  };
  return <AuthShell eyebrow="ACCOUNT RECOVERY" title="Set a new password" subtitle="Enter the one-time reset code from your administrator."
    footer={<Link to="/login">Back to sign in</Link>}>
    <form onSubmit={submit} className="auth-form" noValidate>
      <div className="form-grid two">
        <Field label="Doctor ID"><input value={form.login_id} onChange={set('login_id')} autoComplete="username" required /></Field>
        <Field label="Reset code"><input value={form.reset_code} onChange={set('reset_code')} autoComplete="one-time-code" required /></Field>
      </div>
      <Field label="New password" hint={PolicyHint}><PasswordInput value={form.new_password} onChange={set('new_password')} autoComplete="new-password" /></Field>
      <Field label="Confirm password" error={mismatch ? 'Passwords do not match' : null}><PasswordInput value={form.confirm_password} onChange={set('confirm_password')} autoComplete="new-password" /></Field>
      {error && <Alert>{error.code === 'VALIDATION_ERROR' && error.details?.length ? error.details.map(d => d.issue).join('. ') : error.message}</Alert>}
      <Button variant="primary" type="submit" busy={busy} className="block" disabled={!form.login_id || !form.reset_code || !form.new_password || mismatch}>Update password</Button>
    </form>
  </AuthShell>;
}

export function UnauthorizedPage() {
  const { user } = useAuth();
  const location = useLocation();
  const home = location.state?.home || user?.home || '/login';
  return <AuthShell eyebrow="ACCESS RESTRICTED" title="This area isn’t available to your account"
    subtitle="Access is determined by your role and by the records shared with you. If you believe you need access, contact your administrator.">
    <div className="auth-icon"><ShieldAlert size={28} /></div>
    <Link to={home} className="button primary block">Go to my workspace</Link>
  </AuthShell>;
}

export function AccountStatePage() {
  const location = useLocation();
  const suspended = location.state?.code === 'ACCOUNT_SUSPENDED';
  return <AuthShell eyebrow="ACCOUNT STATUS" title={suspended ? 'Account suspended' : 'Account inactive'}
    subtitle={location.state?.message || 'This account cannot sign in at the moment.'}>
    <div className="auth-icon"><ShieldAlert size={28} /></div>
    <p className="auth-note">Your records are retained. Only your hospital administrator can restore access.</p>
    <Link to="/login" className="button block">Back to sign in</Link>
  </AuthShell>;
}
