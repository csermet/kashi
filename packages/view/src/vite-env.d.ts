/**
 * The two vite features the package's TESTS use (vitest runs them), declared
 * here because the package does not depend on vite's own client types:
 * importing a file as text (`?raw`) and globbing modules.
 */
declare module '*?raw' {
  const content: string;
  export default content;
}

interface ImportMeta {
  glob<T>(
    patterns: string | string[],
    options: { query?: string; import?: string; eager: true },
  ): Record<string, T>;
}
