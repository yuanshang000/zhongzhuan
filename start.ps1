# CloudRive 私有云盘 - 本地启动（PowerShell 版）
# 双击本文件即可运行；也可右键"使用 PowerShell 运行"
# 若被策略禁止，可用 start.bat（CMD 版）

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Write-Step($msg) { Write-Host $msg -ForegroundColor Cyan }
function Write-Err($msg)  { Write-Host $msg -ForegroundColor Red }
function Write-Ok($msg)   { Write-Host $msg -ForegroundColor Green }

Write-Host ""
Write-Host "============================================================" -ForegroundColor DarkCyan
Write-Host "   CloudRive 私有云盘 - 本地启动" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor DarkCyan
Write-Host ""

# ---- 查找 Python ----
$pyCmd = $null
if (Get-Command python -ErrorAction SilentlyContinue) { $pyCmd = "python" }
elseif (Get-Command py -ErrorAction SilentlyContinue)    { $pyCmd = "py" }

if (-not $pyCmd) {
    Write-Err "[错误] 没有检测到 Python"
    Write-Host ""
    Write-Host "请先安装 Python 3.10 或更高版本：https://www.python.org/downloads/"
    Write-Host "安装时请务必勾选 Add Python to PATH"
    Write-Host ""
    Read-Host "按回车键退出"
    exit 1
}

Write-Step "[1/3] 准备运行环境..."

$venvPy = Join-Path $PSScriptRoot "venv\Scripts\python.exe"

# ---- 创建虚拟环境 ----
if (-not (Test-Path $venvPy)) {
    Write-Host "正在创建虚拟环境，首次运行需要 1-2 分钟，请耐心等待..."
    & $pyCmd -m venv (Join-Path $PSScriptRoot "venv")
    if ($LASTEXITCODE -ne 0) {
        Write-Err "[错误] 虚拟环境创建失败，请检查 Python 是否为完整版"
        Read-Host "按回车键退出"
        exit 1
    }
}

# ---- 安装依赖 ----
Write-Step "[2/3] 安装依赖，首次运行需要联网下载..."
& $venvPy -m pip install --upgrade pip -q
& $venvPy -m pip install -r requirements.txt -q
if ($LASTEXITCODE -ne 0) {
    Write-Err "[错误] 依赖安装失败，请检查网络"
    Write-Host "可手动执行： $venvPy -m pip install -r requirements.txt"
    Read-Host "按回车键退出"
    exit 1
}

# ---- 确保目录存在 ----
foreach ($d in @("document", "data")) {
    $p = Join-Path $PSScriptRoot $d
    if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p | Out-Null }
}

Write-Step "[3/3] 启动服务..."
Write-Host ""
Write-Host "   管理面板   http://127.0.0.1:8000" -ForegroundColor Green
Write-Host "   存储目录   $(Join-Path $PSScriptRoot 'document')" -ForegroundColor Green
Write-Host ""
Write-Host "   保持此窗口不要关闭，按 Ctrl+C 可停止服务" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor DarkCyan
Write-Host ""

# 不使用 --reload：热重载会重启进程，导致进行中的分片上传失效
& $venvPy -m uvicorn app.main:app --host 0.0.0.0 --port 8000

Write-Host ""
Write-Host "服务已停止。" -ForegroundColor Yellow
Read-Host "按回车键退出"