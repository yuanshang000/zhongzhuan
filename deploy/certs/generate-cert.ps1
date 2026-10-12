# 生成自签证书，供 Nginx 启动 HTTPS 使用（Windows 版）。
#
# 为什么仓库里不直接放证书：
#   私钥一旦进 git 就等于公开泄露，即使后续删除，历史记录里仍能翻出来。
#   所以仓库只保留本脚本，证书由使用者在本地生成，且已被 .gitignore 忽略。
#
# 用法：
#   .\generate-cert.ps1                 # 默认 3650 天，CN=localhost
#   .\generate-cert.ps1 -Days 365 -HostName pan.example.com
#
# 内网自测够用；公网请换成 Let's Encrypt 等受信任证书。

param(
    [int]$Days = 3650,
    [string]$HostName = "localhost"
)

$ErrorActionPreference = "Stop"
$outDir = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not (Get-Command openssl -ErrorAction SilentlyContinue)) {
    Write-Host "错误：需要 openssl，请先安装。" -ForegroundColor Red
    Write-Host "  Git for Windows 自带 openssl，可直接用其 bin 目录下的 exe。" -ForegroundColor Yellow
    exit 1
}

New-Item -ItemType Directory -Force -Path $outDir | Out-Null

Write-Host "生成自签证书到 $outDir"
Write-Host "  有效期 $Days 天，CN=$HostName"
Write-Host ""

# SAN 很重要：现代浏览器不再读 CN 字段，只认 Subject Alternative Name。
# 缺了 SAN 会导致 Chrome / Firefox 报 NET::ERR_CERT_COMMON_NAME_INVALID。
$cnf = Join-Path $env:TEMP "cloudrive_openssl.cnf"
@"
[req]
distinguished_name = dn
x509_extensions = ext
prompt = no

[dn]
CN = $HostName

[ext]
subjectAltName = DNS:$HostName, DNS:localhost, IP:127.0.0.1
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
"@ | Set-Content -Path $cnf -Encoding UTF8

$key = Join-Path $outDir "server.key"
$crt = Join-Path $outDir "server.crt"

# 2>$null 屏蔽 openssl 在 Windows 上写 stderr 造成的红字，实际不影响结果
openssl req -x509 -nodes -days $Days -newkey rsa:2048 `
    -keyout $key -out $crt -config $cnf 2>$null

if ($LASTEXITCODE -ne 0) {
    Write-Host "生成失败，请检查 openssl 是否可用。" -ForegroundColor Red
    exit 1
}

Remove-Item $cnf -Force -ErrorAction SilentlyContinue

Write-Host "完成：" -ForegroundColor Green
Write-Host "  $crt"
Write-Host "  $key"
Write-Host ""
Write-Host "提示：浏览器会提示证书不受信任，这是自签证书的正常现象。"
Write-Host "公网部署请改用 Let's Encrypt。"
