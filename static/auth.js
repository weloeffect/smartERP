"use strict";
(() => {
  const get = id => document.getElementById(id);
  let setup = false;
  const showError = message => { get("auth-error").textContent = message; get("auth-error").hidden = !message; };
  async function configure() {
    try {
      const response = await fetch("/api/auth/status", {credentials: "same-origin", cache: "no-store"});
      if (!response.ok) throw new Error("Unable to connect. Refresh this page to try again.");
      const status = await response.json();
      if (status.authenticated) { location.replace("/"); return; }
      setup = status.setup_required;
      get("auth-title").textContent = setup ? "Create your account" : "Welcome back";
      get("auth-description").textContent = setup ? "Choose a login for your smartERP workspace." : "Sign in to your workspace.";
      get("auth-description").hidden = setup;
      get("auth-submit").textContent = setup ? "Create account" : "Sign in";
      get("confirm-field").hidden = !setup;
      get("confirm-password").required = setup;
      get("password-hint").hidden = !setup;
      get("password").minLength = setup ? 8 : 1;
      get("password").autocomplete = setup ? "new-password" : "current-password";
      document.title = `smartERP — ${setup ? "Create account" : "Sign in"}`;
      get("auth-form").hidden = false;
      get("username").focus();
    } catch (error) { showError(error.message); }
    finally { get("auth-loading").hidden = true; }
  }
  get("toggle-password").addEventListener("click", () => {
    const show = get("password").type === "password";
    get("password").type = show ? "text" : "password";
    get("toggle-password").textContent = show ? "Hide" : "Show";
    get("toggle-password").setAttribute("aria-pressed", String(show));
  });
  get("auth-form").addEventListener("submit", async event => {
    event.preventDefault();
    showError("");
    if (setup && get("password").value !== get("confirm-password").value) {
      showError("The passwords do not match."); get("confirm-password").focus(); return;
    }
    get("auth-submit").disabled = true;
    get("auth-submit").textContent = setup ? "Creating account…" : "Signing in…";
    try {
      const response = await fetch(`/api/auth/${setup ? "setup" : "login"}`, {
        method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({username: get("username").value.trim(), password: get("password").value})
      });
      const body = await response.json();
      if (!response.ok) {
        if (response.status === 409) await configure();
        throw new Error(body.error?.message || "Unable to sign in. Try again.");
      }
      get("password").value = ""; get("confirm-password").value = "";
      location.replace("/");
    } catch (error) { showError(error.message || "Unable to connect. Try again."); }
    finally { get("auth-submit").disabled = false; get("auth-submit").textContent = setup ? "Create account" : "Sign in"; }
  });
  configure();
})();
