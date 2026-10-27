import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, useNavigate } from "react-router-dom";
import "../../static/styles.css";
import "./react.css";
import { App } from "./App.jsx";
import { AuthUiProvider, useAuthUi } from "./auth.jsx";
import { createCozemaAuthClient, getAccessToken } from "./better-auth.js";
import { setAccessTokenGetter } from "./api.js";
import { WorkspaceContext } from "./workspace.jsx";

const AUTH_BOOTSTRAP_TIMEOUT_MS = 15_000;

function AuthBootstrapNotice({ title, detail, retry = false }) {
  return <main className="auth-gate"><section className="auth-gate-card"><p className="eyebrow">Secure workspace</p><h1>{title}</h1><p>{detail}</p>{retry && <button className="primary-button" type="button" onClick={() => window.location.reload()}>Reload workspace</button>}</section></main>;
}

function WorkspaceLoadingNotice({ detail = "Verifying your access and preparing your client data." }) {
  return <main className="auth-gate"><section className="auth-gate-card auth-loading-card" aria-live="polite"><div className="auth-gate-brand"><span className="brand-mark">C</span><span>Cozema<span>Health</span></span></div><p className="eyebrow">Secure workspace</p><h1>Loading your workspace</h1><p>{detail}</p><div className="workspace-loading-indicator" role="status"><span className="workspace-loading-orb" aria-hidden="true" /><span className="workspace-loading-track" aria-hidden="true"><span /></span><span className="sr-only">Loading your workspace</span></div></section></main>;
}

function WorkspaceAccessNotice({ workspace }) {
  const auth = useAuthUi();
  const status = workspace?.workspace_status;
  const copy = status === "unlinked_organization"
    ? { eyebrow: "Workspace unavailable", title: "This organization is not connected to a client workspace.", detail: "Ask a Cozema Health administrator to connect this organization to the appropriate client before continuing." }
    : { eyebrow: "Access required", title: "Your account is not assigned to a client workspace.", detail: "Ask a customer administrator to invite this email address to their Cozema Health client organization." };
  if (workspace?.request_failed) {
    copy.eyebrow = "Connection delayed";
    copy.title = "We could not confirm your client access.";
    copy.detail = "Reload to try again, or contact your administrator if the problem continues.";
  }
  return <main className="auth-gate"><section className="auth-gate-card"><div className="auth-gate-brand"><span className="brand-mark">C</span><span>Cozema<span>Health</span></span></div><p className="eyebrow">{copy.eyebrow}</p><h1>{copy.title}</h1><p>{copy.detail}</p><button className="secondary-button" type="button" onClick={auth?.signOut}>Sign out</button></section></main>;
}

function SignInForm({ authClient, allowGoogleSignIn = false, callbackURL = window.location.origin, mode: controlledMode, onModeChange }) {
  const [localMode, setLocalMode] = useState("sign-in");
  const mode = controlledMode || localMode;
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    setSubmitting(true);
    setMessage("");
    try {
      const result = mode === "sign-up"
        ? await authClient.signUp.email({ name, email, password, callbackURL })
        : await authClient.signIn.email({ email, password, callbackURL });
      if (result.error) throw new Error(result.error.message || "We could not complete that request.");
      setMessage(mode === "sign-up" ? "Check your email to verify your account before signing in." : "Signed in successfully.");
    } catch (error) {
      setMessage(error.message || "We could not complete that request.");
    } finally {
      setSubmitting(false);
    }
  };

  return <form className="auth-email-form" onSubmit={submit}>
    {allowGoogleSignIn && <button className="auth-google-button" type="button" onClick={() => authClient.signIn.social({ provider: "google", callbackURL })}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path fill="#4285f4" d="M21.35 12.27c0-.79-.07-1.55-.21-2.27H12v4.3h5.23a4.47 4.47 0 0 1-1.94 2.93v2.79h3.6c2.1-1.94 3.31-4.79 3.31-7.75Z" /><path fill="#34a853" d="M12 21.75c2.62 0 4.82-.87 6.43-2.36l-3.6-2.79c-1 .67-2.28 1.06-3.83 1.06-2.94 0-5.43-1.99-6.32-4.66H.95v2.88A9.7 9.7 0 0 0 12 21.75Z" /><path fill="#fbbc05" d="M4.68 13c-.23-.67-.36-1.38-.36-2.11s.13-1.44.36-2.11V5.9H.95A9.7 9.7 0 0 0 .95 15.88L4.68 13Z" /><path fill="#ea4335" d="M12 4.13c1.74 0 3.29.6 4.51 1.77l3.38-3.38C16.81-.63 14.62-.75 12-.75A9.7 9.7 0 0 0 .95 5.9l3.73 2.88C5.57 6.12 8.06 4.13 12 4.13Z" /></svg>
      <span>Continue with Google</span>
    </button>}
    {allowGoogleSignIn && <div className="auth-divider" aria-hidden="true"><span />or<span /></div>}
    <div className="auth-email-fields">
      {mode === "sign-up" && <label>Name<input value={name} onChange={(event) => setName(event.target.value)} autoComplete="name" placeholder="Your full name" required /></label>}
      <label>Email address<input type="email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="email" placeholder="Enter your email address" required /></label>
      <label>Password<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete={mode === "sign-up" ? "new-password" : "current-password"} placeholder="Enter your password" minLength="8" required /></label>
    </div>
    <button className="auth-sign-in" type="submit" disabled={submitting}>{submitting ? "Working..." : mode === "sign-up" ? "Create account" : "Continue"}<span aria-hidden="true">›</span></button>
    {message && <p className="user-action-message" role="status">{message}</p>}
    <p className="auth-account-switch">{mode === "sign-in" ? "Don't have an account?" : "Already have an account?"}<button className="text-button" type="button" onClick={() => { const nextMode = mode === "sign-in" ? "sign-up" : "sign-in"; setLocalMode(nextMode); onModeChange?.(nextMode); setMessage(""); }}>{mode === "sign-in" ? "Sign up" : "Sign in"}</button></p>
  </form>;
}

function InvitationAcceptance({ authClient, config, session, refreshSession }) {
  const navigate = useNavigate();
  const invitationId = new URLSearchParams(window.location.search).get("invitationId");
  const [message, setMessage] = useState("");
  const [accepting, setAccepting] = useState(false);
  const [signInMode, setSignInMode] = useState("sign-in");
  const accept = async () => {
    if (accepting) return;
    setAccepting(true);
    setMessage("");
    try {
      const result = await authClient.organization.acceptInvitation({ invitationId });
      if (result.error) {
        if (result.error.code === "INVITATION_NOT_FOUND" || /invitation not found/i.test(result.error.message || "")) {
          setMessage("This invitation was already accepted, expired, or cancelled. If you already accepted it, open the workspace from the app home page.");
          return;
        }
        throw new Error(result.error.message || "Unable to accept invitation");
      }
      await refreshSession();
      navigate("/", { replace: true });
    } catch (error) { setMessage(error.message || "Unable to accept invitation"); }
    finally { setAccepting(false); }
  };
  if (!session) {
    const signingUp = signInMode === "sign-up";
    return <main className="auth-gate auth-sign-in-gate"><section className="auth-gate-card auth-sign-in-card"><div className="auth-sign-in-header"><div className="auth-gate-brand"><span className="brand-mark">C</span><span>Cozema<span>Health</span></span></div><p className="eyebrow">Client invitation</p><h1>{signingUp ? "Create your Cozema Health account" : "Join your Cozema Health workspace"}</h1><p>{signingUp ? "Create an account with the email address that received this invitation." : "Sign in with the email address that received this invitation to join the workspace."}</p></div><SignInForm authClient={authClient} allowGoogleSignIn={config.google_sign_in_enabled} callbackURL={window.location.href} mode={signInMode} onModeChange={setSignInMode} /></section></main>;
  }
  if (!invitationId) return <AuthBootstrapNotice title="Invitation link is incomplete." detail="Request a new invitation from your workspace administrator." />;
  return <main className="auth-gate auth-sign-in-gate"><section className="auth-gate-card auth-sign-in-card invitation-confirmation-card"><div className="auth-sign-in-header"><div className="auth-gate-brand"><span className="brand-mark">C</span><span>Cozema<span>Health</span></span></div><p className="eyebrow">Client invitation</p><h1>Join your workspace</h1><p>Confirm that you want to join this Cozema Health workspace.</p></div><div className="invitation-confirmation-action"><button className="auth-sign-in" type="button" onClick={accept} disabled={accepting}>{accepting ? "Accepting..." : "Accept invitation"}<span aria-hidden="true">›</span></button>{message && <p className="user-action-message" role="status">{message}</p>}</div></section></main>;
}

function BetterAuthGate({ authClient, config }) {
  const { data: session, isPending, error, refetch } = authClient.useSession();
  const [workspace, setWorkspace] = useState(null);
  const [isWorkspaceLoading, setIsWorkspaceLoading] = useState(false);
  const [isRestoringOrganization, setIsRestoringOrganization] = useState(false);
  const [signInMode, setSignInMode] = useState("sign-in");
  const restoredSessions = useRef(new Set());
  const authBaseURL = config.base_url;
  const sessionIdentity = session ? `${session.user?.id || "unknown"}:${session.session?.id || "unknown"}:${session.session?.activeOrganizationId || "none"}` : "";
  const sessionKey = session ? `${session.user?.id || "unknown"}:${session.session?.id || "unknown"}` : "";
  const activeOrganizationId = session?.session?.activeOrganizationId;

  useEffect(() => {
    if (!sessionKey || activeOrganizationId || restoredSessions.current.has(sessionKey)) {
      setIsRestoringOrganization(false);
      return undefined;
    }

    let cancelled = false;
    restoredSessions.current.add(sessionKey);
    setIsRestoringOrganization(true);

    const request = async () => {
      const baseURL = authBaseURL.replace(/\/$/, "");
      const listResponse = await fetch(`${baseURL}/api/auth/organization/list`, { credentials: "include" });
      if (!listResponse.ok) throw new Error("Unable to load your workspaces");
      const organizations = await listResponse.json();

      // A user with one workspace should never be stranded by a missing active-org session value.
      if (!Array.isArray(organizations) || organizations.length !== 1) return;

      const activeResponse = await fetch(`${baseURL}/api/auth/organization/set-active`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ organizationId: organizations[0].id }),
      });
      if (!activeResponse.ok) throw new Error("Unable to open your workspace");
      await refetch();
    };

    request()
      .catch((restoreError) => console.error("Unable to restore the active workspace", restoreError))
      .finally(() => { if (!cancelled) setIsRestoringOrganization(false); });
    return () => { cancelled = true; };
  }, [activeOrganizationId, authBaseURL, refetch, sessionKey]);

  useEffect(() => {
    setAccessTokenGetter(sessionIdentity ? () => getAccessToken(authBaseURL) : null);
    return () => setAccessTokenGetter(null);
  }, [authBaseURL, sessionIdentity]);

  useEffect(() => {
    if (!sessionIdentity || isRestoringOrganization) {
      setWorkspace(null);
      setIsWorkspaceLoading(false);
      return undefined;
    }
    let cancelled = false;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), AUTH_BOOTSTRAP_TIMEOUT_MS);
    setWorkspace(null);
    setIsWorkspaceLoading(true);
    getAccessToken(authBaseURL)
      .then((token) => fetch("/auth/me", { headers: { Authorization: `Bearer ${token}` }, signal: controller.signal }))
      .then((response) => (response.ok ? response.json() : Promise.reject(response)))
      .then((payload) => { if (!cancelled) setWorkspace(payload); })
      .catch((requestError) => {
        console.error("Workspace verification failed", requestError?.status || requestError?.message || "unknown error");
        if (!cancelled) setWorkspace({ workspace_ready: false, request_failed: true });
      })
      .finally(() => { window.clearTimeout(timeout); if (!cancelled) setIsWorkspaceLoading(false); });
    return () => { cancelled = true; controller.abort(); window.clearTimeout(timeout); };
  }, [authBaseURL, isRestoringOrganization, sessionIdentity]);

  const signOut = async () => { await authClient.signOut(); await refetch(); };
  const authUi = { user: session?.user, signOut };

  if (isPending && !workspace) return <WorkspaceLoadingNotice detail="Starting your secure session." />;
  if (error) return <AuthBootstrapNotice title="We could not start your secure session." detail="Reload to try again." retry />;
  if (window.location.pathname === "/accept-invitation") return <AuthUiProvider value={authUi}><InvitationAcceptance authClient={authClient} config={config} session={session} refreshSession={refetch} /></AuthUiProvider>;
  if (!session) {
    const signingUp = signInMode === "sign-up";
    return <main className="auth-gate auth-sign-in-gate"><section className="auth-gate-card auth-sign-in-card"><div className="auth-sign-in-header"><div className="auth-gate-brand"><span className="brand-mark">C</span><span>Cozema<span>Health</span></span></div><p className="eyebrow">Secure workspace</p><h1>{signingUp ? "Create your Cozema Health account" : "Sign in to Cozema Health"}</h1><p>{signingUp ? "Set up your account to access your credentialing workspace." : "Welcome back. Sign in to access your credentialing workspace."}</p></div><SignInForm authClient={authClient} allowGoogleSignIn={config.google_sign_in_enabled} mode={signInMode} onModeChange={setSignInMode} /></section></main>;
  }
  if (isRestoringOrganization) return <WorkspaceLoadingNotice detail="Opening your client workspace." />;
  if (isWorkspaceLoading) return <WorkspaceLoadingNotice />;
  if (!workspace?.workspace_ready) return <AuthUiProvider value={authUi}><WorkspaceAccessNotice workspace={workspace} /></AuthUiProvider>;
  return <AuthUiProvider value={authUi}><WorkspaceContext.Provider value={workspace}><App /></WorkspaceContext.Provider></AuthUiProvider>;
}

function LocalDevelopmentGate() {
  const [workspace, setWorkspace] = useState(null);
  const [loading, setLoading] = useState(true);
  const [requestFailed, setRequestFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    fetch("/auth/me", { signal: controller.signal, cache: "no-store" })
      .then((response) => (response.ok ? response.json() : Promise.reject(response)))
      .then((payload) => { if (!cancelled) setWorkspace(payload); })
      .catch((error) => {
        if (cancelled || error?.name === "AbortError") return;
        console.error("Unable to initialize local development access", error);
        setRequestFailed(true);
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; controller.abort(); };
  }, []);

  if (loading) return <WorkspaceLoadingNotice detail="Opening local development access." />;
  if (requestFailed || !workspace?.workspace_ready) return <AuthBootstrapNotice title="Local development access is unavailable." detail="Ensure the local FastAPI server is running with LOCAL_DEV_AUTH_BYPASS=true." retry />;
  const authUi = { user: workspace.user, signOut: () => window.location.reload() };
  return <AuthUiProvider value={authUi}><WorkspaceContext.Provider value={workspace}><App /></WorkspaceContext.Provider></AuthUiProvider>;
}

function BetterAuthBootstrap() {
  const [config, setConfig] = useState(null);
  const [requestFailed, setRequestFailed] = useState(false);
  useEffect(() => {
    // Local development has an explicit loopback-only backend bypass so the
    // dashboard remains usable when the optional local auth service is offline.
    if (["127.0.0.1", "localhost", "::1"].includes(window.location.hostname)) {
      setConfig({ provider: "local_dev_bypass" });
      return undefined;
    }

    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), AUTH_BOOTSTRAP_TIMEOUT_MS);
    let cancelled = false;
    fetch("/auth/config", { signal: controller.signal, cache: "no-store" })
      .then((response) => (response.ok ? response.json() : Promise.reject(response)))
      .then((payload) => {
        if (payload?.enabled && payload.provider === "local_dev_bypass") {
          setConfig({ provider: "local_dev_bypass" });
          return;
        }
        if (!payload?.enabled || payload.provider !== "better_auth" || !payload?.better_auth_config?.base_url) throw new Error("Better Auth configuration is unavailable");
        setConfig({ provider: "better_auth", ...payload.better_auth_config });
      })
      .catch((requestError) => {
        if (cancelled || requestError?.name === "AbortError") return;
        console.error("Unable to load Better Auth configuration", requestError);
        setRequestFailed(true);
      })
      .finally(() => window.clearTimeout(timeout));
    return () => { cancelled = true; controller.abort(); window.clearTimeout(timeout); };
  }, []);
  if (requestFailed) return <AuthBootstrapNotice title="We could not start your secure session." detail="The authentication service configuration is unavailable. Reload to try again." retry />;
  if (!config) return <WorkspaceLoadingNotice detail="Connecting to the authentication service." />;
  if (config.provider === "local_dev_bypass") return <LocalDevelopmentGate />;
  return <BetterAuthGate authClient={createCozemaAuthClient(config.base_url)} config={config} />;
}

createRoot(document.getElementById("root")).render(<React.StrictMode><BrowserRouter><BetterAuthBootstrap /></BrowserRouter></React.StrictMode>);
