/**
 * Single source of truth for user-visible overlay version strings.
 * Bump IN LOCKSTEP with apps/overlay/package.json "version".
 */
export const KASHI_VERSION = '0.6.1';

/**
 * The extension build this overlay was tested against. A hello from any other
 * client version prints a loud terminal warning (stale-extension detector —
 * the extension does NOT auto-rebuild from source).
 */
export const EXPECTED_EXTENSION = 'kashi-extension/0.1.11';
