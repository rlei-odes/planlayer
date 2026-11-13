// Inline SVG icons (self-hosted: no icon font, no CDN). 20×20, stroked in currentColor.

import type { ReactNode } from 'react'

function Icon({ children, size = 18 }: { children: ReactNode; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {children}
    </svg>
  )
}

export const LogoMark = () => (
  <svg width="26" height="26" viewBox="0 0 26 26" aria-hidden="true">
    <rect width="26" height="26" rx="7" fill="var(--accent)" />
    <path d="M6 15.5 13 19l7-3.5" fill="none" stroke="#fff" strokeOpacity=".55" strokeWidth="1.7" strokeLinejoin="round" />
    <path d="M6 11.5 13 15l7-3.5L13 8z" fill="#fff" />
  </svg>
)

export const PlanningIcon = () => (
  <Icon>
    <rect x="3" y="3.5" width="14" height="13" rx="2" />
    <path d="M3 8h14M8 8v8.5" />
  </Icon>
)

// A gauge: how much a work center runs.
export const CapacityIcon = () => (
  <Icon>
    <path d="M3.5 14.5a6.5 6.5 0 1 1 13 0" />
    <path d="m10 14.5 3-4.5" />
    <path d="M3.5 14.5h13" />
  </Icon>
)

// Two people: the cost center's workforce.
export const WorkforceIcon = () => (
  <Icon>
    <circle cx="7.5" cy="7" r="2.6" />
    <path d="M2.8 16.5c.6-2.7 2.4-4.2 4.7-4.2s4.1 1.5 4.7 4.2" />
    <circle cx="13.8" cy="8" r="2.1" />
    <path d="M13.3 12.4c2 0 3.4 1.2 3.9 3.4" />
  </Icon>
)

// One source branching to several receivers: costs passed on.
export const ForwardingIcon = () => (
  <Icon>
    <circle cx="4.5" cy="10" r="1.8" />
    <path d="M6.3 10h3.2M9.5 10c2 0 2.5-5 5-5M9.5 10c2 0 2.5 5 5 5M9.5 10h5" />
    <path d="m13 3.5 1.5 1.5L13 6.5M13 13.5l1.5 1.5-1.5 1.5" />
  </Icon>
)

// A gear: installation settings.
export const SettingsIcon = () => (
  <Icon>
    <circle cx="10" cy="10" r="2.6" />
    <path d="M10 2.8v2.1M10 15.1v2.1M2.8 10h2.1M15.1 10h2.1M4.9 4.9l1.5 1.5M13.6 13.6l1.5 1.5M4.9 15.1l1.5-1.5M13.6 6.4l1.5-1.5" />
  </Icon>
)

export const StatusIcon = () => (
  <Icon>
    <circle cx="10" cy="10" r="7" />
    <path d="m7 10.2 2 2 4-4.4" />
  </Icon>
)

export const SetupIcon = () => (
  <Icon>
    <path d="M4 6h8M15 6h1M4 14h1M8 14h8" />
    <circle cx="13.5" cy="6" r="1.7" />
    <circle cx="6.5" cy="14" r="1.7" />
  </Icon>
)

export const PublishIcon = () => (
  <Icon>
    <path d="M10 12.5V3.5M6.5 7 10 3.5 13.5 7" />
    <path d="M3.5 12v2.5a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2V12" />
  </Icon>
)

export const MasterDataIcon = () => (
  <Icon>
    <ellipse cx="10" cy="5" rx="6" ry="2.2" />
    <path d="M4 5v10c0 1.2 2.7 2.2 6 2.2s6-1 6-2.2V5M4 10c0 1.2 2.7 2.2 6 2.2s6-1 6-2.2" />
  </Icon>
)

export const ResponsibilitiesIcon = () => (
  <Icon>
    <circle cx="7.5" cy="7" r="2.6" />
    <path d="M2.8 16c.6-2.6 2.4-4 4.7-4s4.1 1.4 4.7 4" />
    <path d="M13 4.6a2.6 2.6 0 0 1 0 4.8M14.5 12.2c1.4.5 2.4 1.8 2.7 3.8" />
  </Icon>
)

export const UsersIcon = () => (
  <Icon>
    <circle cx="10" cy="7" r="3" />
    <path d="M4.5 16.5c.7-3 2.8-4.6 5.5-4.6s4.8 1.6 5.5 4.6" />
  </Icon>
)

export const CollapseIcon = ({ collapsed }: { collapsed: boolean }) => (
  <Icon size={16}>
    <rect x="3" y="3.5" width="14" height="13" rx="2" />
    <path d="M8 3.5v13" />
    <path d={collapsed ? 'm11.5 8 2 2-2 2' : 'm13.5 8-2 2 2 2'} />
  </Icon>
)

export const UndoIcon = () => (
  <Icon size={16}>
    <path d="M7.5 5 4 8.5 7.5 12" />
    <path d="M4 8.5h7.5a4.5 4.5 0 0 1 0 9H9" />
  </Icon>
)

export const RedoIcon = () => (
  <Icon size={16}>
    <path d="M12.5 5 16 8.5 12.5 12" />
    <path d="M16 8.5H8.5a4.5 4.5 0 0 0 0 9H11" />
  </Icon>
)

export const LogoutIcon = () => (
  <Icon size={16}>
    <path d="M8 4H5.5a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2H8M12.5 6.5 16 10l-3.5 3.5M16 10H8" />
  </Icon>
)

// A speech bubble: line notes (ADR 0017).
export const NotesIcon = () => (
  <Icon>
    <path d="M4 4.5h12a1.5 1.5 0 0 1 1.5 1.5v7a1.5 1.5 0 0 1-1.5 1.5H9l-3.5 3v-3H4A1.5 1.5 0 0 1 2.5 13V6A1.5 1.5 0 0 1 4 4.5z" />
    <path d="M6 8.5h8M6 11h5" />
  </Icon>
)

// A list with bullets and a sum line: item lists (ADR 0017).
export const ItemListIcon = () => (
  <Icon>
    <path d="M7.5 5h9M7.5 9h9M7.5 13h9" />
    <circle cx="4.2" cy="5" r=".6" fill="currentColor" />
    <circle cx="4.2" cy="9" r=".6" fill="currentColor" />
    <circle cx="4.2" cy="13" r=".6" fill="currentColor" />
    <path d="M10.5 16.5h6" />
  </Icon>
)
