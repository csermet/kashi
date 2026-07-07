/**
 * Preload: exposes a deliberately narrow bridge. The renderer never gets
 * direct Node/Electron access (contextIsolation + sandbox stay on).
 */
import { contextBridge } from 'electron';

contextBridge.exposeInMainWorld('kashi', {
  version: '0.1.0',
});
