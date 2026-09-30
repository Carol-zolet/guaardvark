// frontend/src/api/authService.js
// This install's API key (backend/api/auth_api.py). The saved key rides on
// these requests like any other (see apiKey.js); getAuthStatus can test a key
// that is typed but not saved yet.
/* eslint-env browser */

import { BASE_URL, handleResponse } from "./apiClient";
import { API_KEY_HEADER } from "./apiKey";

const JSON_BODY = { "Content-Type": "application/json" };

/**
 * { key_required, this_machine, key_ok, can_run_protected, can_manage_key,
 *   manage_note, restart_needed, docker, tool_endpoints_protected, protected }
 * @param {{key?: string}} [options] send this key instead of the saved one
 *   ("" sends none)
 */
export const getAuthStatus = async ({ key } = {}) =>
  handleResponse(
    await fetch(`${BASE_URL}/auth/status`, {
      cache: "no-store",
      headers: key === undefined ? {} : { [API_KEY_HEADER]: key },
    }),
  );

/** { key } — shown once; the caller keeps it. */
export const createApiKey = async () =>
  handleResponse(await fetch(`${BASE_URL}/auth/key`, { method: "POST", headers: JSON_BODY, body: "{}" }));

/** { key } — the old key stops working at once. */
export const replaceApiKey = async () =>
  handleResponse(await fetch(`${BASE_URL}/auth/key`, { method: "PUT", headers: JSON_BODY, body: "{}" }));

/** { removed } */
export const removeApiKey = async () =>
  handleResponse(await fetch(`${BASE_URL}/auth/key`, { method: "DELETE" }));
