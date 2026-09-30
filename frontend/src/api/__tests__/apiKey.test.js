import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  API_KEY_HEADER,
  AUTH_REFUSED_EVENT,
  apiKeyHeaders,
  describeAuthRefusal,
  getStoredApiKey,
  installApiKeyTransport,
  isBackendUrl,
  storeApiKey,
  withApiKey,
} from "../apiKey";
import { handleResponse } from "../apiClient";

const PAGE = { origin: "http://192.168.1.5:5173", href: "http://192.168.1.5:5173/chat" };
const onPage = (extra = {}) => ({ location: PAGE, apiBase: "/api", backendUrls: [], ...extra });

const jsonResponse = (status, body) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

beforeEach(() => {
  window.localStorage.clear();
  storeApiKey("");
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("isBackendUrl", () => {
  it("accepts the API path on the page's own origin", () => {
    expect(isBackendUrl("/api/tools/execute", onPage())).toBe(true);
    expect(isBackendUrl("http://192.168.1.5:5173/api/auth/status", onPage())).toBe(true);
    expect(isBackendUrl("/api", onPage())).toBe(true);
  });

  it("refuses the page's other paths and look-alike prefixes", () => {
    expect(isBackendUrl("/assets/index.js", onPage())).toBe(false);
    expect(isBackendUrl("/apiary", onPage())).toBe(false);
  });

  it("refuses other hosts, other ports and non-http URLs", () => {
    expect(isBackendUrl("https://huggingface.co/api/models", onPage())).toBe(false);
    expect(isBackendUrl("http://192.168.1.9:5173/api/interconnector/status", onPage())).toBe(false);
    expect(isBackendUrl("http://192.168.1.5:5000/api/x", onPage())).toBe(false);
    expect(isBackendUrl("data:text/plain,hi", onPage())).toBe(false);
    expect(isBackendUrl("", onPage())).toBe(false);
  });

  it("accepts every path on a backend origin named in the build settings", () => {
    const opts = onPage({ apiBase: "http://192.168.1.5:5000/api", backendUrls: ["http://192.168.1.5:5000"] });
    expect(isBackendUrl("http://192.168.1.5:5000/voice/stream", opts)).toBe(true);
    expect(isBackendUrl("http://192.168.1.5:5000/api/tools/execute", opts)).toBe(true);
    expect(isBackendUrl("http://192.168.1.6:5000/api/tools/execute", opts)).toBe(false);
  });
});

describe("apiKeyHeaders and withApiKey", () => {
  it("add nothing while this browser has no key", () => {
    const init = { method: "POST" };
    expect(apiKeyHeaders("/api/x", onPage())).toEqual({});
    expect(withApiKey("/api/x", init, onPage())).toBe(init);
  });

  it("add the saved key for the backend only", () => {
    storeApiKey("  k-123  ");
    expect(getStoredApiKey()).toBe("k-123");
    expect(apiKeyHeaders("/api/x", onPage())).toEqual({ [API_KEY_HEADER]: "k-123" });
    expect(apiKeyHeaders("https://example.com/api/x", onPage())).toEqual({});
    const init = { method: "POST", headers: { "Content-Type": "application/json" } };
    const next = withApiKey("/api/x", init, onPage());
    expect(next).not.toBe(init);
    expect(next.method).toBe("POST");
    expect(next.headers.get(API_KEY_HEADER)).toBe("k-123");
    expect(next.headers.get("Content-Type")).toBe("application/json");
    expect(withApiKey("https://example.com/api/x", init, onPage())).toBe(init);
  });

  it("never replaces a key the caller set, whatever its case", () => {
    storeApiKey("mine");
    const init = { headers: { "x-api-key": "master-key" } };
    expect(withApiKey("/api/interconnector/status", init, onPage())).toBe(init);
  });

  it("carries a Request's own headers over", () => {
    storeApiKey("mine");
    const request = new Request("http://192.168.1.5:5173/api/x", { headers: { Accept: "application/json" } });
    const next = withApiKey(request, undefined, onPage());
    expect(next.headers.get("Accept")).toBe("application/json");
    expect(next.headers.get(API_KEY_HEADER)).toBe("mine");
  });
});

describe("storeApiKey", () => {
  it("keeps the key for this tab when the browser refuses storage", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(storeApiKey("tab-only")).toBe(false);
    expect(getStoredApiKey()).toBe("tab-only");
  });
});

describe("installApiKeyTransport", () => {
  it("adds the key to backend fetches only and announces refusals", async () => {
    storeApiKey("k-9");
    const seen = [];
    const fetch = vi.fn(async (input) =>
      String(input).endsWith("/refused")
        ? jsonResponse(401, { error: "x", code: "api_key_required" })
        : jsonResponse(200, {}),
    );
    const target = { fetch };
    installApiKeyTransport({ target });
    installApiKeyTransport({ target }); // a second call changes nothing

    await target.fetch("/api/ok");
    expect(fetch.mock.calls[0][1].headers.get(API_KEY_HEADER)).toBe("k-9");

    await target.fetch("https://example.com/api/ok", { method: "GET" });
    expect(fetch.mock.calls[1][1]).toEqual({ method: "GET" });

    const listener = (e) => seen.push(e.detail.code);
    window.addEventListener(AUTH_REFUSED_EVENT, listener);
    const refused = await target.fetch("/api/refused");
    await settle();
    await settle();
    window.removeEventListener(AUTH_REFUSED_EVENT, listener);
    expect(seen).toEqual(["api_key_required"]);
    // The caller still reads the body it was given.
    expect((await refused.json()).code).toBe("api_key_required");
  });
});

describe("handleResponse on a refusal", () => {
  it("says what to do in the web UI and flags the error", async () => {
    await expect(
      handleResponse(jsonResponse(403, { error: "server words", code: "local_only" }), { quiet: true }),
    ).rejects.toMatchObject({
      status: 403,
      authRefused: "local_only",
      message: describeAuthRefusal("local_only", false),
    });
  });

  it("leaves other 401s alone", async () => {
    await expect(
      handleResponse(jsonResponse(401, { error: "Invalid API key" }), { quiet: true }),
    ).rejects.toMatchObject({ message: "Invalid API key" });
  });
});
