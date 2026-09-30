// frontend/src/components/settings/ApiKeySection.jsx
// Settings → API key: the key this browser sends, and this install's key.
//
// Protected actions (running tools, automation, backups, file edits, ...)
// answer the Guaardvark machine itself while the install has no key. Once it
// has one, every device, that machine included, sends it; this browser keeps
// its copy in localStorage (api/apiKey.js adds it to every request to this
// backend). The install's key is created, replaced and removed here, from the
// Guaardvark machine while there is no key, or with the current key.
/* eslint-env browser */

import React, { useCallback, useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  InputAdornment,
  TextField,
  Typography,
} from "@mui/material";
import VisibilityIcon from "@mui/icons-material/Visibility";
import VisibilityOffIcon from "@mui/icons-material/VisibilityOff";

import {
  API_KEY_CHANGED_EVENT,
  clearStoredApiKey,
  describeAuthRefusal,
  getStoredApiKey,
  storeApiKey,
} from "../../api/apiKey";
import { createApiKey, getAuthStatus, removeApiKey, replaceApiKey } from "../../api/authService";
import { ActionButton, Cluster, ConfirmActionDialog, Hint, Line, SettingsPanel, StatusPill } from "./ui";

export const API_KEY_SECTION_ID = "settings-api-key";

const NO_KEY_ELSEWHERE = (docker) =>
  docker
    ? "This install has no API key, so protected actions work only on the Guaardvark machine, and a browser never counts as that machine under Docker. Run ./start-docker.sh on the Docker host: it creates a key, prints it, and restarts with it."
    : "This install has no API key, so protected actions work only on the Guaardvark machine. Create one there in Settings → API key, then enter it here.";

/** Pill and sentence for what a status answer means for this browser. */
export function describeStatus(status, sentKey) {
  if (!status) return { tone: "neutral", label: "checking", text: "", ok: false };
  if (status.key_ok) {
    return { tone: "ok", label: "key accepted", text: "This browser can run protected actions.", ok: true };
  }
  if (!status.key_required && status.this_machine) {
    return {
      tone: "ok",
      label: "Guaardvark machine",
      text: "This install has no API key, so protected actions work here on the Guaardvark machine without one. Other devices need a key: create one below.",
      ok: true,
    };
  }
  if (status.key_required && sentKey) {
    return { tone: "error", label: "key not accepted", text: describeAuthRefusal("api_key_required", true), ok: false };
  }
  if (status.key_required) {
    return {
      tone: "warn",
      label: "key needed",
      text: "This install has an API key. Enter it above to run protected actions from this browser.",
      ok: false,
    };
  }
  return { tone: "warn", label: "Guaardvark machine only", text: NO_KEY_ELSEWHERE(status.docker), ok: false };
}

function manageHint(status) {
  if (!status || status.can_manage_key) return null;
  if (status.manage_note) return status.manage_note;
  if (!status.key_required) {
    return status.docker
      ? "Under Docker the key is made by ./start-docker.sh on the Docker host."
      : "A key can be created only on the Guaardvark machine itself: open Settings → API key in a browser there.";
  }
  return "Enter the current key above to replace or remove it. The Guaardvark machine keeps it in its .env file as GUAARDVARK_API_KEY.";
}

// navigator.clipboard exists only in secure contexts, and a LAN address over
// http is not one; selecting the field and copying works there.
async function copyText(text, input) {
  try {
    if (window.isSecureContext && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Fall through to the selection copy.
  }
  try {
    input?.focus();
    input?.select();
    return document.execCommand("copy");
  } catch {
    return false;
  }
}

function NewKeyDialog({ apiKey, persisted, onClose }) {
  const inputRef = useRef(null);
  const [copied, setCopied] = useState(null);
  const copy = async () => setCopied(await copyText(apiKey, inputRef.current));
  return (
    <Dialog open={Boolean(apiKey)} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ fontSize: "1rem" }}>This install&apos;s API key</DialogTitle>
      <DialogContent sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
        <Typography variant="body2" color="text.secondary">
          It is shown once. {persisted
            ? "This browser has saved it."
            : "This browser refuses to store it, so it is kept only until this tab closes."}{" "}
          To use Guaardvark from another device, open Settings → API key there and paste it.
          Command-line and API clients send it in the X-API-Key header.
        </Typography>
        <Line nowrap>
          <TextField
            className="grow"
            size="small"
            value={apiKey || ""}
            inputRef={inputRef}
            onFocus={(e) => e.target.select()}
            InputProps={{ readOnly: true, sx: { fontFamily: "monospace", fontSize: "0.85rem" } }}
          />
          <ActionButton onClick={copy}>Copy</ActionButton>
        </Line>
        {copied === true && <Hint>Copied.</Hint>}
        {copied === false && <Hint>Copy did not work here; select the key and copy it by hand.</Hint>}
        <Hint>
          Lost it later? The Guaardvark machine keeps it in the .env file in its folder, as
          GUAARDVARK_API_KEY.
        </Hint>
      </DialogContent>
      <DialogActions>
        <ActionButton onClick={onClose}>Done</ActionButton>
      </DialogActions>
    </Dialog>
  );
}

NewKeyDialog.propTypes = {
  apiKey: PropTypes.string,
  persisted: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
};

export default function ApiKeySection() {
  const [status, setStatus] = useState(null);
  const [statusError, setStatusError] = useState(null);
  const [stored, setStored] = useState(() => getStoredApiKey());
  const [draft, setDraft] = useState(() => getStoredApiKey());
  const [showKey, setShowKey] = useState(false);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(null);
  const [confirm, setConfirm] = useState(null);
  const [newKey, setNewKey] = useState(null);
  const [actionError, setActionError] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const next = await getAuthStatus();
      setStatus(next);
      setStatusError(null);
      return next;
    } catch (e) {
      setStatusError(e.message || "Could not read the API key status.");
      return null;
    }
  }, []);

  useEffect(() => {
    refresh();
    const onChanged = () => {
      const current = getStoredApiKey();
      setStored(current);
      setDraft(current);
      refresh();
    };
    window.addEventListener(API_KEY_CHANGED_EVENT, onChanged);
    return () => window.removeEventListener(API_KEY_CHANGED_EVENT, onChanged);
  }, [refresh]);

  const run = async (name, fn) => {
    setBusy(name);
    setActionError(null);
    try {
      await fn();
    } catch (e) {
      setActionError(e.message || "That did not work.");
    } finally {
      setBusy(null);
    }
  };

  const test = () =>
    run("test", async () => {
      const typed = draft.trim();
      const answer = await getAuthStatus({ key: typed });
      const said = describeStatus(answer, Boolean(typed));
      setResult({ severity: said.ok ? "success" : "warning", text: said.text });
    });

  const save = () =>
    run("save", async () => {
      const persisted = storeApiKey(draft);
      const answer = await refresh();
      const said = describeStatus(answer, Boolean(draft.trim()));
      const kept = persisted ? "Saved in this browser." : "This browser refuses to store it, so it is kept only until this tab closes.";
      setResult({ severity: said.ok ? "success" : "warning", text: `${kept} ${said.text}` });
    });

  const forget = () => {
    clearStoredApiKey();
    setResult({ severity: "info", text: "Removed from this browser. The install's key is unchanged." });
  };

  const showNewKey = (key) => {
    const persisted = storeApiKey(key);
    setNewKey({ key, persisted });
    setResult(null);
  };

  const create = () =>
    run("create", async () => {
      const { key } = await createApiKey();
      showNewKey(key);
    });

  const replace = () =>
    run("replace", async () => {
      setConfirm(null);
      const { key } = await replaceApiKey();
      showNewKey(key);
    });

  const remove = () =>
    run("remove", async () => {
      setConfirm(null);
      await removeApiKey();
      clearStoredApiKey();
      setResult({ severity: "info", text: "The install has no API key now. Protected actions work only on the Guaardvark machine itself." });
    });

  const said = describeStatus(status, Boolean(stored));
  const pending = draft.trim() !== stored;
  const hint = manageHint(status);

  return (
    <SettingsPanel
      id={API_KEY_SECTION_ID}
      title="API key"
      description="Lets other devices and scripts run protected actions."
    >
      <Cluster label="This browser" note="kept here only, sent only to this Guaardvark">
        <Line>
          <TextField
            className="grow"
            size="small"
            label="API key"
            type={showKey ? "text" : "password"}
            value={draft}
            autoComplete="off"
            onChange={(e) => {
              setDraft(e.target.value);
              setResult(null);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && pending && draft.trim()) {
                e.preventDefault();
                save();
              }
            }}
            inputProps={{ spellCheck: false, "aria-label": "API key" }}
            InputProps={{
              sx: { fontFamily: showKey ? "monospace" : undefined },
              endAdornment: (
                <InputAdornment position="end">
                  <IconButton
                    size="small"
                    aria-label={showKey ? "Hide the key" : "Show the key"}
                    onClick={() => setShowKey((v) => !v)}
                    edge="end"
                  >
                    {showKey ? <VisibilityOffIcon fontSize="small" /> : <VisibilityIcon fontSize="small" />}
                  </IconButton>
                </InputAdornment>
              ),
            }}
          />
          {pending && draft.trim() && (
            <ActionButton kind="primary" onClick={save} loading={busy === "save"}>
              Save
            </ActionButton>
          )}
          <ActionButton
            onClick={test}
            loading={busy === "test"}
            tooltip="Asks this Guaardvark whether the key in the field lets this browser run protected actions"
          >
            Test
          </ActionButton>
          {stored && (
            <ActionButton onClick={forget} tooltip="Removes the key from this browser only">
              Forget
            </ActionButton>
          )}
        </Line>
        <Line>
          <StatusPill tone={said.tone} label={said.label} />
          {said.text && <Hint>{said.text}</Hint>}
        </Line>
        {statusError && <Alert severity="error" sx={{ py: 0.25 }}>{statusError}</Alert>}
        {result && (
          <Alert severity={result.severity} sx={{ py: 0.25 }} onClose={() => setResult(null)}>
            {result.text}
          </Alert>
        )}
      </Cluster>

      <Cluster label="This install" note={status ? (status.key_required ? "has a key" : "no key yet") : undefined}>
        <Hint>
          Once this install has a key, every device, this machine included, has to send it. Browsers keep
          it here; command-line and API clients send it in the X-API-Key header (GUAARDVARK_API_KEY).
        </Hint>
        {status?.can_manage_key && (
          <Line>
            {!status.key_required ? (
              <ActionButton
                onClick={create}
                loading={busy === "create"}
                tooltip="Makes a new random key, saves it in .env and in this browser, and shows it once"
              >
                Create API key
              </ActionButton>
            ) : (
              <>
                <ActionButton onClick={() => setConfirm("replace")} loading={busy === "replace"}>
                  Replace key
                </ActionButton>
                <ActionButton onClick={() => setConfirm("remove")} loading={busy === "remove"}>
                  Remove key
                </ActionButton>
              </>
            )}
          </Line>
        )}
        {hint && <Hint>{hint}</Hint>}
        {actionError && (
          <Alert severity="error" sx={{ py: 0.25 }} onClose={() => setActionError(null)}>
            {actionError}
          </Alert>
        )}
        {status?.protected?.length > 0 && (
          <Box>
            <Hint>Needs the Guaardvark machine or the key: {status.protected.join(" · ")}.</Hint>
          </Box>
        )}
      </Cluster>

      <ConfirmActionDialog
        open={confirm === "replace"}
        title="Replace the API key"
        description="A new key takes the place of the current one at once. Every other browser, script and command-line client that uses the current key is refused until it is given the new one. This browser switches to the new key by itself."
        keeps="Not touched: your data, chats, settings and generated files."
        confirmLabel="Replace key"
        busy={busy === "replace"}
        onConfirm={replace}
        onClose={() => setConfirm(null)}
      />
      <ConfirmActionDialog
        open={confirm === "remove"}
        title="Remove the API key"
        description="Protected actions go back to working only on the Guaardvark machine itself. Other devices and scripts can no longer run them, with or without the old key."
        keeps="Not touched: your data, chats, settings and generated files."
        confirmLabel="Remove key"
        busy={busy === "remove"}
        onConfirm={remove}
        onClose={() => setConfirm(null)}
      />
      <NewKeyDialog apiKey={newKey?.key} persisted={Boolean(newKey?.persisted)} onClose={() => setNewKey(null)} />
    </SettingsPanel>
  );
}
