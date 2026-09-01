import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'
import type { AssistantTask, Change, Finding, Resource, ScriptFormat } from '@/types'

export interface AssistantSeed {
  task: AssistantTask
  question?: string
  findings?: Finding[]
  changes?: Change[]
  resources?: Resource[]
  scriptFormat?: ScriptFormat
  autoSend?: boolean
}

interface AssistantDrawerState {
  open: boolean
  seed: AssistantSeed | null
  seedKey: number
  openDrawer: (seed?: AssistantSeed) => void
  closeDrawer: () => void
}

const Ctx = createContext<AssistantDrawerState | null>(null)

export function AssistantStateProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false)
  const [seed, setSeed] = useState<AssistantSeed | null>(null)
  const [seedKey, setSeedKey] = useState(0)
  const openDrawer = useCallback((s?: AssistantSeed) => {
    if (s) { setSeed(s); setSeedKey(k => k + 1) }
    setOpen(true)
  }, [])
  const closeDrawer = useCallback(() => setOpen(false), [])
  const value = useMemo(() => ({ open, seed, seedKey, openDrawer, closeDrawer }), [open, seed, seedKey, openDrawer, closeDrawer])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useAssistantDrawer(): AssistantDrawerState {
  const v = useContext(Ctx)
  if (!v) throw new Error('useAssistantDrawer outside provider')
  return v
}
