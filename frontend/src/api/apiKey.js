// frontend/src/api/apiKey.js
// This install's API key as this browser holds it, and the one place that adds
// it to requests.
//
// Protected backend routes (backend/utils/auth_guard.py) answer the Guaardvark
// machine itself while the install has no key, and once it has one, only a
// caller that sends it in X-API-Key: every device, that machine included. The
// key is entered or created in Settings → API key and kept in localStorage.
//
// installApiKeyTransport() runs once at startup. It wraps window.fetch and adds
// axios interceptors, so every request this app makes to its own backend
// carries the key; the XMLHttpRequest upload asks apiKeyHeaders() itself. The
// key goes only to this app's backend (the /api path on the page's origin, or
// a backend origin named in VITE_API_BASE_URL, VITE_SOCKET_URL or
// VITE_API_URL), never to another host, and never replaces an X-API-Key a
// caller set itself (the Interconnector sends a master's key that way).
//
// Both transports also notice the guard's refusals (a JSON body whose code is
// local_only or api_key_required) and announce them with AUTH_REFUSED_EVENT;
// ApiKeyRefusalNotice turns that into a message that links to Settings.
/* eslint-env browser */

export const API_KEY_HEADER = "X-API-Key";
export const API_KEY_SETTINGS_PATH = "/settings#settings-api-key";
export const API_KEY_CHANGED_EVENT = "guaardvark:api-key-changed";
export const AUTH_REFUSED_EVENT = "guaardvark:auth-refused";
export const AUTH_REFUSAL_CODES = ["local_only", "api_key_required"];

const STORAGE_KEY = "guaardvark.apiKey";
const INSTALLED = Symbol.for("guaardvark.apiKeyTransport");

// Holds the key when this browser refuses localStorage (a private window,
// blocked site data); it then lasts until the tab closes.
let memoryKey = "";

export function getStoredApiKey() {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved) return saved.trim();
  } catch {
    // Storage blocked: fall back to this tab's copy.
  }
  return memoryKey;
}

function announce(name, detail) {
  try {
    window.dispatchEvent(new CustomEvent(name, { detail }));
  } catch {
    // No window (tests without jsdom).
  }
}

/**
 * Keep `key` in this browser, or forget it when empty. Returns true when it
 * will outlast this tab, false when the browser refuses storage.
 */
export function storeApiKey(key) {
  const value = (key || "").trim();
  memoryKey = value;
  let persisted = false;
  try {
    if (value) window.localStorage.setItem(STORAGE_KEY, value);
    else window.localStorage.removeItem(STORAGE_KEY);
    persisted = true;
  } catch {
    // Storage blocked: memoryKey carries it for this tab.
  }
  announce(API_KEY_CHANGED_EVENT, { present: Boolean(value) });
  return persisted;
}

export const clearStoredApiKey = () => storeApiKey("");

const defaultTargets = () => ({
  apiBase: import.meta.env.VITE_API_BASE_URL || "/api",
  backendUrls: [import.meta.env.VITE_SOCKET_URL, import.meta.env.VITE_API_URL],
});

const parse = (value, base) => {
  try {
    return new URL(String(value), base);
  } catch {
    return null;
  }
};

/**
 * True when `url` is this app's own backend.
 * `options` (for tests): { location, apiBase, backendUrls }.
 */
export function isBackendUrl(url, options = {}) {
  if (!url) return false;
  const location = options.location || window.location;
  const { apiBase, backendUrls } = { ...defaultTargets(), ...options };
  const target = parse(url, location.href);
  if (!target || (target.protocol !== "http:" && target.protocol !== "https:")) return false;

  // A backend on an origin of its own: every path on it is the backend.
  const api = parse(apiBase, location.href);
  const backendOrigins = [api, ...(backendUrls || []).map((u) => (u ? parse(u, location.href) : null))]
    .filter((u) => u && u.origin !== location.origin)
    .map((u) => u.origin);
  if (backendOrigins.includes(target.origin)) return true;

  // The page's own origin also serves the app's files, so only the API path
  // there is the backend.
  if (target.origin !== location.origin) return false;
  const prefixes = ["/api"];
  if (api && api.origin === location.origin) {
    prefixes.push(api.pathname.replace(/\/+$/, "") || "/api");
  }
  return prefixes.some((p) => target.pathname === p || target.pathname.startsWith(`${p}/`));
}

/** `{ "X-API-Key": key }` for a request to this app's backend, else `{}`. */
export function apiKeyHeaders(url, options) {
  const key = getStoredApiKey();
  return key && isBackendUrl(url, options) ? { [API_KEY_HEADER]: key } : {};
}

const requestUrl = (input) => {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.href;
  return input?.url || "";
};

/**
 * The `init` to pass to fetch(input, init): the same object when nothing is
 * added, otherwise a copy whose headers carry the key.
 */
export function withApiKey(input, init, options) {
  const key = getStoredApiKey();
  if (!key || !isBackendUrl(requestUrl(input), options)) return init;
  // fetch() takes init.headers in place of a Request's own headers.
  const fromRequest = typeof Request !== "undefined" && input instanceof Request;
  const headers = new Headers(init?.headers ?? (fromRequest ? input.headers : undefined));
  if (headers.has(API_KEY_HEADER)) return init;
  headers.set(API_KEY_HEADER, key);
  return { ...(init || {}), headers };
}

/** The guard's refusal code in a response body, or null. */
export function authRefusalCode(body) {
  const code = body && typeof body === "object" ? body.code : null;
  return AUTH_REFUSAL_CODES.includes(code) ? code : null;
}

/** What to do about a refusal, in the words the UI shows. */
export function describeAuthRefusal(code, hasKey = Boolean(getStoredApiKey())) {
  if (code === "local_only") {
    return hasKey
      ? "This install no longer has an API key, so this works only on the Guaardvark machine itself. Create a new key there in Settings → API key, then enter it in Settings → API key on this device."
      : "This works only on the Guaardvark machine itself. To use it here, create an API key in Settings → API key on the Guaardvark machine, then enter it in Settings → API key on this device.";
  }
  return hasKey
    ? "The API key saved in this browser is not this install's key. Enter the current key in Settings → API key."
    : "Enter this install's API key in Settings → API key. It is shown when it is created, and the Guaardvark machine keeps it in its .env file as GUAARDVARK_API_KEY.";
}

function noticeRefusal(response, url) {
  if (response.status !== 401 && response.status !== 403) return;
  if (!(response.headers.get("content-type") || "").includes("application/json")) return;
  response
    .clone()
    .json()
    .then((body) => {
      const code = authRefusalCode(body);
      if (code) announce(AUTH_REFUSED_EVENT, { code, url });
    })
    .catch(() => {});
}

const axiosRequestUrl = (config) => {
  const url = config?.url || "";
  const base = config?.baseURL || "";
  if (!base || /^[a-z][a-z\d+.-]*:\/\//i.test(url)) return url;
  return `${base.replace(/\/+$/, "")}/${url.replace(/^\/+/, "")}`;
};

/**
 * Add the key to every request this app makes to its backend. Safe to call
 * more than once. `target` is the object whose fetch is wrapped (window).
 */
export function installApiKeyTransport({ axios, target = window } = {}) {
  if (target && typeof target.fetch === "function" && !target.fetch[INSTALLED]) {
    const baseFetch = target.fetch;
    const fetchWithApiKey = (input, init) => {
      const nextInit = withApiKey(input, init);
      const pending = baseFetch.call(target, input, nextInit);
      const url = requestUrl(input);
      if (!isBackendUrl(url)) return pending;
      return pending.then((response) => {
        noticeRefusal(response, url);
        return response;
      });
    };
    fetchWithApiKey[INSTALLED] = true;
    target.fetch = fetchWithApiKey;
  }

  if (axios && !axios[INSTALLED]) {
    axios.interceptors.request.use((config) => {
      const key = getStoredApiKey();
      if (!key || !isBackendUrl(axiosRequestUrl(config))) return config;
      const headers = config.headers;
      if (headers && typeof headers.has === "function") {
        if (!headers.has(API_KEY_HEADER)) headers.set(API_KEY_HEADER, key);
      } else if (!Object.keys(headers || {}).some((k) => k.toLowerCase() === "x-api-key")) {
        config.headers = { ...(headers || {}), [API_KEY_HEADER]: key };
      }
      return config;
    });
    axios.interceptors.response.use(
      (response) => response,
      (error) => {
        const response = error?.response;
        const url = axiosRequestUrl(error?.config);
        const code = response && (response.status === 401 || response.status === 403)
          ? authRefusalCode(response.data)
          : null;
        if (code && isBackendUrl(url)) {
          // Call sites show response.data.error or error.message; both carry
          // the advice, and the server's own words stay in server_error.
          const text = describeAuthRefusal(code);
          response.data = { ...response.data, error: text, message: text, server_error: response.data.error };
          error.message = text;
          error.authRefused = code;
          announce(AUTH_REFUSED_EVENT, { code, url });
        }
        return Promise.reject(error);
      },
    );
    axios[INSTALLED] = true;
  }

  if (target && typeof target.addEventListener === "function" && !target[INSTALLED]) {
    // Another tab saved or forgot the key: pages here refresh what they show.
    target.addEventListener("storage", (event) => {
      if (event.key === STORAGE_KEY) announce(API_KEY_CHANGED_EVENT, { present: Boolean(event.newValue) });
    });
    target[INSTALLED] = true;
  }
}
