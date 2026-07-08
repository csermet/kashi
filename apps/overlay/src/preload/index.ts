/**
 * Preload: exposes a deliberately narrow, subscribe-only bridge. The renderer
 * never gets direct Node/Electron access (contextIsolation + sandbox stay on).
 */
import { contextBridge, ipcRenderer } from 'electron';
import { KASHI_VERSION } from '../shared/version.js';

type Listener = (payload: unknown) => void;

function subscribe(channel: string) {
  return (listener: Listener) => {
    const wrapped = (_event: unknown, payload: unknown) => listener(payload);
    ipcRenderer.on(channel, wrapped);
    return () => ipcRenderer.removeListener(channel, wrapped);
  };
}

contextBridge.exposeInMainWorld('kashi', {
  version: KASHI_VERSION,
  onTrack: subscribe('kashi:track'),
  onPlayback: subscribe('kashi:playback'),
  onLyrics: subscribe('kashi:lyrics'),
  onConnection: subscribe('kashi:connection'),
  onSourceGone: subscribe('kashi:source-gone'),
  onSettings: subscribe('kashi:settings'),
  /** Flip window interactivity (click-through ↔ draggable) on hover. */
  setInteractive: (interactive: boolean) =>
    ipcRenderer.send('kashi:set-interactive', interactive === true),
  /** Manual window dragging (app-region would swallow mouse events). */
  dragStart: () => ipcRenderer.send('kashi:drag-start'),
  dragEnd: () => ipcRenderer.send('kashi:drag-end'),
  /** Ctrl+scroll opacity nudge; main clamps, persists and broadcasts back. */
  adjustOpacity: (deltaSteps: number) => ipcRenderer.send('kashi:adjust-opacity', deltaSteps),
  /** Right-click on the box: pop the Kashi menu (same one the tray serves). */
  openMenu: () => ipcRenderer.send('kashi:open-menu'),
  /** Diagnostic line, printed to the overlay's terminal. */
  log: (line: string) => ipcRenderer.send('kashi:rlog', String(line)),
});
