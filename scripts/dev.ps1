<#
.SYNOPSIS
  LedgerGuard AI task runner for Windows (mirrors the Makefile targets).
.EXAMPLE
  ./scripts/dev.ps1 up
  ./scripts/dev.ps1 test
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet('help', 'env', 'install', 'up', 'down', 'down-volumes', 'ps', 'logs', 'db-role',
                 'migrate', 'api', 'worker-janitor', 'api-client', 'test', 'test-unit', 'lint', 'fmt',
                 'typecheck', 'check')]
    [string]$Target = 'help',
    # Used by api-client: ./scripts/dev.ps1 api-client -Name demo
    [string]$Name = 'demo'
)

$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

function Invoke-Step {
    # Run a native command and stop on a non-zero exit code. The exit code is the only
    # failure signal: Windows PowerShell 5.1 would otherwise turn ordinary stderr output
    # (docker progress, alembic INFO logs) into terminating errors when output is redirected.
    param([string]$Exe, [string[]]$Arguments)
    $ErrorActionPreference = 'Continue'
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "'$Exe $($Arguments -join ' ')' failed with exit code $LASTEXITCODE" }
}

function Initialize-Env {
    if (-not (Test-Path .env)) { Copy-Item .env.example .env; Write-Host 'Created .env from .env.example' }
}

switch ($Target) {
    'help' {
        Write-Host 'Targets: env install up down down-volumes ps logs db-role migrate api worker-janitor api-client test test-unit lint fmt typecheck check'
    }
    'env' { Initialize-Env }
    'install' { Invoke-Step uv @('sync', '--frozen') }
    'up' { Initialize-Env; Invoke-Step docker @('compose', 'up', '-d', '--wait', '--wait-timeout', '180') }
    'down' { Invoke-Step docker @('compose', 'down') }
    'down-volumes' { Invoke-Step docker @('compose', 'down', '-v') }
    'ps' { Invoke-Step docker @('compose', 'ps') }
    'logs' { Invoke-Step docker @('compose', 'logs', '-f', '--tail', '100') }
    'db-role' { Initialize-Env; Invoke-Step docker @('compose', 'exec', '-T', 'postgres', 'sh', '/docker-entrypoint-initdb.d/10-app-role.sh') }
    'migrate' { Initialize-Env; Invoke-Step uv @('run', 'python', '-m', 'alembic', '-c', 'migrations/alembic.ini', 'upgrade', 'head') }
    'api' {
        Initialize-Env
        Invoke-Step uv @('run', 'python', '-m', 'uvicorn', '--factory', 'apps.api.main:create_app', '--reload', '--host', '127.0.0.1', '--port', '8000')
    }
    'worker-janitor' { Initialize-Env; Invoke-Step uv @('run', 'python', '-m', 'apps.worker', 'janitor') }
    'api-client' { Initialize-Env; Invoke-Step uv @('run', 'python', 'scripts/create_api_client.py', '--name', $Name) }
    'test' { Invoke-Step uv @('run', 'python', '-m', 'pytest') }
    'test-unit' { Invoke-Step uv @('run', 'python', '-m', 'pytest', '-m', 'not integration') }
    'lint' {
        Invoke-Step uv @('run', 'python', '-m', 'ruff', 'check', '.')
        Invoke-Step uv @('run', 'python', '-m', 'ruff', 'format', '--check', '.')
    }
    'fmt' {
        Invoke-Step uv @('run', 'python', '-m', 'ruff', 'check', '--fix', '.')
        Invoke-Step uv @('run', 'python', '-m', 'ruff', 'format', '.')
    }
    'typecheck' { Invoke-Step uv @('run', 'python', '-m', 'mypy') }
    'check' {
        Invoke-Step uv @('run', 'python', '-m', 'ruff', 'check', '.')
        Invoke-Step uv @('run', 'python', '-m', 'ruff', 'format', '--check', '.')
        Invoke-Step uv @('run', 'python', '-m', 'mypy')
        Invoke-Step uv @('run', 'python', '-m', 'pytest')
    }
}
