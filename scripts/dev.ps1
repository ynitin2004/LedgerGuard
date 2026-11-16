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
                 'verify-decisions', 'image', 'bench-up', 'workers-up', 'reconcile', 'replay-dlq', 'load',
                 'test', 'test-linux', 'test-unit', 'lint', 'fmt',
                 'typecheck', 'check')]
    [string]$Target = 'help',
    # Used by api-client and seed: ./scripts/dev.ps1 seed -Name demo
    [string]$Name = 'demo',
    # Used by load: ./scripts/dev.ps1 load -ApiKey <key printed by seed>
    [string]$ApiKey = '',
    # Used by replay-dlq: ./scripts/dev.ps1 replay-dlq -EventId <id> (or all)
    [string]$EventId = ''
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
        Write-Host 'Targets: env install up down down-volumes ps logs db-role migrate api worker-janitor api-client train register-model seed verify-decisions image bench-up workers-up reconcile replay-dlq load test test-linux test-unit lint fmt typecheck check'
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
    'image' { Invoke-Step docker @('build', '-f', 'infra/api/Dockerfile', '-t', 'ledgerguard-app:local', '.') }
    'bench-up' {
        Initialize-Env
        Invoke-Step docker @('build', '-f', 'infra/api/Dockerfile', '-t', 'ledgerguard-app:local', '.')
        Invoke-Step docker @('compose', '--profile', 'bench', 'up', '-d', '--no-build', '--wait', 'api', 'outbox-publisher', 'feature-updater')
    }
    'workers-up' {
        Initialize-Env
        Invoke-Step docker @('build', '-f', 'infra/api/Dockerfile', '-t', 'ledgerguard-app:local', '.')
        Invoke-Step docker @('compose', '--profile', 'workers', 'up', '-d', '--no-build', 'outbox-publisher', 'feature-updater')
    }
    'reconcile' {
        Invoke-Step docker @('compose', '--profile', 'workers', 'run', '--rm', '--no-deps', 'feature-updater',
            '/opt/venv/bin/python', '-m', 'apps.worker', 'reconcile', '--strict')
    }
    'replay-dlq' {
        if (-not $EventId) { throw 'Pass -EventId <id> or -EventId all' }
        $target = if ($EventId -eq 'all') { @('--all') } else { @('--event-id', $EventId) }
        Invoke-Step docker (@('compose', '--profile', 'workers', 'run', '--rm', '--no-deps', 'feature-updater',
            '/opt/venv/bin/python', '-m', 'apps.worker', 'replay-dlq') + $target)
    }
    'load' {
        if (-not $ApiKey) { throw 'Pass -ApiKey <key printed by ./scripts/dev.ps1 seed>' }
        $env:LOAD_API_KEY = $ApiKey
        Invoke-Step docker @('compose', '--profile', 'bench', 'run', '--rm', '--no-deps', 'k6', 'run',
            '--summary-export', '/load/results/latest.json', '/load/transactions.js')
    }
    'test' { Invoke-Step uv @('run', 'python', '-m', 'pytest') }
    'test-linux' {
        # Kafka and numpy cannot load on this PC: run the whole suite in Linux (ADR-033).
        Invoke-Step docker @('build', '-q', '-t', 'ledgerguard-ml:local', 'infra/ml')
        Invoke-Step docker @('run', '--rm', '-v', "${PWD}:/repo", '-v', 'ledgerguard-uv-cache:/root/.cache/uv',
            '-v', '/var/run/docker.sock:/var/run/docker.sock', '-e', 'TESTCONTAINERS_HOST_OVERRIDE=host.docker.internal',
            '-e', 'TESTCONTAINERS_RYUK_DISABLED=true', 'ledgerguard-ml:local', 'uv', 'run', '--frozen', 'python', '-m', 'pytest', '-p', 'no:cacheprovider')
    }
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
