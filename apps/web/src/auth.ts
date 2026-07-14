const authority = import.meta.env.VITE_OIDC_AUTHORITY?.replace(/\/$/, "");
const clientId = import.meta.env.VITE_OIDC_CLIENT_ID;
const redirectUri = import.meta.env.VITE_OIDC_REDIRECT_URI || `${window.location.origin}/signin-callback`;

type Discovery = { authorization_endpoint: string; token_endpoint: string; end_session_endpoint?: string };

const encode = (bytes: Uint8Array) => btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

async function discovery(): Promise<Discovery> {
  if (!authority) throw new Error("VITE_OIDC_AUTHORITY is not configured");
  const response = await fetch(`${authority}/.well-known/openid-configuration`);
  if (!response.ok) throw new Error("Unable to load OIDC discovery document");
  return response.json() as Promise<Discovery>;
}

export const auth = {
  configured: Boolean(authority && clientId),
  token: () => sessionStorage.getItem("openkate.access_token") || import.meta.env.VITE_ACCESS_TOKEN,
  login: async () => {
    if (!clientId) throw new Error("VITE_OIDC_CLIENT_ID is not configured");
    const verifier = encode(crypto.getRandomValues(new Uint8Array(48)));
    const challenge = encode(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier))));
    const state = encode(crypto.getRandomValues(new Uint8Array(24)));
    sessionStorage.setItem("openkate.oidc.verifier", verifier);
    sessionStorage.setItem("openkate.oidc.state", state);
    const metadata = await discovery();
    const query = new URLSearchParams({ client_id: clientId, redirect_uri: redirectUri, response_type: "code", scope: "openid profile email", code_challenge: challenge, code_challenge_method: "S256", state });
    window.location.assign(`${metadata.authorization_endpoint}?${query}`);
  },
  callback: async () => {
    if (!clientId) throw new Error("VITE_OIDC_CLIENT_ID is not configured");
    const query = new URLSearchParams(window.location.search);
    if (query.get("state") !== sessionStorage.getItem("openkate.oidc.state")) throw new Error("OIDC state does not match");
    const code = query.get("code");
    const verifier = sessionStorage.getItem("openkate.oidc.verifier");
    if (!code || !verifier) throw new Error(query.get("error_description") || "OIDC callback is incomplete");
    const metadata = await discovery();
    const response = await fetch(metadata.token_endpoint, { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: new URLSearchParams({ grant_type: "authorization_code", client_id: clientId, redirect_uri: redirectUri, code, code_verifier: verifier }) });
    if (!response.ok) throw new Error("OIDC token exchange failed");
    const tokens = await response.json() as { access_token: string };
    sessionStorage.setItem("openkate.access_token", tokens.access_token);
    sessionStorage.removeItem("openkate.oidc.verifier");
    sessionStorage.removeItem("openkate.oidc.state");
    window.location.replace("/foundation");
  },
  logout: () => {
    sessionStorage.removeItem("openkate.access_token");
    window.location.replace("/foundation");
  },
};
