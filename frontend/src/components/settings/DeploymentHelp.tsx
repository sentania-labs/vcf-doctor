export default function DeploymentHelp({ children }: { children: React.ReactNode }) {
  return <details className="text-xs text-muted"><summary className="cursor-pointer">Deployment details</summary><div className="mt-1 font-mono break-all">{children}</div></details>
}
