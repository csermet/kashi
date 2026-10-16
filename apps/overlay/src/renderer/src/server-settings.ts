/**
 * Server settings prompt (Faz 6 P6 — the tray's "Server settings…"): edits
 * server_url/server_api_key through the preload bridge, killing the
 * hand-edit-kashi-settings.json trap (the running app's flush overwrote
 * manual edits — twice in the field). Enter = save, Escape = cancel.
 *
 * The URL arrives via ?url=; the key NEVER rides the query string — ?hasKey=1
 * only signals that one exists, and an untouched empty key field means
 * "keep the stored key". Clearing the URL disables the server (both fields
 * are wiped main-side — serverless mode is byte-identical to before).
 */
interface PromptBridge {
  submitServerSettings(url: string, key: string | null, mirror: string): void;
  cancelPrompt(): void;
}

const kashi = (window as unknown as { kashi: PromptBridge }).kashi;

const urlInput = document.getElementById('url-input') as HTMLInputElement;
const keyInput = document.getElementById('key-input') as HTMLInputElement;
const mirrorInput = document.getElementById('mirror-input') as HTMLInputElement;
const params = new URLSearchParams(window.location.search);
urlInput.value = params.get('url') ?? '';
mirrorInput.value = params.get('mirror') ?? '';
if (params.get('hasKey') === '1') {
  keyInput.placeholder = '•••• (unchanged)';
}

function submit(): void {
  const url = urlInput.value.trim();
  // Empty key field + an existing key = keep it (null marks "unchanged").
  const key = keyInput.value.trim();
  kashi.submitServerSettings(
    url,
    key === '' && params.get('hasKey') === '1' ? null : key,
    mirrorInput.value.trim(),
  );
}

document.getElementById('ok')?.addEventListener('click', submit);
document.getElementById('cancel')?.addEventListener('click', () => kashi.cancelPrompt());
window.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') submit();
  if (event.key === 'Escape') kashi.cancelPrompt();
});
urlInput.focus();

// Module scope isolation: both prompt pages are otherwise plain scripts and
// tsc would merge their globals ('kashi' redeclare).
export {};
