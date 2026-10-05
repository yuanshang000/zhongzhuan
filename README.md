# ===== CloudRive 云端存储 =====

基于 FastAPI 的私有云盘，支持 GB 级大文件、文件夹打包下载、多选批量操作、免登录分享链接。

## 核心特性

| 能力 | 说明 |
|---|---|
| 大文件上传 | 分片并发上传（默认 4 并发），支持失败重试，GB 级无压力 |
| 不限速 | Nginx 关闭全部缓冲，直通转发；S3 后端下字节完全不经过服务器 |
| 文件夹 | 无限层级，重命名 O(1)（存储路径与文件名解耦） |
| 批量操作 | 多选后批量下载 / 移动 / 重命名 / 删除 |
| 文件夹下载 | 流式 ZIP 打包（ZIP64，支持数 TB），边压边下发 |
| 分享链接 | `/s/{slug}` 免登录打开即下载，链接永久有效直到文件删除 |
| 存储后端 | 本地磁盘 或 S3/MinIO，配置一行切换 |
| **存储路径可改** | **网页「存储设置」中随时修改，立即生效无需重启** |

## 快速开始

### 方式一：双击启动（Windows，推荐）

- **`start.bat`** —— 双击即可，纯英文输出，无编码问题
- **`start.ps1`** —— PowerShell 版，中文输出。右键「使用 PowerShell 运行」

两种脚本都会自动创建虚拟环境、安装依赖、启动服务，然后访问 `http://127.0.0.1:8000`。
首次运行需联网下载依赖，约 1-2 分钟；之后启动是秒开。

> 提示：运行后**不要关闭那个命令行窗口**，关掉窗口服务就停了。
> 需要停止时，在窗口里按 `Ctrl+C`。

**如果双击没反应**：右键 →「以管理员身份运行」，或看窗口里的错误提示。
最常见的原因是没装 Python 或装的是精简版（不含 venv 模块）。

### 方式二：Docker

```bash
cp .env.example .env    # 按需修改
docker compose up -d --build
```

### 方式三：手动

```bash
python -m venv venv
venv\Scripts\activate     # Linux: source venv/bin/activate
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

> 不要加 `--reload` 参数。它会在代码变动时重启进程，导致进行中的分片上传失败。

## 存储路径设置

**默认存储到项目目录下的 `document` 文件夹**，可在网页中随时修改：

- 点击左下角 **存储设置**
- 可直接输入路径，或点「浏览」逐级选择目录
- 支持相对路径（`./document`）与绝对路径（Linux `/data/files`、Windows `D:\files`）
- 目录不存在会自动创建
- **修改后立即生效，无需重启服务**
- 配置持久化在 `data/runtime_settings.json`，重启后仍生效

每个文件会记录上传时使用的存储目录，因此**切换存储路径后，历史文件仍可正常下载和删除**。

切换到 MinIO（S3 模式）：

```bash
# .env 中设置，或直接在网页设置里选
STORAGE_BACKEND=s3
S3_ENDPOINT=https://你的域名   # 或走 Nginx 反代
S3_ACCESS_KEY=xxx
S3_SECRET_KEY=xxx
```

MinIO 模式下浏览器**直连对象存储**上传下载，服务器只签发凭证，不经流量。

## 不限速的关键配置

Nginx 默认会缓冲请求体和响应，导致大文件上传下载变慢。以下配置是必须的：

```nginx
client_max_body_size 0;          # 不限制上传体积
proxy_request_buffering off;     # 请求体直通，不落磁盘
proxy_buffering off;             # 响应直通
proxy_read_timeout 3600s;        # 长连接超时
gzip off;                        # 大文件不压缩
```

已在 `deploy/nginx.conf` 中配置好。

若走 MinIO 直传，则需额外为 MinIO 配置同样的 Nginx 直通规则。

## 项目结构

```
cloudrive/
├── app/
│   ├── main.py              # 应用入口，路由注册
│   ├── core/                # 配置、数据库、错误
│   ├── models/node.py       # 统一节点模型（文件与文件夹同表）
│   ├── schemas/             # 请求响应模型
│   ├── services/
│   │   ├── storage.py       # 存储抽象层（local / S3 双后端）
│   │   ├── node_service.py  # 目录树、重命名、移动、容量统计
│   │   └── share_service.py # 分享链接生成
│   └── api/                 # 路由：节点、上传、下载、分享
├── frontend/                # 单页管理面板（原生 JS，无构建步骤）
├── deploy/nginx.conf        # 反向代理（不限速关键）
├── Dockerfile
└── docker-compose.yml
```

## 设计要点

**存储路径与文件名解耦**：文件实际存储在 `f/{node_id}`，文件名只存在数据库。因此重命名是纯数据库操作，即使对接 S3（其"移动"实为 copy + delete）也不会搬运字节。

**统一节点模型**：文件与文件夹共用一张表，递归查询、移动、删除逻辑统一，无需类型分支。

**递归 CTE**：子树查询用一条 SQL 完成，避免 N+1。

**ZIP64 流式打包**：文件夹打包不落整包到内存，边读边压边下发。

## 备份

数据在 `appdata` 卷内：
- `storage/` 文件内容
- `cloudrive.db` 节点元数据

```bash
docker run --rm -v cloudrive_appdata:/data -v $(pwd):/backup alpine \
  tar czf /backup/cloudrive-$(date +%F).tar.gz -C /data .
```