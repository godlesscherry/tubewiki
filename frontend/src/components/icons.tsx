// Lean inline icon set (stroke = currentColor). No icon library.
import type { ReactNode } from 'react'

export interface IconProps {
  size?: number
  className?: string
}

function Svg({
  size = 16,
  className,
  strokeWidth = 1.7,
  children,
}: IconProps & { children: ReactNode; strokeWidth?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  )
}

export function IconBrand(props: IconProps) {
  return (
    <Svg {...props} strokeWidth={1.6}>
      <path d="M13.5 2.5 4.4 14.2h6.2l-1 7.3 9.1-11.7h-6.2l1-7.3Z" />
    </Svg>
  )
}

export function IconExtract(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M12 3v11" />
      <path d="m8 10 4 4 4-4" />
      <path d="M4 16.5v3A1.5 1.5 0 0 0 5.5 21h13a1.5 1.5 0 0 0 1.5-1.5v-3" />
    </Svg>
  )
}

export function IconRefresh(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M20.5 12a8.5 8.5 0 1 1-2.5-6" />
      <path d="M20.5 3.5V9H15" />
    </Svg>
  )
}

export function IconTrash(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M3.5 6.2h17" />
      <path d="M8.5 6.2V4.4a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v1.8" />
      <path d="m18.7 6.2-1 13.6a2 2 0 0 1-2 1.9H8.3a2 2 0 0 1-2-1.9L5.3 6.2" />
      <path d="M10 10.8v6.2M14 10.8v6.2" />
    </Svg>
  )
}

export function IconSearch(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.4-3.4" />
    </Svg>
  )
}

export function IconClose(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M18 6 6 18M6 6l12 12" />
    </Svg>
  )
}

export function IconCheck(props: IconProps) {
  return (
    <Svg {...props} strokeWidth={2}>
      <path d="m20 6.5-10.5 11L4 12" />
    </Svg>
  )
}

export function IconAlert(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M10.4 4.3 2.8 17.6a1.9 1.9 0 0 0 1.6 2.9h15.2a1.9 1.9 0 0 0 1.6-2.9L13.6 4.3a1.9 1.9 0 0 0-3.2 0Z" />
      <path d="M12 9.4v4.1" />
      <path d="M12 17.1h.01" />
    </Svg>
  )
}

export function IconPlay(props: IconProps) {
  return (
    <Svg {...props} strokeWidth={1}>
      <path d="M8 5.2v13.6L19.4 12 8 5.2Z" fill="currentColor" stroke="none" />
    </Svg>
  )
}

export function IconFile(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M14 3.5H7.2a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h9.6a2 2 0 0 0 2-2V8.3L14 3.5Z" />
      <path d="M14 3.5v4.8h4.8" />
      <path d="M9 13.2h6M9 16.6h4" />
    </Svg>
  )
}

export function IconFolder(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M3.5 7.2a2 2 0 0 1 2-2h3.8l2 2h7.2a2 2 0 0 1 2 2v9.4a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2V7.2Z" />
    </Svg>
  )
}

export function IconExternal(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M14 3.5h6.5V10" />
      <path d="M20.5 3.5 10.5 13.5" />
      <path d="M18.5 13.5v6a1.8 1.8 0 0 1-1.8 1.8H5.3a1.8 1.8 0 0 1-1.8-1.8V8.1a1.8 1.8 0 0 1 1.8-1.8h6" />
    </Svg>
  )
}

export function IconYouTube(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="2.4" y="5.4" width="19.2" height="13.2" rx="4" />
      <path d="M10.2 9.2v5.6L15 12l-4.8-2.8Z" fill="currentColor" stroke="none" />
    </Svg>
  )
}

export function IconGear(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="12" cy="12" r="3.2" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1.03 1.56V21a2 2 0 1 1-4 0v-.09A1.7 1.7 0 0 0 8.9 19.3a1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 4.7 15a1.7 1.7 0 0 0-1.56-1.03H3a2 2 0 1 1 0-4h.09A1.7 1.7 0 0 0 4.7 8.9a1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.7 1.7 0 0 0 9 4.7h.09A1.7 1.7 0 0 0 10.1 3.14V3a2 2 0 1 1 4 0v.09a1.7 1.7 0 0 0 1.03 1.56 1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.7 1.7 0 0 0 19.3 9v.09a1.7 1.7 0 0 0 1.56 1.03H21a2 2 0 1 1 0 4h-.09A1.7 1.7 0 0 0 19.4 15Z" />
    </Svg>
  )
}

export function IconChevronLeft(props: IconProps) {
  return (
    <Svg {...props} strokeWidth={2}>
      <path d="m15 6-6 6 6 6" />
    </Svg>
  )
}

export function IconSparkle(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M12 3.2 13.9 8.3 19 10.2l-5.1 1.9L12 17.2l-1.9-5.1L5 10.2l5.1-1.9L12 3.2Z" />
    </Svg>
  )
}
