// Production is served with the /api reverse proxy. Keep the separate local
// backend only as the development default; explicit build configuration wins.
export const API_BASE = import.meta.env.VITE_API_BASE ??
  (import.meta.env.DEV ? "http://localhost:8000" : "");
