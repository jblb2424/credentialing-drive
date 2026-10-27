const controls = [...document.querySelectorAll("[data-auth-controls]")];
let clerk;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderAccount(account) {
  controls.forEach((control) => {
    control.hidden = false;
    if (!account) {
      control.innerHTML = '<button class="auth-sign-in" type="button" data-auth-sign-in>Sign in</button>';
      return;
    }
    const role = account.role && account.role !== "unassigned" ? account.role.replaceAll("_", " ") : "member";
    control.innerHTML = `<span class="auth-user" title="${escapeHtml(account.email || "")}"><span class="auth-user-name">${escapeHtml(account.display_name)}</span><span class="auth-role">${escapeHtml(role)}</span></span><button class="auth-sign-out" type="button" data-auth-sign-out>Sign out</button>`;
  });
}

async function getConfig() {
  const response = await fetch("/auth/config", { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error("Could not load authentication configuration");
  return response.json();
}

async function loadClerk(config) {
  if (window.Clerk) return window.Clerk;
  await new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.async = true;
    script.crossOrigin = "anonymous";
    script.src = `${config.frontend_api_url}/npm/@clerk/ui@1/dist/ui.browser.js`;
    script.onload = resolve;
    script.onerror = () => reject(new Error("Could not load Clerk UI"));
    document.head.append(script);
  });
  await new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.async = true;
    script.crossOrigin = "anonymous";
    script.dataset.clerkPublishableKey = config.publishable_key;
    script.src = `${config.frontend_api_url}/npm/@clerk/clerk-js@6/dist/clerk.browser.js`;
    script.onload = resolve;
    script.onerror = () => reject(new Error("Could not load Clerk"));
    document.head.append(script);
  });
  await window.Clerk.load({ ui: { ClerkUI: window.__internal_ClerkUICtor } });
  return window.Clerk;
}

function clerkBrowserProfile() {
  const user = clerk?.user;
  if (!user) return null;
  const email = user.primaryEmailAddress?.emailAddress;
  return {
    email,
    display_name: user.fullName || user.username || email || "Credentialing user",
  };
}

async function getCurrentUser() {
  if (!clerk?.session) return null;
  const token = await clerk.session.getToken();
  const response = await fetch("/auth/me", {
    headers: { Accept: "application/json", Authorization: `Bearer ${token}` },
  });
  if (!response.ok) throw new Error("Could not verify your account");
  const payload = await response.json();
  return payload.user ? { ...payload.user, ...clerkBrowserProfile() } : null;
}

async function syncAccount() {
  try {
    renderAccount(clerk?.user ? await getCurrentUser() : null);
  } catch (error) {
    console.warn("Authentication profile could not be loaded", error);
    renderAccount(clerkBrowserProfile());
  }
}

async function initializeAuth() {
  if (!controls.length) return;
  const config = await getConfig();
  if (!config.enabled) return;

  clerk = await loadClerk(config.clerk_config);
  await syncAccount();
  clerk.addListener(syncAccount);

  document.addEventListener("click", async (event) => {
    if (event.target.closest("[data-auth-sign-in]")) {
      clerk.openSignIn({ afterSignInUrl: window.location.href, afterSignUpUrl: window.location.href });
    }
    if (event.target.closest("[data-auth-sign-out]")) {
      await clerk.signOut();
      renderAccount(null);
    }
  });
}

initializeAuth().catch((error) => console.warn("Clerk Authentication is unavailable", error));
