import { useEffect, useState } from 'react'
import { apiGet } from './api/client'

export default function App() {
  const [health, setHealth] = useState<string>('checking...')
  useEffect(() => {
    apiGet<{ status: string }>('/health').then(h => setHealth(h.status)).catch(e => setHealth(`error: ${e.message}`))
  }, [])
  return (
    <main className="min-h-screen bg-neutral-950 text-neutral-100 flex items-center justify-center">
      <div className="text-center space-y-2">
        <h1 className="text-3xl font-semibold tracking-tight">VCF Doctor</h1>
        <p className="text-neutral-400">What's wrong, what changed, and what should I do about it?</p>
        <p className="text-sm text-neutral-500">backend: {health}</p>
      </div>
    </main>
  )
}
