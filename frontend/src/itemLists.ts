// Item list labels shared by the sheet overlay and the My item lists page (ADR 0017).

import type { Visibility } from './api'

export const VISIBILITY: Record<Visibility, { label: string; hint: string }> = {
  private: { label: 'Private', hint: 'only you' },
  cost_center: { label: 'Cost center', hint: 'enabled planners' },
  public: { label: 'Public', hint: 'everyone' },
}
