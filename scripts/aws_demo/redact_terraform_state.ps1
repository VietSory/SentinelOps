param(
    [Parameter(Mandatory = $true)]
    [string] $InputPath,

    [Parameter(Mandatory = $true)]
    [string] $OutputPath
)

$ErrorActionPreference = "Stop"
$sensitiveName = '(?i)(password|secret|token|client_secret|access_key|private_key|credential|master_user|connection_string|authorization|api_key|cookie)'

function Redact-Node {
    param(
        [AllowNull()] $Node,
        [string] $PropertyName = ''
    )

    if ($null -eq $Node) { return $null }

    if ($PropertyName -match $sensitiveName) {
        return '[REDACTED]'
    }

    if ($Node -is [string]) {
        if ($Node -match '(?i)AKIA[0-9A-Z]{16}' -or
            $Node -match '(?i)ASIA[0-9A-Z]{16}' -or
            $Node -match '-----BEGIN [A-Z ]+-----' -or
            $Node -match '^arn:aws:secretsmanager:' -or
            $Node -match '^[^\s@]+://[^\s@]+:[^\s@]+@') {
            return '[REDACTED]'
        }
        return $Node
    }

    if ($Node -is [System.Management.Automation.PSCustomObject]) {
        $copy = [ordered]@{}
        $isSensitiveObject = $false
        $sensitiveFlag = $Node.PSObject.Properties['sensitive']
        if ($null -ne $sensitiveFlag -and $sensitiveFlag.Value -eq $true) {
            $isSensitiveObject = $true
        }

        foreach ($property in $Node.PSObject.Properties) {
            if ($isSensitiveObject -and $property.Name -eq 'value') {
                $copy[$property.Name] = '[REDACTED]'
            } else {
                $copy[$property.Name] = Redact-Node -Node $property.Value -PropertyName $property.Name
            }
        }
        return [PSCustomObject]$copy
    }

    if ($Node -is [System.Collections.IEnumerable] -and $Node -isnot [string]) {
        $items = foreach ($item in $Node) {
            Redact-Node -Node $item -PropertyName $PropertyName
        }
        return @($items)
    }

    return $Node
}

$state = Get-Content -LiteralPath $InputPath -Raw | ConvertFrom-Json -Depth 100
$redacted = Redact-Node -Node $state
$parent = Split-Path -Parent $OutputPath
New-Item -ItemType Directory -Force -Path $parent | Out-Null
$redacted | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $OutputPath -Encoding UTF8

Write-Output "Wrote redacted Terraform state snapshot: $OutputPath"
Write-Output "Serial: $($state.serial)"
