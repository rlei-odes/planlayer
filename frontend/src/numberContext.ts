import { createContext, useContext } from 'react'
import type { Scale } from './amounts'

// The user's scale and how to change it, for the sheets and overviews that offer the 1 · k · M
// toggle. Amounts read the format itself from amounts.ts; the scale here re-renders what depends
// on it (a grid's memoised cells).
export const ScaleContext = createContext<{ scale: Scale; setScale: (scale: Scale) => void }>({
  scale: 'full',
  setScale: () => undefined,
})

export const useScale = () => useContext(ScaleContext)
