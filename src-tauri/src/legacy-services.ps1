$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $env:RIVULET_INSTALL_ROOT).ProviderPath
$desktop = [int]$env:RIVULET_DESKTOP_PID
$python = Join-Path $root 'runtime\python.exe'
$node = Join-Path $root 'fatfish\NapCat\node.exe'
$ports = @(8088, 18091, 18095, 18096, 18097, 18098, 18099, 18100)
$scripts = @('main.py', 'memory\api.py', 'workspace\api.py', 'qq_service.py', 'bili_service.py',
             'setup_service.py', 'persona_service.py', 'mood_service.py')
$scripts += Join-Path $root 'core\main.py'
$scripts += @($scripts | Where-Object { $_ -notmatch '^(?:main\.py|[A-Z]:)' } |
              ForEach-Object { Join-Path $root "services\$_" })
$oneScript = '^(?:"[^"]+"|\S+)\s+(?:"(?<script>[^"]+)"|(?<script>\S+))\s*$'

$processes = @(Get-CimInstance Win32_Process)
$byId = @{}
$owned = @{}
$closedPreview = @{}
foreach ($proc in $processes) {
    $byId[[int]$proc.ProcessId] = $proc
    if ($proc.ExecutablePath -eq $python -and $proc.CommandLine -match $oneScript -and
        ($Matches['script'].Replace('/', '\') -in $scripts)) {
        $owned[[int]$proc.ProcessId] = $proc
    }
    if ($proc.ProcessId -ne $desktop -and $proc.ExecutablePath -and
        (Split-Path -Parent $proc.ExecutablePath) -eq $root -and
        $proc.Name -match '^rivulet(?:ruo|若|[-_]ruo)(?:[-_.].*)?\.exe$') {
        $activity = Get-Process -Id $proc.ProcessId -ErrorAction SilentlyContinue
        if ($activity -and $activity.MainWindowHandle -eq 0 -and
            $proc.Name -match '^RivuletRuo-v0\.10\.2-preview\.[12]-win64\.exe$' -and
            $proc.CreationDate -lt (Get-Date).AddSeconds(-45)) {
            $closedPreview[[int]$proc.ProcessId] = $proc
        } elseif ($activity) {
            Write-Output 'WINDOW'
            exit 2
        }
    }
}

function Get-LiveParent($proc) {
    $parent = $byId[[int]$proc.ParentProcessId]
    if ($parent -and $parent.CreationDate -le $proc.CreationDate) {
        return $parent
    }
}

$retire = @{}
foreach ($proc in $owned.Values) {
    $ancestor = $proc
    while ($parent = Get-LiveParent $ancestor) {
        if ($closedPreview.ContainsKey([int]$parent.ProcessId)) {
            $parent = $null
            break
        }
        if (!$owned.ContainsKey([int]$parent.ProcessId)) {
            break
        }
        $ancestor = $parent
    }
    if (!$parent) {
        $retire[[int]$proc.ProcessId] = $proc
    }
}
$listeners = @(Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -in $ports })
foreach ($listener in $listeners) {
    if (!$retire.ContainsKey([int]$listener.OwningProcess)) {
        Write-Output "PORT $($listener.LocalPort)"
        exit 2
    }
}

foreach ($proc in $processes) {
    if ($proc.ExecutablePath -eq $python -and
        $proc.CommandLine -match '(?i)(?:^|[\s"\\/])publish\.py(?=$|[\s"])') {
        $parent = Get-LiveParent $proc
        if ($parent -and $retire.ContainsKey([int]$parent.ProcessId)) {
            Write-Output 'PUBLISHING'
            exit 2
        }
    }
}

function Stop-SameProcess($proc) {
    $pidToStop = [int]$proc.ProcessId
    $current = Get-CimInstance Win32_Process -Filter "ProcessId = $pidToStop"
    if ($current -and $current.ExecutablePath -eq $proc.ExecutablePath -and
        $current.CreationDate -eq $proc.CreationDate) {
        $instance = Get-Process -Id $pidToStop -ErrorAction SilentlyContinue
        if ($instance) {
            $null = $instance.Handle
            if ($instance.Path -ne $proc.ExecutablePath -or
                [Math]::Abs(($instance.StartTime - $proc.CreationDate).TotalSeconds) -ge 1) { return }
            if (!$instance.HasExited) {
                $instance.Kill()
                if (!$instance.WaitForExit(10000)) { throw "Legacy service $pidToStop did not stop." }
            }
        }
    }
}

# Stop each owned Node supervisor before its workers, which it can respawn.
# The second snapshot includes workers that were starting during shutdown.
for ($pass = 0; $pass -lt 2; $pass++) {
    $children = @(Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" |
        Where-Object {$_.ExecutablePath -eq $node})
    foreach ($proc in $children) { $byId[[int]$proc.ProcessId] = $proc }
    $qqTree = foreach ($proc in $children) {
        $ancestor = Get-LiveParent $proc
        $depth = 0
        while ($ancestor -and $ancestor.ExecutablePath -eq $node) {
            $depth++
            $ancestor = Get-LiveParent $ancestor
        }
        if ($ancestor -and $retire.ContainsKey([int]$ancestor.ProcessId) -and
            $ancestor.CommandLine -match 'qq_service\.py(?=$|[\s"])') {
            [pscustomobject]@{ Depth = $depth; Process = $proc }
        }
    }
    foreach ($entry in ($qqTree | Sort-Object Depth)) {
        Stop-SameProcess $entry.Process
    }
}
foreach ($proc in $retire.Values) {
    Stop-SameProcess $proc
}
foreach ($proc in $closedPreview.Values) {
    Stop-SameProcess $proc
}
Write-Output "READY $($retire.Count)"
