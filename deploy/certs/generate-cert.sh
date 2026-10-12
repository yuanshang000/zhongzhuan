#!/usr/bin/env bash
# 生成自签证书，供 Nginx 启动 HTTPS 使用。
#
# 为什么仓库里不直接放证书：
#   私钥一旦进 git 就等于公开泄露，即使后续删除，历史记录里仍能翻出来。
#   所以仓库只保留本脚本，证书由使用者在本地生成，且已被 .gitignore 忽略。
#
# 内网自测够用；公网请换成 Let's Encrypt 等受信任证书：
#   sudo certbot --nginx -d pan.example.com

set -euo pipefail

OUT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DAYS="${1:-3650}"
HOST="${2:-localhost}"

if ! command -v openssl >/dev/null 2>&1; then
    echo "错误：需要 openssl，请先安装。" >&2
    exit 1
fi

mkdir -p "$OUT_DIR"

echo "生成自签证书到 $OUT_DIR"
echo "  有效期 ${DAYS} 天，CN=${HOST}"
echo

# SAN 很重要：现代浏览器不再读 CN 字段，只认 Subject Alternative Name。
# 缺了 SAN 会导致 Chrome / Firefox 报 NET::ERR_CERT_COMMON_NAME_INVALID。
openssl req -x509 -nodes \
    -days "$DAYS" \
    -newkey rsa:2048 \
    -keyout "$OUT_DIR/server.key" \
    -out "$OUT_DIR/server.crt" \
    -subj "/C=CN/ST=Local/L=Local/O=CloudRive/CN=${HOST}" \
    -addext "subjectAltName=DNS:${HOST},DNS:localhost,IP:127.0.0.1" \
    -addext "basicConstraints=CA:FALSE" \
    -addext "keyUsage=digitalSignature,keyEncipherment" \
    -addext "extendedKeyUsage=serverAuth" \
    2>/dev/null

chmod 600 "$OUT_DIR/server.key"

echo "完成："
echo "  $OUT_DIR/server.crt"
echo "  $OUT_DIR/server.key"
echo
echo "提示：浏览器会提示证书不受信任，这是自签证书的正常现象。"
echo "公网部署请改用 Let's Encrypt。"
