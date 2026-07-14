// API origin. Empty (default) = same-origin: the local dev proxy and the
// single-origin nginx build both keep working unchanged. Set VITE_API_BASE_URL
// at build time to point a statically hosted frontend (e.g. GitHub Pages) at
// the tunnelled API on another origin — small JSON/SSE responses ride the
// tunnel fine, unlike the large static bundle.
export const API_BASE: string = import.meta.env.VITE_API_BASE_URL ?? "";
