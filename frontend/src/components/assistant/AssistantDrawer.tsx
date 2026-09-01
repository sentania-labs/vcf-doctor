import { useMemo } from 'react'
import { Bot, Maximize2 } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { Drawer } from '@/components/ui'
import { useAssistantDrawer } from '@/state/AssistantState'
import { AssistantPanel } from './AssistantPanel'

export function AssistantDrawer() {
  const { open, seed, seedKey, closeDrawer } = useAssistantDrawer()
  const nav = useNavigate()
  const empty = useMemo(() => ({ findings: [], changes: [], resources: [], events: [] }), [])
  return (
    <Drawer open={open} onClose={closeDrawer} width="w-[560px]"
      header={
        <div className="flex items-center gap-3 min-w-0 flex-1">
          <div className="h-8 w-8 rounded-md bg-info-bg text-accent flex items-center justify-center shrink-0"><Bot size={16} /></div>
          <div className="min-w-0">
            <h2 className="text-[15px] font-semibold tracking-tight">Assistant</h2>
            <p className="text-xs text-muted">Read-only guidance from the evidence in view</p>
          </div>
          <button onClick={() => { closeDrawer(); nav('/assistant') }} className="ml-auto text-muted hover:text-fg rounded-md p-1 transition-colors" title="Open full page"><Maximize2 size={15} /></button>
        </div>
      }>
      <AssistantPanel seed={seed} seedKey={seedKey} context={empty} compact />
    </Drawer>
  )
}
