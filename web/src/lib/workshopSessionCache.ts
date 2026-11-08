export const WORKSHOP_SESSION_CACHE_KEY = "novel-os.workshop.active-session";

export function forgetWorkshopSession(id: string) {
  try {
    if (localStorage.getItem(WORKSHOP_SESSION_CACHE_KEY) === id) {
      localStorage.removeItem(WORKSHOP_SESSION_CACHE_KEY);
    }
  } catch { /* The server remains authoritative if storage is unavailable. */ }
}
