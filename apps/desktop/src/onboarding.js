const statusLabels = {
  failed: 'Local runtime needs attention',
  needs_project: 'Choose a project to begin',
  ready: 'Local runtime is ready',
  starting: 'Starting the isolated Loop runtime…',
  stopped: 'Local runtime is stopped',
  stopping: 'Stopping the local runtime…',
};

const dot = document.querySelector('#status-dot');
const error = document.querySelector('#error');
const projectStatus = document.querySelector('#project-status');
const providerForm = document.querySelector('#provider-form');
const providerStatus = document.querySelector('#provider-status');
const recovery = document.querySelector('#recovery');
const retry = document.querySelector('#retry');
const runtimeStatus = document.querySelector('#runtime-status');
const selectProject = document.querySelector('#select-project');

function render(state) {
  runtimeStatus.textContent = statusLabels[state.runtime] ?? 'Checking local runtime…';
  projectStatus.textContent = state.project
    ? `Authorized project: ${state.project.name}`
    : 'No project selected.';
  providerStatus.textContent = state.provider
    ? `Configured provider: ${state.provider} · ${state.providerStorage === 'encrypted' ? 'encrypted on this device' : 'this session only'}`
    : 'No model provider configured.';
  recovery.hidden = !state.recoveryRequired;
  error.hidden = !state.lastError;
  error.textContent = state.lastError ?? '';
  retry.hidden = state.runtime !== 'failed' || !state.project;
  const busy = state.runtime === 'starting' || state.runtime === 'stopping';
  selectProject.disabled = busy;
  retry.disabled = busy;
  dot.className = `dot ${state.runtime === 'ready' ? 'ready' : busy ? 'busy' : state.runtime === 'failed' ? 'failed' : ''}`;
}

selectProject.addEventListener('click', async () =>
  render(await window.loopDesktop.selectProject()),
);
providerForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const provider = document.querySelector('#provider').value;
  const apiKeyInput = document.querySelector('#api-key');
  const state = await window.loopDesktop.configureProvider(provider, apiKeyInput.value);
  apiKeyInput.value = '';
  render(state);
});
retry.addEventListener('click', async () => render(await window.loopDesktop.restartRuntime()));
window.loopDesktop.onStateChange(() => {
  const state = window.loopDesktop.getCachedState();
  if (state) render(state);
});
window.loopDesktop.getState().then(render);
