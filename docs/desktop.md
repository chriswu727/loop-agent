# Loop Desktop

Loop Desktop is a native control plane around the same least-authority runtime used
by server deployments. It does not grant the renderer Node.js, shell, Docker, or
filesystem access, and it does not run agent commands directly on the host.

## Security boundary

- The BrowserWindow enables Chromium sandboxing and context isolation and disables
  Node integration, insecure mixed content, external navigation, window creation,
  and all browser permission requests.
- The preload exposes named operations only: read cached state, configure one model
  provider, choose one Git project, restart the runtime, open settings, and subscribe
  to state changes. Raw Electron IPC is never exposed.
- Every IPC handler validates the sender URL against the bundled desktop origin or
  the fixed `http://127.0.0.1:3000` Loop origin.
- Provider keys are encrypted through the operating-system credential service
  (Keychain on macOS, DPAPI on Windows, or a Linux secret store) before the ciphertext
  reaches the OS user-data directory. Loop refuses Linux's insecure `basic_text`
  fallback; if secure storage is unavailable, the key remains in main-process memory
  for the current session and is never written to disk. API/session secrets and
  ed25519 authority/Receipt keys stay in owner-only files; no key is returned in
  desktop state.
- Native project selection resolves symlinks, requires the exact root of a clean Git
  repository, and returns only its display name plus relative path `.` to the Web UI.
  Docker mounts that single repository at `/loop-project`.

## Runtime lifecycle and recovery

The main process checks Docker, invokes Compose with argument arrays and `shell:
false`, and verifies that all core services belong to the owned `loop-desktop`
Compose project before sending the API token to a health endpoint. It then checks
Web/API identity, dependency readiness, and protected task limits. Generated errors
redact the selected path, state directory, provider key, API token, and session
secret before crossing IPC.

The session journal is marked dirty before runtime startup and clean only after an
orderly shutdown. On the next launch an interrupted journal is shown explicitly.
If the owned `loop-desktop` Compose project is still healthy, the supervisor adopts
it; otherwise the user can run a checked stop/start recovery. Persistent task,
Receipt, and change-set state remains in Postgres and the Loop data volume.

On Linux, the API, worker, data volume, and per-command sandbox image use the host
UID/GID so an approved patch does not create root-owned project files. The worker is
granted only the actual Docker socket group. macOS and Windows use Docker Desktop's
bind-mount identity mapping.

## Packaging verification

Electron Forge packages the bundled main/preload code in ASAR, flips fuses to block
Run-as-Node, `NODE_OPTIONS`, CLI inspection, and non-ASAR app loading, and includes a
staged minimal runtime build context. CI creates the native artifact on macOS,
Windows, and Ubuntu, then launches the packaged executable with a fresh profile and
requires a sandboxed startup marker.

Code signing, macOS notarization, and auto-update are release-channel operations and
remain intentionally disabled until real platform credentials are configured.
