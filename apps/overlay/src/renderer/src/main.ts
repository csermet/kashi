/**
 * Renderer entry. Phase 2 adds the line renderer + extrapolation-clock driven
 * highlighting. All dynamic text is written via textContent (never innerHTML).
 */
const line = document.getElementById('lyric-line');
if (line) {
  line.textContent = 'Kashi overlay — skeleton';
}

export {};
