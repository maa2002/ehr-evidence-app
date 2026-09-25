<#
.SYNOPSIS
    Applies the destination configuration to the current process.

.DESCRIPTION
    Reads config/paths.json, creates the configured directories under the
    private workspace root (authoritative) or, for the approved runtime/cache
    categories only, under the runtime root, both inside
    G:\Industry Dataset, and sets the configured environment variables for
    this process only. Nothing is written to the user or machine
    environment, to Git configuration, to the inherited source tree or to
    the PostgreSQL service.
    Re-running the script is idempotent: directories are created only when
    missing and variables are set to the same resolved values.

    Preflight: the policy anchor, private root and runtime root are fixed in
    this script, so the configuration cannot move or widen them. Only the
    runtime/cache directory keys fixed below may name the runtime root; every
    other key stays in the private workspace root. The runtime root must
    already exist on a fixed NTFS volume (ehr_evidence_app.storage.runtime
    creates it with restrictive ACLs). Every directory,
    marker file and file-valued variable is checked before anything is
    created or set: plain absolute drive paths only (no UNC or device paths,
    alternate streams, reserved names, 8.3 short names or other ambiguous
    spellings), inside its fixed root, outside the source repository, and
    with no symlink, junction or other reparse point and no .git entry in any
    existing component. The complete mandatory variable set and its essential
    directory keys must be defined: destination variables as the directory or
    file entry of their fixed key, PYTEST_ADDOPTS and CLEARML_OFFLINE_MODE as
    their exact canonical values, and no other literal or template. Any
    failure aborts the run with no directory created and no variable set.

    Running the script by path and dot-sourcing it behave identically: both
    perform the full preflight and apply the same process environment.

.PARAMETER ConfigPath
    Path to the JSON destination configuration. Defaults to config/paths.json
    next to this repository's scripts directory.

.PARAMETER Quiet
    Suppress the per-variable summary table.

.EXAMPLE
    .\scripts\setup-destinations.ps1
    Applies destinations to the current PowerShell process.

.EXAMPLE
    . .\scripts\setup-destinations.ps1 -Quiet
    Dot-sourced: the same checks and the same process environment.
#>
[CmdletBinding()]
param(
    [string]$ConfigPath,
    [switch]$Quiet
)

# Resolve the script location in the body, after parameter binding. Windows
# PowerShell can leave $PSScriptRoot empty inside a parameter default when
# the script is dot-sourced from a -Command string.
$SetupScriptPath = $MyInvocation.MyCommand.Path
if (-not $SetupScriptPath) { $SetupScriptPath = $PSCommandPath }
if (-not $SetupScriptPath) {
    throw 'Cannot determine the setup script location; run or dot-source it by path.'
}
$SetupScriptDirectory = Split-Path -Parent $SetupScriptPath
if (-not $ConfigPath) {
    $ConfigPath = Join-Path $SetupScriptDirectory '..\config\paths.json'
}

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Fixed policy, mirrored from the Python storage policy module.
$PolicyAnchor = 'G:\Industry Dataset'
$PrivateRoot = 'G:\Industry Dataset\ehr-evidence-workspace'
$RuntimeRoot = 'G:\Industry Dataset\ehr-evidence-runtime'
# Directory keys that may use root "runtime"; every other key stays in the
# private workspace root.
$RuntimeDirectoryKeys = @(
    'runtime_root', 'temp', 'pytest_temp', 'pytest_cache', 'hf_home', 'hf_hub',
    'hf_assets', 'hf_datasets', 'torch_home', 'pip_cache', 'uv_cache', 'xdg_cache',
    'pycache', 'conda_pkgs', 'conda_envs', 'uv_project_env', 'venvs', 'models',
    'clearml_cache')
$RootNames = @{ 'workspace' = $PrivateRoot; 'runtime' = $RuntimeRoot }
$RuntimeParent = 'G:\Industry Dataset'
# Written by ehr_evidence_app.storage.runtime; holds the pinned identity digest.
$RuntimeRecordPath = $PrivateRoot + '\outputs\verification\runtime-storage-preflight.json'
$RepositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $SetupScriptDirectory '..'))
# Mandatory environment set and fixed bindings, mirrored from
# ehr_evidence_app.storage.policy. Every variable must be defined exactly once.
$DirectoryVariables = @{
    'EHR_WORKSPACE_ROOT' = 'workspace_root'; 'EHR_RUNTIME_ROOT' = 'runtime_root'
    'EHR_PREPARED_DATA_DIR' = 'prepared_data'
    'EHR_OUTPUTS_DIR' = 'outputs'; 'EHR_LOGS_DIR' = 'logs'; 'EHR_MODELS_DIR' = 'models'
    'EHR_CLEARML_DIR' = 'clearml_local'; 'TEMP' = 'temp'; 'TMP' = 'temp'; 'TMPDIR' = 'temp'
    'PYTHONPYCACHEPREFIX' = 'pycache'; 'HF_HOME' = 'hf_home'; 'HF_HUB_CACHE' = 'hf_hub'
    'HF_ASSETS_CACHE' = 'hf_assets'; 'HF_DATASETS_CACHE' = 'hf_datasets'
    'TORCH_HOME' = 'torch_home'; 'PIP_CACHE_DIR' = 'pip_cache'; 'UV_CACHE_DIR' = 'uv_cache'
    'UV_PROJECT_ENVIRONMENT' = 'uv_project_env'; 'XDG_CACHE_HOME' = 'xdg_cache'
    'CONDA_PKGS_DIRS' = 'conda_pkgs'; 'CONDA_ENVS_DIRS' = 'conda_envs'
    'PYTEST_DEBUG_TEMPROOT' = 'pytest_temp'; 'CLEARML_CACHE_DIR' = 'clearml_cache'
}
$FileVariables = @{ 'CLEARML_CONFIG_FILE' = 'clearml_local' }
$TemplateVariables = @{
    'PYTEST_ADDOPTS' = '--basetemp="${dir_posix:pytest_temp}" -o "cache_dir=${dir_posix:pytest_cache}"'
}
$LiteralVariables = @{ 'CLEARML_OFFLINE_MODE' = '1' }
$RequiredVariables = @($DirectoryVariables.Keys) + @($FileVariables.Keys) +
    @($TemplateVariables.Keys) + @($LiteralVariables.Keys)
$RequiredDirectoryKeys = @(@($DirectoryVariables.Values) + @($FileVariables.Values) +
    @('verification', 'pytest_temp', 'pytest_cache', 'test_scratch') | Sort-Object -Unique)
$ListVariables = @('CONDA_PKGS_DIRS', 'CONDA_ENVS_DIRS')
$ListDelimiters = [char[]]';,'
$PlaceholderPattern = '\$\{(dir|dir_posix):([A-Za-z0-9_]+)\}'
$ReservedDeviceNames = @('CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$')
foreach ($suffix in @('1', '2', '3', '4', '5', '6', '7', '8', '9',
        [string][char]0x00B9, [string][char]0x00B2, [string][char]0x00B3)) {
    $ReservedDeviceNames += @("COM$suffix", "LPT$suffix")
}

function Get-DestinationConfig {
    <#
    .SYNOPSIS
        Loads and minimally validates the JSON destination configuration.
    #>
    param([Parameter(Mandatory)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Destination configuration not found: $Path"
    }
    $config = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($field in 'workspace_root', 'directories', 'environment', 'policy') {
        if (@($config.PSObject.Properties.Name) -notcontains $field) {
            throw "Destination configuration is missing '$field': $Path"
        }
    }
    return $config
}

function Get-ComponentProblem {
    <#
    .SYNOPSIS
        Returns why one path component is unsafe on Windows, or $null.
    #>
    param([string]$Part)

    if ($Part.Length -eq 0) { return 'path has an empty component or a trailing separator' }
    if ($Part -eq '.' -or $Part -eq '..') { return "path contains a '.' or '..' component" }
    if ($Part -ne $Part.TrimEnd('.', ' ')) { return 'a path component ends with a dot or space' }
    if ($Part.IndexOfAny([char[]]'<>:"/|?*') -ge 0) {
        return 'a path component contains a reserved character or an alternate data stream'
    }
    $stem = $Part.Split('.')[0].TrimEnd(' ').ToUpperInvariant()
    if ($ReservedDeviceNames -contains $stem) { return 'a path component is a reserved device name' }
    if ($Part -match '~[0-9]') { return 'a path component looks like an 8.3 short name' }
    return $null
}

function Get-PathProblem {
    <#
    .SYNOPSIS
        Returns why a path is not a plain absolute Windows drive path, or $null.
    #>
    param([AllowNull()][object]$Path)

    if ($Path -isnot [string] -or $Path.Length -eq 0) { return 'path is empty or not text' }
    foreach ($character in $Path.ToCharArray()) {
        if ([int]$character -lt 32) { return 'path contains a control character' }
    }
    if ($Path.StartsWith('\\') -or $Path.StartsWith('//')) {
        return 'UNC and device paths are not permitted'
    }
    if ($Path.Contains('/')) { return 'forward slashes are ambiguous; use backslashes' }
    if ($Path -notmatch '^[A-Za-z]:\\') {
        return 'path must be absolute with a drive letter; relative and drive-relative paths are not permitted'
    }
    foreach ($part in $Path.Substring(3).Split('\')) {
        $problem = Get-ComponentProblem -Part $part
        if ($problem) { return $problem }
    }
    $full = [System.IO.Path]::GetFullPath($Path)
    if (-not $full.Equals($Path, [System.StringComparison]::OrdinalIgnoreCase)) {
        return 'path has an ambiguous Windows spelling'
    }
    return $null
}

function Get-NameProblem {
    <#
    .SYNOPSIS
        Returns why a value is not one safe file or directory name, or $null.
    #>
    param([AllowNull()][object]$Name)

    if ($Name -isnot [string] -or $Name.Contains('\') -or $Name.Contains('/')) {
        return 'name must be a single path component'
    }
    if ($Name -eq '.git') { return 'name must not be .git' }
    return Get-ComponentProblem -Part $Name
}

function Get-RelativeProblem {
    <#
    .SYNOPSIS
        Returns why a configured root-relative path is unsafe, or $null.
    #>
    param(
        [AllowNull()][object]$Relative,
        [string]$Root = $PrivateRoot
    )

    if ($Relative -isnot [string]) { return 'relative path is not text' }
    if ($Relative -eq '.') { return $null }
    return Get-PathProblem -Path ($Root + '\' + $Relative)
}

function Get-DirectoryRootProblem {
    <#
    .SYNOPSIS
        Returns why a directory entry's root field is not permitted, or $null.

    .DESCRIPTION
        The optional root field is "workspace" (the default) or "runtime".
        Only the fixed runtime/cache keys may name "runtime".
    #>
    param(
        [Parameter(Mandatory)][string]$Key,
        [AllowNull()][object]$Entry
    )

    if ($Entry -isnot [psobject] -or $Entry -is [string]) {
        return ('{0}: directory entry is not an object' -f $Key)
    }
    $name = 'workspace'
    if (@($Entry.PSObject.Properties.Name) -contains 'root') { $name = $Entry.root }
    if ($name -isnot [string] -or ($name -cne 'workspace' -and $name -cne 'runtime')) {
        return ('{0}: root must be "workspace" or "runtime"' -f $Key)
    }
    if ($name -ceq 'runtime' -and $RuntimeDirectoryKeys -cnotcontains $Key) {
        return ('{0} is a workspace-only category; only approved runtime/cache categories may use the runtime root {1}' -f $Key, $RuntimeRoot)
    }
    return $null
}

function Get-DirectoryRoot {
    <#
    .SYNOPSIS
        Returns the fixed root named by a validated directory entry.
    #>
    param([Parameter(Mandatory)][psobject]$Entry)

    if (@($Entry.PSObject.Properties.Name) -contains 'root') { return $RootNames[$Entry.root] }
    return $PrivateRoot
}

function Test-WithinRoot {
    <#
    .SYNOPSIS
        Returns $true when a path equals or lies beneath a root, by component.
    #>
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Root,
        [switch]$Strict
    )

    $comparison = [System.StringComparison]::OrdinalIgnoreCase
    $base = $Root.TrimEnd('\')
    if ($Path.StartsWith($base + '\', $comparison)) { return $true }
    return (-not $Strict) -and $Path.Equals($base, $comparison)
}

function Get-FixedPolicyProblem {
    <#
    .SYNOPSIS
        Returns why the fixed anchor, both roots and repository are unsafe.
    #>
    foreach ($root in @($PolicyAnchor, $PrivateRoot, $RuntimeRoot)) {
        $problem = Get-PathProblem -Path $root
        if ($problem) { return ('{0}: {1}' -f $root, $problem) }
    }
    if (-not (Test-WithinRoot -Path $PrivateRoot -Root $PolicyAnchor -Strict)) {
        return 'the private root is not inside the policy anchor'
    }
    if (-not (Test-WithinRoot -Path $RuntimeRoot -Root $PolicyAnchor -Strict)) {
        return 'the runtime root is not inside the policy anchor'
    }
    if ((Test-WithinRoot -Path $PrivateRoot -Root $RepositoryRoot) -or
            (Test-WithinRoot -Path $RepositoryRoot -Root $PrivateRoot)) {
        return 'the private root and the source repository overlap'
    }
    foreach ($other in @($PrivateRoot, $RepositoryRoot)) {
        if ((Test-WithinRoot -Path $RuntimeRoot -Root $other) -or
                (Test-WithinRoot -Path $other -Root $RuntimeRoot)) {
            return "the runtime root overlaps $other"
        }
    }
    return $null
}

function Get-RuntimeVolumeProblem {
    <#
    .SYNOPSIS
        Returns why the runtime root's drive is not a fixed NTFS volume.
    #>
    try {
        $drive = New-Object System.IO.DriveInfo ($RuntimeRoot.Substring(0, 1))
        if ($drive.DriveType -ne [System.IO.DriveType]::Fixed) {
            return ('{0} is not a fixed local volume' -f $drive.Name)
        }
        if ($drive.DriveFormat -ne 'NTFS') {
            return ('{0} is {1}, not NTFS' -f $drive.Name, $drive.DriveFormat)
        }
    } catch {
        return ('runtime volume information is unavailable ({0})' -f $_.Exception.GetType().Name)
    }
    return $null
}

function Get-RuntimeExpectation {
    <#
    .SYNOPSIS
        Returns the fixed runtime volume and ACL expectations.

    .DESCRIPTION
        Mirrored from ehr_evidence_app.storage.runtime, including the
        versioned identity-digest prefix and canonical field order.
    #>
    return @{
        DiskModel = 'CT1000T500SSD8'; Bus = 'NVMe'; Media = 'SSD'
        FileSystem = 'NTFS'; DriveType = 'Fixed'; Healthy = 'Healthy'
        MinFreeBytes = [int64]10 * 1024 * 1024 * 1024
        IdentityVersion = 'v1'; IdentityPrefix = 'ehr-runtime-volume-identity:v1'
        IdentityFields = @('disk_model', 'bus_type', 'size_bytes', 'partition_guid', 'volume_id')
        SystemSid = 'S-1-5-18'; AdministratorsSid = 'S-1-5-32-544'
        FullControl = [int64]0x1F01FF; InheritBoth = 'ContainerInherit, ObjectInherit'
    }
}

function Get-RuntimeIdentityDigest {
    <#
    .SYNOPSIS
        Returns the versioned SHA-256 volume identity digest, or $null.

    .DESCRIPTION
        SHA-256 over the prefix line and "name=value" lines of the canonical
        fields joined by a line feed; every field must be present. Raw
        identifiers are used only in memory and never printed.
    #>
    param([Parameter(Mandatory)][hashtable]$Facts)

    $expected = Get-RuntimeExpectation
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add($expected.IdentityPrefix)
    foreach ($name in $expected.IdentityFields) {
        $value = $Facts[$name]
        if ($null -eq $value -or $value -is [bool] -or [string]$value -eq '') { return $null }
        $lines.Add(('{0}={1}' -f $name, [string]$value))
    }
    $bytes = [System.Text.Encoding]::UTF8.GetBytes(($lines -join "`n"))
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { $hash = $sha.ComputeHash($bytes) } finally { $sha.Dispose() }
    return $expected.IdentityVersion + ':' + (-join ($hash | ForEach-Object { $_.ToString('x2') }))
}

function Get-RuntimeFactsProblem {
    <#
    .SYNOPSIS
        Returns why current volume facts fail the runtime gate, or $null.

    .DESCRIPTION
        Pure: expected disk model, bus and media, fixed NTFS volume, healthy
        volume and disk, minimum free space and an identity digest equal to
        the pinned digest (a missing pin fails).
    #>
    param(
        [Parameter(Mandatory)][hashtable]$Facts,
        [AllowNull()][AllowEmptyString()][string]$PinnedDigest
    )

    $expected = Get-RuntimeExpectation
    if ($Facts.disk_model -cne $expected.DiskModel) { return 'unexpected runtime disk model' }
    if ($Facts.bus_type -cne $expected.Bus) { return 'unexpected runtime disk bus' }
    if ($Facts.media_type -cne $expected.Media) { return 'unexpected runtime media type' }
    if ($Facts.drive_type -cne $expected.DriveType) { return 'the runtime volume is not fixed' }
    if ($Facts.file_system -cne $expected.FileSystem) { return 'the runtime volume is not NTFS' }
    if ($Facts.volume_health -cne $expected.Healthy -or $Facts.volume_operational -cne 'OK') {
        return 'the runtime volume is not healthy'
    }
    if ($Facts.disk_health -cne $expected.Healthy -or $Facts.disk_operational -cne 'Online') {
        return 'the runtime disk is not healthy'
    }
    if ($Facts.free_bytes -isnot [int64] -or $Facts.free_bytes -lt $expected.MinFreeBytes) {
        return 'the runtime volume has too little free space'
    }
    if (-not $PinnedDigest) {
        return 'no pinned runtime identity digest; run ehr_evidence_app.storage.runtime first'
    }
    if ((Get-RuntimeIdentityDigest -Facts $Facts) -cne $PinnedDigest) {
        return 'the runtime volume identity digest differs from the pinned digest'
    }
    return $null
}

function Get-RuntimeAclProblem {
    <#
    .SYNOPSIS
        Returns why a runtime directory ACL is not restrictive, or $null.

    .DESCRIPTION
        Pure: inheritance disabled, owner and every allow rule limited to
        SYSTEM, Administrators and the current user, no inherited or deny
        rule, and inherited full control for each of the three.
    #>
    param(
        [Parameter(Mandatory)][hashtable]$Acl,
        [Parameter(Mandatory)][string]$UserSid
    )

    $expected = Get-RuntimeExpectation
    $allowed = @($expected.SystemSid, $expected.AdministratorsSid, $UserSid)
    if ($Acl.protected -ne $true) { return 'inheritance is not disabled' }
    if ($allowed -notcontains $Acl.owner) { return 'the owner is not SYSTEM, Administrators or the current user' }
    $granted = @{}
    foreach ($rule in @($Acl.rules)) {
        if ($rule.inherited) { return 'an inherited rule is present' }
        if ($rule.type -ne 'Allow') { return 'a non-allow rule is present' }
        if ($allowed -notcontains $rule.sid) { return 'a rule grants access to another principal' }
        if ([int64]$rule.rights -eq $expected.FullControl -and
                $rule.inheritance -eq $expected.InheritBoth -and $rule.propagation -eq 'None') {
            $granted[$rule.sid] = $true
        }
    }
    foreach ($sid in $allowed) {
        if (-not $granted.ContainsKey($sid)) { return 'a required principal lacks inherited full control' }
    }
    return $null
}

function Get-RuntimeVolumeFacts {
    <#
    .SYNOPSIS
        Reads the runtime drive's current volume and disk facts (read-only).
    #>
    $letter = $RuntimeRoot.Substring(0, 1)
    $volume = Get-Volume -DriveLetter $letter -ErrorAction Stop
    $partition = Get-Partition -DriveLetter $letter -ErrorAction Stop
    $disk = Get-Disk -Number $partition.DiskNumber -ErrorAction Stop
    $physical = @(Get-PhysicalDisk -ErrorAction Stop |
            Where-Object { [string]$_.DeviceId -eq [string]$partition.DiskNumber })
    $media = 'unknown'
    if ($physical.Count -eq 1) { $media = [string]$physical[0].MediaType }
    return @{
        file_system = [string]$volume.FileSystem; drive_type = [string]$volume.DriveType
        volume_health = [string]$volume.HealthStatus
        volume_operational = [string]$volume.OperationalStatus
        size_bytes = [int64]$volume.Size; free_bytes = [int64]$volume.SizeRemaining
        volume_id = [string]$volume.UniqueId; partition_guid = [string]$partition.Guid
        disk_model = [string]$disk.FriendlyName; bus_type = [string]$disk.BusType
        disk_health = [string]$disk.HealthStatus
        disk_operational = [string]$disk.OperationalStatus; media_type = $media
    }
}

function Get-RuntimeAclFacts {
    <#
    .SYNOPSIS
        Reads one directory's owner and access rules as SIDs (read-only).
    #>
    param([Parameter(Mandatory)][string]$Path)

    $acl = New-Object System.Security.AccessControl.DirectorySecurity($Path, 'Access, Owner')
    $sid = [System.Security.Principal.SecurityIdentifier]
    $rules = @(foreach ($rule in $acl.GetAccessRules($true, $true, $sid)) {
            @{
                sid = $rule.IdentityReference.Value; rights = [int64]$rule.FileSystemRights
                type = [string]$rule.AccessControlType; inherited = [bool]$rule.IsInherited
                inheritance = [string]$rule.InheritanceFlags
                propagation = [string]$rule.PropagationFlags
            }
        })
    return @{ protected = [bool]$acl.AreAccessRulesProtected; owner = $acl.GetOwner($sid).Value; rules = $rules }
}

function Get-PinnedRuntimeDigest {
    <#
    .SYNOPSIS
        Returns the pinned identity digest from the preflight record, or $null.

    .DESCRIPTION
        A record written before the digest existed is converted in memory,
        exactly as check_runtime_storage.py does.
    #>
    $attributes = Get-NoFollowAttributes -Path $RuntimeRecordPath
    if ($null -eq $attributes -or ($attributes -band 0x410) -ne 0) { return $null }
    try {
        $record = Get-Content -LiteralPath $RuntimeRecordPath -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch { return $null }
    $names = @($record.PSObject.Properties.Name)
    if ($names -contains 'identity_digest' -and $record.identity_digest -is [string] -and
            $record.identity_digest.StartsWith('v1:')) {
        return $record.identity_digest
    }
    if ($names -contains 'volume' -and $null -ne $record.volume) {
        $facts = @{}
        foreach ($property in $record.volume.PSObject.Properties) { $facts[$property.Name] = $property.Value }
        return Get-RuntimeIdentityDigest -Facts $facts
    }
    return $null
}

function Get-RuntimeGateProblem {
    <#
    .SYNOPSIS
        Re-runs the read-only runtime gate before anything is created or set.

    .DESCRIPTION
        Current volume identity (against the pinned digest), health, fixed
        NTFS media, free space and the restrictive ACLs of both runtime
        levels. Reparse checks of the root path run separately through
        Assert-Destination. Nothing is written and no raw identifier is
        returned.
    #>
    try {
        $facts = Get-RuntimeVolumeFacts
    } catch {
        return ('runtime volume facts are unavailable ({0})' -f $_.Exception.GetType().Name)
    }
    $problem = Get-RuntimeFactsProblem -Facts $facts -PinnedDigest (Get-PinnedRuntimeDigest)
    if ($problem) { return $problem }
    $user = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    foreach ($path in @($RuntimeParent, $RuntimeRoot)) {
        $attributes = Get-NoFollowAttributes -Path $path
        if ($null -eq $attributes -or ($attributes -band 0x400) -ne 0 -or ($attributes -band 0x10) -eq 0) {
            return ('{0} is missing or not a plain directory' -f $path)
        }
        try {
            $acl = Get-RuntimeAclFacts -Path $path
        } catch {
            return ('{0} ACL is unreadable ({1})' -f $path, $_.Exception.GetType().Name)
        }
        $problem = Get-RuntimeAclProblem -Acl $acl -UserSid $user
        if ($problem) { return ('{0}: {1}' -f $path, $problem) }
    }
    return $null
}

function Get-NoFollowAttributes {
    <#
    .SYNOPSIS
        Returns a path's attributes without following links, or $null if absent.
    #>
    param([Parameter(Mandatory)][string]$Path)

    try {
        return [int][System.IO.File]::GetAttributes($Path)
    } catch {
        $inner = $_.Exception
        while ($null -ne $inner.InnerException) { $inner = $inner.InnerException }
        if ($inner -is [System.IO.FileNotFoundException] -or
                $inner -is [System.IO.DirectoryNotFoundException]) {
            return $null
        }
        throw ("Cannot inspect '{0}' safely: {1}" -f $Path, $inner.GetType().Name)
    }
}

function Get-AttributeProblem {
    <#
    .SYNOPSIS
        Returns why one existing component's attributes are unsafe, or $null.

    .DESCRIPTION
        Pure interpretation of FileAttributes read without following links: a
        reparse point (symlink, junction, dangling link or other) is always
        refused, an ancestor must be a directory and a leaf must be the
        expected kind.
    #>
    param(
        [Parameter(Mandatory)][int]$Attributes,
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][bool]$IsLeaf,
        [Parameter(Mandatory)][ValidateSet('Directory', 'File')][string]$Kind
    )

    if (($Attributes -band 0x400) -ne 0) {
        return ("'{0}' is a symlink, junction or other reparse point" -f $Path)
    }
    $isDirectory = ($Attributes -band 0x10) -ne 0
    if (-not $IsLeaf) {
        if (-not $isDirectory) { return ("existing ancestor '{0}' is not a directory" -f $Path) }
        return $null
    }
    if ($Kind -eq 'Directory' -and -not $isDirectory) { return 'existing destination is not a directory' }
    if ($Kind -eq 'File' -and $isDirectory) { return 'existing destination is not a regular file' }
    return $null
}

function Get-FileSystemProblem {
    <#
    .SYNOPSIS
        Inspects the existing components of a path without following links.

    .DESCRIPTION
        Every existing ancestor must be a plain directory without a .git entry
        and no existing component may be a reparse point (symlink, junction or
        other). The first missing component ends the walk. An existing leaf
        must be of the expected kind.
    #>
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][ValidateSet('Directory', 'File')][string]$Kind
    )

    $current = $Path.Substring(0, 3)
    $parts = $Path.Substring(3).Split('\')
    for ($index = 0; $index -lt $parts.Count; $index++) {
        $gitEntry = [System.IO.Path]::Combine($current, '.git')
        if ($null -ne (Get-NoFollowAttributes -Path $gitEntry)) {
            return ("existing ancestor '{0}' contains .git" -f $current)
        }
        $current = [System.IO.Path]::Combine($current, $parts[$index])
        $attributes = Get-NoFollowAttributes -Path $current
        if ($null -eq $attributes) { return $null }
        $isLeaf = $index -eq ($parts.Count - 1)
        $problem = Get-AttributeProblem -Attributes $attributes -Path $current -IsLeaf $isLeaf -Kind $Kind
        if ($problem) { return $problem }
    }
    if ($Kind -eq 'Directory') {
        $gitEntry = [System.IO.Path]::Combine($Path, '.git')
        if ($null -ne (Get-NoFollowAttributes -Path $gitEntry)) {
            return 'existing destination contains .git'
        }
    }
    return $null
}

function Assert-Destination {
    <#
    .SYNOPSIS
        Fails fast unless a path is an approved destination in its fixed root.

    .DESCRIPTION
        Root defaults to the private workspace root; only the fixed runtime root may
        be named instead, so runtime destinations are requested explicitly.
    #>
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][ValidateSet('Directory', 'File')][string]$Kind,
        [switch]$AllowRoot,
        [string]$Root = $PrivateRoot
    )

    if ($Root -cne $PrivateRoot -and $Root -cne $RuntimeRoot) {
        throw 'Assert-Destination: root must be the fixed private or runtime root.'
    }
    $problem = Get-PathProblem -Path $Path
    if (-not $problem -and
            -not (Test-WithinRoot -Path $Path -Root $Root -Strict:(-not $AllowRoot))) {
        $problem = "it is outside the root '$Root'"
    }
    if (-not $problem -and (Test-WithinRoot -Path $Path -Root $RepositoryRoot)) {
        $problem = 'it is inside the source repository'
    }
    if (-not $problem -and (@($Path.Split('\')) -contains '.git')) {
        $problem = 'it names a .git directory'
    }
    if (-not $problem) { $problem = Get-FileSystemProblem -Path $Path -Kind $Kind }
    if ($problem) { throw ("Refusing to configure '{0}': {1}." -f $Path, $problem) }
}

function Resolve-DestinationDirectory {
    <#
    .SYNOPSIS
        Joins the fixed private root with a validated relative path.
    #>
    param(
        [Parameter(Mandatory)][string]$Root,
        [Parameter(Mandatory)][string]$Relative
    )

    if ($Relative -eq '.') { return $Root }
    return $Root + '\' + $Relative
}

function Get-DirectoryPath {
    <#
    .SYNOPSIS
        Looks up a resolved directory path and fails on an unknown key.
    #>
    param(
        [Parameter(Mandatory)][hashtable]$Directories,
        [Parameter(Mandatory)][string]$Key
    )

    if (-not $Directories.ContainsKey($Key)) {
        throw "Unknown directory key '$Key' in the destination configuration."
    }
    return $Directories[$Key]
}

function Get-EnvironmentEntryProblem {
    <#
    .SYNOPSIS
        Returns why an environment entry could bypass path checks, or $null.

    .DESCRIPTION
        Mandatory destination variables must be the directory or file entry of
        their fixed key. PYTEST_ADDOPTS and CLEARML_OFFLINE_MODE must equal
        their canonical template or literal exactly. Any other variable may
        only be a directory or file entry; referenced keys must exist and a
        file entry must name one safe file.
    #>
    param(
        [Parameter(Mandatory)][psobject]$Entry,
        [Parameter(Mandatory)][hashtable]$Directories
    )

    $fields = @($Entry.PSObject.Properties.Name)
    $name = ''
    if ($fields -contains 'name' -and $Entry.name -is [string]) { $name = $Entry.name }
    if ($name -cnotmatch '^[A-Z_][A-Z0-9_]*$') {
        return 'environment variable name is not a plain upper-case name'
    }
    $kinds = @(@('directory', 'file', 'literal', 'template') | Where-Object { $fields -contains $_ })
    if ($kinds.Count -ne 1) {
        return ('{0} must define exactly one of directory, file, literal, template' -f $name)
    }
    $kind = $kinds[0]
    $fileFields = @()
    if ($kind -eq 'file' -and $Entry.file -is [psobject] -and $Entry.file -isnot [string]) {
        $fileFields = @($Entry.file.PSObject.Properties.Name)
    }
    $fileKey = $null
    if ($fileFields -contains 'directory') { $fileKey = $Entry.file.directory }

    if ($DirectoryVariables.ContainsKey($name)) {
        $key = $DirectoryVariables[$name]
        if ($kind -ne 'directory' -or -not ($Entry.directory -is [string] -and $Entry.directory -ceq $key)) {
            return ('{0} must be the directory entry for key {1}' -f $name, $key)
        }
    } elseif ($FileVariables.ContainsKey($name)) {
        $key = $FileVariables[$name]
        if ($kind -ne 'file' -or -not ($fileKey -is [string] -and $fileKey -ceq $key)) {
            return ('{0} must be a file entry in directory key {1}' -f $name, $key)
        }
    } elseif ($TemplateVariables.ContainsKey($name)) {
        $canonical = $TemplateVariables[$name]
        if ($kind -ne 'template' -or -not ($Entry.template -is [string] -and $Entry.template -ceq $canonical)) {
            return ('{0} must be exactly the canonical template {1}' -f $name, $canonical)
        }
    } elseif ($LiteralVariables.ContainsKey($name)) {
        $canonical = $LiteralVariables[$name]
        if ($kind -ne 'literal' -or -not ($Entry.literal -is [string] -and $Entry.literal -ceq $canonical)) {
            return ('{0} must be exactly the literal {1}' -f $name, $canonical)
        }
    } elseif ($kind -ne 'directory' -and $kind -ne 'file') {
        return ('{0}: additional variables must be validated directory or file entries; literal and template values are fixed in code' -f $name)
    }

    if ($kind -eq 'directory') {
        if ($Entry.directory -isnot [string] -or -not $Directories.ContainsKey($Entry.directory)) {
            return ('{0} names an unknown directory key' -f $name)
        }
    } elseif ($kind -eq 'file') {
        if ($fileKey -isnot [string] -or -not $Directories.ContainsKey($fileKey)) {
            return ('{0} names an unknown directory key' -f $name)
        }
        $fileName = $null
        if ($fileFields -contains 'name') { $fileName = $Entry.file.name }
        $problem = Get-NameProblem -Name $fileName
        if ($problem) { return ('{0} file name: {1}' -f $name, $problem) }
    } elseif ($kind -eq 'template') {
        foreach ($match in [regex]::Matches($Entry.template, $PlaceholderPattern)) {
            if (-not $Directories.ContainsKey($match.Groups[2].Value)) {
                return ('{0} template names an unknown directory key' -f $name)
            }
        }
    }
    return $null
}

function Get-EnvironmentSetProblem {
    <#
    .SYNOPSIS
        Returns why the mandatory directory and variable set is incomplete.

    .DESCRIPTION
        Independent of which entries are present: every essential directory
        key and every mandatory variable must be defined, each variable
        exactly once, and list-valued variables must not point at a path that
        contains a list delimiter. Returns $null when the set is complete.
    #>
    param(
        [Parameter(Mandatory)][psobject]$Config
    )

    $keys = @($Config.directories.PSObject.Properties.Name)
    foreach ($key in $RequiredDirectoryKeys) {
        if ($keys -cnotcontains $key) { return ('required directory key {0} is missing' -f $key) }
    }
    $names = @(foreach ($entry in @($Config.environment)) {
            if (@($entry.PSObject.Properties.Name) -contains 'name') { [string]$entry.name }
        })
    foreach ($name in $RequiredVariables) {
        $count = @($names | Where-Object { $_ -ceq $name }).Count
        if ($count -ne 1) {
            return ('required environment variable {0} is defined {1} times; exactly once is required' -f $name, $count)
        }
    }
    foreach ($name in $ListVariables) {
        $path = $Config.directories.($DirectoryVariables[$name]).path
        if ($path -is [string] -and $path.IndexOfAny($ListDelimiters) -ge 0) {
            return ('{0} path contains a list delimiter (; or ,)' -f $name)
        }
    }
    foreach ($property in $Config.directories.PSObject.Properties) {
        $problem = Get-DirectoryRootProblem -Key $property.Name -Entry $property.Value
        if ($problem) { return $problem }
    }
    return $null
}

function Expand-DestinationTemplate {
    <#
    .SYNOPSIS
        Expands ${dir:key} and ${dir_posix:key} placeholders in a value template.
    #>
    param(
        [Parameter(Mandatory)][string]$Template,
        [Parameter(Mandatory)][hashtable]$Directories
    )

    $pattern = '\$\{(dir|dir_posix):([A-Za-z0-9_]+)\}'
    $result = $Template
    foreach ($match in [regex]::Matches($Template, $pattern)) {
        $value = Get-DirectoryPath -Directories $Directories -Key $match.Groups[2].Value
        if ($match.Groups[1].Value -eq 'dir_posix') {
            $value = $value.Replace('\', '/')
        }
        $result = $result.Replace($match.Value, $value)
    }
    return $result
}

function Get-EnvironmentValue {
    <#
    .SYNOPSIS
        Resolves one configured environment entry to its literal value.
    #>
    param(
        [Parameter(Mandatory)][psobject]$Entry,
        [Parameter(Mandatory)][hashtable]$Directories
    )

    $fields = @($Entry.PSObject.Properties.Name)
    if ($fields -contains 'directory') {
        return Get-DirectoryPath -Directories $Directories -Key $Entry.directory
    }
    if ($fields -contains 'file') {
        $base = Get-DirectoryPath -Directories $Directories -Key $Entry.file.directory
        # Normalise before the caller's containment assertions so a file name
        # containing '..' cannot escape the configured directory.
        return [System.IO.Path]::GetFullPath(
            [System.IO.Path]::Combine($base, $Entry.file.name))
    }
    if ($fields -contains 'literal') { return [string]$Entry.literal }
    if ($fields -contains 'template') {
        return Expand-DestinationTemplate -Template $Entry.template -Directories $Directories
    }
    throw "Environment entry '$($Entry.name)' defines no value."
}

function Set-ProcessDestinationVariable {
    <#
    .SYNOPSIS
        Sets an environment variable for this process only.
    #>
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Value
    )

    [System.Environment]::SetEnvironmentVariable(
        $Name, $Value, [System.EnvironmentVariableTarget]::Process)
}

$problem = Get-FixedPolicyProblem
if ($problem) { throw "Fixed destination policy is unsafe: $problem." }

$config = Get-DestinationConfig -Path $ConfigPath
$comparison = [System.StringComparison]::OrdinalIgnoreCase
if ($config.workspace_root -isnot [string] -or
        -not $config.workspace_root.Equals($PrivateRoot, $comparison)) {
    throw "Refusing configuration: workspace_root must be the fixed private root '$PrivateRoot'; configuration cannot relocate it."
}
if (@($config.PSObject.Properties.Name) -notcontains 'runtime_root' -or
        $config.runtime_root -isnot [string] -or
        -not $config.runtime_root.Equals($RuntimeRoot, $comparison)) {
    throw "Refusing configuration: runtime_root must be the fixed runtime root '$RuntimeRoot'; configuration cannot relocate it."
}
$requiredPrefix = [string]$config.policy.required_root_prefix
if (-not $requiredPrefix.TrimEnd('\').Equals($PolicyAnchor, $comparison)) {
    throw "Refusing configuration: required_root_prefix must be the fixed anchor '$PolicyAnchor\'; configuration cannot relocate it."
}
$problem = Get-EnvironmentSetProblem -Config $config
if ($problem) { throw "Refusing configuration: $problem." }
$workspaceRoot = $PrivateRoot

if (-not (Test-Path -LiteralPath $workspaceRoot -PathType Container)) {
    throw "Workspace root does not exist: $workspaceRoot (create the project layout first)."
}
$problem = Get-RuntimeVolumeProblem
if ($problem) { throw "Refusing runtime root: $problem." }
Assert-Destination -Path $RuntimeRoot -Kind Directory -AllowRoot -Root $RuntimeRoot
if (-not (Test-Path -LiteralPath $RuntimeRoot -PathType Container)) {
    throw "Runtime root does not exist: $RuntimeRoot (ehr_evidence_app.storage.runtime --create-root creates it with restrictive ACLs)."
}
# Current read-only runtime gate: identity, health and ACL drift since the
# preflight stops setup before any directory is created or variable set.
$problem = Get-RuntimeGateProblem
if ($problem) { throw "Refusing runtime root: $problem." }

# Preflight: resolve and check every configured path before anything is
# created or set. A single rejected path aborts the whole run.
$directories = @{}
$directoryRoots = @{}
$pending = New-Object System.Collections.Generic.List[string]

foreach ($property in $config.directories.PSObject.Properties) {
    $entry = $property.Value
    $root = Get-DirectoryRoot -Entry $entry
    $problem = Get-RelativeProblem -Relative $entry.path -Root $root
    if ($problem) { throw ("Refusing directory '{0}': {1}." -f $property.Name, $problem) }
    $path = Resolve-DestinationDirectory -Root $root -Relative $entry.path
    Assert-Destination -Path $path -Kind Directory -AllowRoot -Root $root
    $directories[$property.Name] = $path
    $directoryRoots[$property.Name] = $root

    if ($entry.create -and -not (Test-Path -LiteralPath $path -PathType Container)) {
        $pending.Add($path)
    }
}

$markerName = $config.policy.marker_file_name
$problem = Get-NameProblem -Name $markerName
if ($problem) { throw "Refusing marker file name: $problem." }
foreach ($property in $config.directories.PSObject.Properties) {
    if ($property.Value.create) {
        Assert-Destination -Path ($directories[$property.Name] + '\' + $markerName) -Kind File `
            -Root $directoryRoots[$property.Name]
    }
}

$seen = @{}
$resolved = @(foreach ($entry in $config.environment) {
        $problem = Get-EnvironmentEntryProblem -Entry $entry -Directories $directories
        if ($problem) { throw "Refusing environment entry: $problem." }
        if ($seen.ContainsKey($entry.name)) {
            throw ("Refusing environment entry: {0} is defined more than once." -f $entry.name)
        }
        $seen[$entry.name] = $true
        $value = Get-EnvironmentValue -Entry $entry -Directories $directories
        if (@($entry.PSObject.Properties.Name) -contains 'file') {
            Assert-Destination -Path $value -Kind File -Root $directoryRoots[$entry.file.directory]
        }
        [pscustomobject]@{
            Name     = $entry.name
            Evidence = $entry.evidence
            Value    = $value
        }
    })

# Apply only after the whole configuration has passed preflight.
$created = New-Object System.Collections.Generic.List[string]
foreach ($path in $pending) {
    New-Item -ItemType Directory -Path $path -Force | Out-Null
    $created.Add($path)
}

$applied = @(foreach ($item in $resolved) {
        Set-ProcessDestinationVariable -Name $item.Name -Value $item.Value
        $item
    })

if (-not $Quiet) {
    foreach ($item in $applied) {
        Write-Host ("{0,-24} {1,-12} {2}" -f $item.Name, $item.Evidence, $item.Value)
    }
    Write-Host ""
    Write-Host "Workspace root : $workspaceRoot"
    Write-Host "Runtime root   : $RuntimeRoot"
    Write-Host ("Directories    : {0} configured, {1} created this run" -f `
            $directories.Count, $created.Count)
    Write-Host ("Variables      : {0} set for this process only" -f $applied.Count)
    Write-Host "These destinations are configured, not confirmed. Actual routing is"
    Write-Host "confirmed only for the Python operations the verifier exercises."
    Write-Host "Verify with: python -m ehr_evidence_app.storage.destinations"
}
