/** Explicit offline demo works in development and packaged production builds. */
export function isDemoMode() {
  return import.meta.env.VITE_WELLIO_MODE === 'demo' || import.meta.env.VITE_WELLIO_PREVIEW === '1'
}
