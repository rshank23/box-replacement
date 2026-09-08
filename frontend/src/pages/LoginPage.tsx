import { useState, type FormEvent } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';

import { useAuth } from '@/auth/AuthContext';
import { errorMessage, Notice, Spinner } from '@/components/Feedback';

const SSO_URL = import.meta.env.VITE_SSO_LOGIN_URL ?? '';

const ROLE_OPTIONS = [
  { value: 'tmf_admin', label: 'TMF Administrator — full access including mapping configuration' },
  { value: 'tmf_operator', label: 'TMF Operator — run migrations and resolve exceptions' },
  { value: 'tmf_reader', label: 'Read only — dashboards and audit trail' },
];

export function LoginPage() {
  const { isAuthenticated, signIn } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState('');
  const [role, setRole] = useState('tmf_admin');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const from = (location.state as { from?: string } | null)?.from ?? '/dashboard';

  if (isAuthenticated) return <Navigate to={from} replace />;

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await signIn(email.trim(), [role]);
      navigate(from, { replace: true });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-page">
      <div className="panel">
        <div className="wordmark">Johnson&amp;Johnson</div>
        <div className="app-name">Document Migration Utility</div>

        <p className="lead">For J&amp;J employees: please log in using SSO.</p>

        <button
          type="button"
          className="btn btn-dark"
          disabled={!SSO_URL}
          title={SSO_URL ? undefined : 'Configure VITE_SSO_LOGIN_URL to enable enterprise sign-in'}
          onClick={() => {
            window.location.assign(SSO_URL);
          }}
        >
          Sign in via SSO
        </button>

        <div className="divider">or</div>

        <form onSubmit={onSubmit}>
          <Notice tone="info">
            Local sign-in issues a short-lived development token from the Integration API. It is only available
            when <span className="mono">DEV_TOKEN_ENABLED</span> is on and is never used in a validated
            environment.
          </Notice>

          <div className="field">
            <label htmlFor="email">Email</label>
            <input
              id="email"
              type="email"
              autoComplete="username"
              placeholder="you@its.jnj.com"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
          </div>

          <div className="field">
            <label htmlFor="role">Role</label>
            <select id="role" value={role} onChange={(event) => setRole(event.target.value)}>
              {ROLE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <span className="help">The role is carried in the token and enforced by the API.</span>
          </div>

          {error ? <Notice tone="danger">{error}</Notice> : null}

          <button type="submit" className="btn btn-dark" disabled={busy || email.trim().length === 0}>
            {busy ? <Spinner /> : null}
            Sign in
          </button>
        </form>

        <div className="links">
          <a href="https://jnj.service-now.com" target="_blank" rel="noreferrer">
            Lost your password?
          </a>
          <a href="https://jnj.service-now.com" target="_blank" rel="noreferrer">
            Request for access
          </a>
        </div>
      </div>
    </div>
  );
}
