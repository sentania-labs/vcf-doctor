## Investigation (read only)

These commands only read state. They assume an existing PowerCLI session; connect first with `Connect-VIServer` using credentials you supply. No vCenter address or credential is embedded here.

```powershell
# READ ONLY: inspect the disconnected host and its context. Nothing below changes state.
$HostName = 'esx03.wld01.vcf.example'

# Current connection, power, and maintenance state as vCenter sees it
Get-VMHost -Name $HostName |
    Select-Object Name, ConnectionState, PowerState, State, Parent

# Recent tasks and events for this host (last 2 hours) to locate the disconnect time
Get-VIEvent -Entity (Get-VMHost -Name $HostName) -Start (Get-Date).AddHours(-2) -MaxSamples 200 |
    Sort-Object CreatedTime |
    Select-Object CreatedTime, FullFormattedMessage

# VMs vCenter last placed on this host and their last known power state
Get-VMHost -Name $HostName | Get-VM |
    Select-Object Name, PowerState, NumCpu, MemoryMB

# Datastores the host participates in, with free space
Get-VMHost -Name $HostName | Get-Datastore |
    Select-Object Name, Type, CapacityGB, FreeSpaceGB, Accessible

# Management network reachability from the machine running this script
Test-NetConnection -ComputerName $HostName -Port 443
Test-NetConnection -ComputerName $HostName -Port 902
```

## Modification (changes environment)

Run these only after the read-only section confirms the host is reachable and its services are healthy. Each command changes the environment.

```powershell
# MODIFIES ENVIRONMENT: attempts to re-establish the vCenter to host management connection.
# Requires host root or equivalent credentials; supply them at the prompt.
$HostName = 'esx03.wld01.vcf.example'
$HostCred = Get-Credential -Message "Root credential for $HostName"

# Reconnect the host. This restarts the vpxa agent on the host; running VMs are not affected.
Set-VMHost -VMHost (Get-VMHost -Name $HostName) -State Connected -Confirm:$true
```

```powershell
# MODIFIES ENVIRONMENT: restarts management agents on the host over SSH.
# Only use if reconnect fails and the host answers on the network. Requires SSH enabled on the host.
# Running VMs keep running, but the host is briefly unmanageable while hostd and vpxa restart.
#   ssh root@esx03.wld01.vcf.example '/etc/init.d/hostd restart && /etc/init.d/vpxa restart'
```

Do not reboot the host while its seven resident VMs are reported as powered on. If the host is unreachable on the management network, escalate as a network incident instead of forcing changes from vCenter.
