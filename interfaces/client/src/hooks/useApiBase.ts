const API_BASE = import.meta.env.VITE_API_BASE || "/api";

export function useApiBase() {
  return API_BASE;
}
