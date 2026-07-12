/**
 * Timing-offset prompt (the tray's "Other…"): a number input whose value goes
 * back through the preload bridge; main clamps to ±500 and persists. Enter =
 * OK, Escape = cancel; empty/garbage input cancels (changes nothing). The
 * current value arrives via the ?value= query param set by main.
 */
interface PromptBridge {
  submitTimingOffset(value: number): void;
  cancelPrompt(): void;
}

const kashi = (window as unknown as { kashi: PromptBridge }).kashi;

const input = document.getElementById('offset-input') as HTMLInputElement;
const initial = Number(new URLSearchParams(window.location.search).get('value'));
input.value = String(Number.isFinite(initial) ? initial : 0);

function submit(): void {
  const value = input.valueAsNumber;
  if (Number.isFinite(value)) kashi.submitTimingOffset(value);
  else kashi.cancelPrompt();
}

document.getElementById('ok')?.addEventListener('click', submit);
document.getElementById('cancel')?.addEventListener('click', () => kashi.cancelPrompt());
window.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') submit();
  if (event.key === 'Escape') kashi.cancelPrompt();
});
input.focus();
input.select();
