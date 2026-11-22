#requires -Version 7.0
param(
    [Parameter(Mandatory)]
    [string] $InputPath,

    [Parameter(Mandatory)]
    [string] $OutputPath
)

$ErrorActionPreference = 'Stop'
$source = (Get-Item -LiteralPath $InputPath).FullName
$destination = [IO.Path]::GetFullPath($OutputPath, (Get-Location).ProviderPath)
if (Test-Path -LiteralPath $destination) {
    throw 'Choose a new path for the public inventory.'
}

$state = Get-Content -LiteralPath $source -Raw | ConvertFrom-Json -AsHashtable -Depth 100
if ($state.serial -isnot [long] -and $state.serial -isnot [int]) {
    throw 'Terraform state must contain an integer serial.'
}
if ($state.serial -lt 0 -or $state.resources -isnot [array]) {
    throw 'Invalid Terraform state serial or resource list.'
}

foreach ($resource in $state.resources) {
    if ($resource.mode -cnotin @('managed', 'data') -or
        $resource.type -isnot [string] -or $resource.type -cnotmatch '^[a-z0-9_]{1,100}$') {
        throw 'Unexpected resource mode or type.'
    }
}

# Build the public artifact from scratch. Provider fields and output values
# can contain credentials even when their keys do not look sensitive.
$groups = @(
    $state.resources |
        Group-Object -Property mode, type |
        Sort-Object -Property Name |
        ForEach-Object {
            [ordered]@{
                mode = $_.Group[0].mode
                type = $_.Group[0].type
                entries = $_.Count
            }
        }
)

$inventory = [ordered]@{
    format = 'terraform-resource-inventory/v1'
    serial = $state.serial
    resource_entries = $state.resources.Count
    resource_types = $groups
}

$inventory | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $destination -Encoding utf8
Write-Output "Wrote Terraform resource inventory: $destination"
