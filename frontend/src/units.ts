// The cost center a page shows: the one chosen last (shared by the capacity and planning pages),
// if the page lists it; else the first one the user may edit, else the first one they may read.
export function pickUnit<U extends { code: string; can_write: boolean }>(units: U[], wanted: string): string {
  if (units.some((u) => u.code === wanted)) return wanted
  return (units.find((u) => u.can_write) ?? units[0])?.code ?? ''
}
