/**
 * Preload: exposes a deliberately narrow, subscribe-only bridge. The renderer
 * never gets direct Node/Electron access (contextIsolation + sandbox stay on).
 */
import { contextBridge, ipcRenderer } from 'electron';

type Listener = (payload: unknown) => void;

function subscribe(channel: string) {
  return (listener: Listener) => {
    const wrapped = (_event: unknown, payload: unknown) => listener(payload);
    ipcRenderer.on(channel, wrapped);
    return () => ipcRenderer.removeListener(channel, wrapped);
  };
}

contextBridge.exposeInMainWorld('kashi', {
  version: '0.1.0',
  onTrack: subscribe('kashi:track'),
  onPlayback: subscribe('kashi:playback'),
  onLyrics: subscribe('kashi:lyrics'),
  onConnection: subscribe('kashi:connection'),
  /** Flip window interactivity (click-through ↔ draggable) on hover. */
  setInteractive: (interactive: boolean) =>
    ipcRenderer.send('kashi:set-interactive', interactive === true),
});
