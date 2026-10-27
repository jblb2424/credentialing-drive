import React, { createContext, useContext, useEffect, useRef, useState } from "react";

const AuthUiContext = createContext(null);

export function AuthUiProvider({ value, children }) {
  return <AuthUiContext.Provider value={value}>{children}</AuthUiContext.Provider>;
}

export function useAuthUi() {
  return useContext(AuthUiContext);
}

export function AuthControls() {
  const auth = useAuthUi();
  const [open, setOpen] = useState(false);
  const profileRef = useRef(null);

  useEffect(() => {
    if (!auth?.user || !open) return undefined;
    const closeOnOutsideClick = (event) => {
      if (!profileRef.current?.contains(event.target)) setOpen(false);
    };
    const closeOnEscape = (event) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", closeOnOutsideClick);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsideClick);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [auth?.user, open]);

  if (!auth?.user) return null;
  const displayName = auth.user.name || auth.user.email;
  const initial = displayName.trim().charAt(0).toUpperCase() || "C";

  return <details ref={profileRef} open={open} className="auth-controls auth-profile">
    <summary aria-label="Open account menu" onClick={(event) => { event.preventDefault(); setOpen((current) => !current); }}><span className="auth-profile-avatar" aria-hidden="true">{initial}</span></summary>
    <section className="auth-profile-menu" aria-label="Account menu">
      <div className="auth-profile-identity"><span className="auth-profile-avatar" aria-hidden="true">{initial}</span><div><strong>{displayName}</strong><span>{auth.user.email}</span></div></div>
      <div className="auth-profile-actions"><button type="button" onClick={() => { setOpen(false); auth.signOut(); }}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 5H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h4M14 16l4-4-4-4M18 12H9" /></svg><span>Sign out</span></button></div>
    </section>
  </details>;
}
