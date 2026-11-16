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
                 'migrate', 'api', 'worker-janitor', 'api-client', 'train', 'register-model', 'seed',
                 'verify-decisions', 'bench-up', 'load', 'test', 'test-unit', 'lint', 'fmt',
                 'typecheck', 'check')]
    [string]$Target = 'help',
    # Used by api-client and seed: ./scripts/dev.ps1 seed -Name demo
    [string]$Name = 'demo',
    # Used by load: ./scripts/dev.ps1 load -ApiKey <key printed by seed>
    [string]$ApiKey = ''
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
        Write-Host 'Targets: env install up down down-volumes ps logs db-role migrate api worker-janitor api-client train register-model seed verify-decisions bench-up load test test-unit lint fmt typecheck check'
    }
    'env' { Initialize-Env }
    'install' { Invoke-Step uv @('sync', '--frozen') }
    'up' { Initialize-Env; Invoke-Step docker @('compose', 'up', '-d', '--wait', '--wait-timeout', '180') }
    'down' { Invoke-Step docker @('compose', '--profile', 'bench', 'down') }
    'down-volumes' { Invoke-Step docker @('compose', '--profile', 'bench', 'down', '-v') }
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
    'train' {
        # numpy cannot load on this PC (Application Control), so train in a Linux container (ADR-021).
        Invoke-Step docker @('build', '-q', '-t', 'ledgerguard-ml:local', 'infra/ml')
        Invoke-Step docker @('run', '--rm', '-v', "${PWD}:/repo", '-v', 'ledgerguard-uv-cache:/root/.cache/uv',
            'ledgerguard-ml:local', 'uv', 'run', '--frozen', '--no-dev', '--group', 'ml', 'python', 'scripts/train_model.py')
    }
    'register-model' { Initialize-Env; Invoke-Step uv @('run', 'python', 'scripts/register_model.py', 'models/lr-v1.json', '--activate') }
    'seed' { Initialize-Env; Invoke-Step uv @('run', 'python', 'scripts/seed_history.py', '--name', $Name) }
    'verify-decisions' { Initialize-Env; Invoke-Step uv @('run', 'python', 'scripts/verify_decisions.py') }
    'bench-up' {
        Initialize-Env
        Invoke-Step docker @('build', '-f', 'infra/api/Dockerfile', '-t', 'ledgerguard-api:local', '.')
        Invoke-Step docker @('compose', '--profile', 'bench', 'up', '-d', '--no-build', '--wait', 'api')
    }
    'load' {
        if (-not $ApiKey) { throw 'Pass -ApiKey <key printed by ./scripts/dev.ps1 seed>' }
        $env:LOAD_API_KEY = $ApiKey
        Invoke-Step docker @('compose', '--profile', 'bench', 'run', '--rm', '--no-deps', 'k6', 'run',
            '--summary-export', '/load/results/latest.json', '/load/transactions.js')
    }
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
