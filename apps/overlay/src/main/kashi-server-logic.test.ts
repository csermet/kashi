import { describe, expect, it } from 'vitest';
import { cacheFileName, normalizeServerUrl } from './kashi-server-logic.js';

describe('normalizeServerUrl', () => {
  it('accepts http(s) and strips trailing slashes', () => {
    expect(normalizeServerUrl('http://cnr-intel:8080/')).toBe('http://cnr-intel:8080');
    expect(normalizeServerUrl('https://kashi.example.com')).toBe('https://kashi.example.com');
  });

  it('rejects garbage', () => {
    for (const bad of [null, 42, '', 'ftp://x', 'not a url', 'http://has space']) {
      expect(normalizeServerUrl(bad)).toBeNull();
    }
  });
});

describe('cacheFileName', () => {
  it('is stable and path-safe', () => {
    expect(cacheFileName('youtube', 'dQw4w9WgXcQ')).toBe('youtube_dQw4w9WgXcQ_v1.json');
    expect(cacheFileName('upload', 'a/b')).not.toContain('/');
  });
});
