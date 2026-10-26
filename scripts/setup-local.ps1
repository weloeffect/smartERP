$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
uv sync --locked
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
& .\.venv\Scripts\python.exe -m app.cli configure
if ($LASTEXITCODE -ne 0) { throw "Configuration failed." }
& .\.venv\Scripts\python.exe -m app.cli init
if ($LASTEXITCODE -ne 0) { throw "Database initialization failed." }
Write-Host "Ready. Add DASHSCOPE_API_KEY and check QWEN_BASE_URL / QWEN_MODEL in .env, then run .\scripts\start-local.ps1."

