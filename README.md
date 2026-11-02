# 航策 · 战略研究知识库（离线内网版）

本分支基于离线 demo v1.2 改造，支持 SQLite 本机演示、PostgreSQL 测试环境和 MySQL 上线环境。应用程序与数据库均可部署在不能访问互联网的内部局域网；运行期间不会调用外部服务。

数据库后端由服务端环境变量选择。建议测试环境与正式环境分别使用独立数据库、账号和连接配置。

## 部署结构

浏览器 → 内网应用服务器（Python）→ PostgreSQL 测试库 / MySQL 生产库

浏览器只访问应用服务器；数据库端口只对应用服务器开放。应用进程需要安装所选数据库的 Python 驱动。数据库表在首次启动时自动创建。

SQLite 仍作为本机演示默认项，沿用原 `data/knowledge.db`。

## 离线安装驱动

在可联网的准备环境中下载与目标操作系统、Python 版本匹配的 wheel 文件，经公司批准的介质传入内网。应用服务器上使用本地 wheelhouse 安装，不需要访问互联网：

```sh
python -m pip install --no-index --find-links ./wheelhouse -r requirements-postgresql.txt
# 或正式 MySQL 环境
python -m pip install --no-index --find-links ./wheelhouse -r requirements-mysql.txt
```

PostgreSQL 使用 psycopg 3（也兼容 psycopg2）；MySQL 使用 PyMySQL。驱动只需安装在运行 Python 应用的服务器上，不必安装在浏览器用户电脑上。

## 配置项

| 环境变量 | 说明 | 示例 |
|---|---|---|
| `AERO_DB_BACKEND` | `sqlite`、`postgresql` 或 `mysql` | `postgresql` |
| `AERO_DB_HOST` | 数据库服务器内网地址 | `10.20.1.15` |
| `AERO_DB_PORT` | PostgreSQL 默认5432，MySQL默认3306 | `5432` |
| `AERO_DB_NAME` | 已由数据库管理员创建的数据库名 | `aero_knowledge_test` |
| `AERO_DB_USER` | 应用专用数据库账号 | `aero_app` |
| `AERO_DB_PASSWORD` | 应用专用数据库密码 | 由管理员配置 |
| `AERO_APP_BIND_HOST` | 应用监听地址；本机演示默认127.0.0.1，内网共享可设为0.0.0.0 | `0.0.0.0` |
| `AERO_APP_PORT` | 应用服务端口，默认8000 | `8000` |
| `AERO_APP_PUBLIC_SCHEME` | 浏览器实际使用的协议；HTTPS反向代理后设为 `https` | `https` |
| `AERO_ALLOWED_HOSTS` | 浏览器访问应用时使用的主机名/IP，逗号分隔 | `10.20.1.20,kb.intra.example` |
| `AERO_ADMIN_INITIAL_PASSWORD` | 外部数据库首次初始化时创建本地admin账号的初始密码 | 由管理员配置 |
| `AERO_LOAD_SEED` | 外部数据库是否载入演示素材；默认不载入 | `1` |

应用账号只需要访问指定数据库并创建、读写本项目表的权限。请由数据库管理员在测试库和正式库分别创建最小权限账号；不要把数据库管理员账号写进应用配置。

### Windows PowerShell 示例（测试环境）

```powershell
$env:AERO_DB_BACKEND = "postgresql"
$env:AERO_DB_HOST = "10.20.1.15"
$env:AERO_DB_PORT = "5432"
$env:AERO_DB_NAME = "aero_knowledge_test"
$env:AERO_DB_USER = "aero_app_test"
$env:AERO_DB_PASSWORD = "<由管理员提供>"
$env:AERO_APP_BIND_HOST = "0.0.0.0"
$env:AERO_APP_PORT = "8000"
$env:AERO_ALLOWED_HOSTS = "10.20.1.20,kb-test.intra.example"
$env:AERO_ADMIN_INITIAL_PASSWORD = "<首次初始化用的强密码>"
py -3 server.py --no-browser
```

正式环境将 `AERO_DB_BACKEND` 改为 `mysql`，数据库地址、库名、账号和密码使用正式环境配置。客户端使用应用服务器内网地址访问；防火墙只需允许客户端访问应用端口，并允许应用服务器访问数据库端口。生产环境建议由内网 HTTPS 反向代理终止 TLS，并设置 `AERO_APP_PUBLIC_SCHEME=https`；程序会据此校验请求来源并设置 Secure 登录 Cookie。不要将应用或数据库端口发布到互联网。

`AERO_ADMIN_INITIAL_PASSWORD` 只在目标数据库尚无 `admin` 用户时生效。当前版本沿用原有单一 admin 登录方式，尚未提供用户管理、角色权限或企业统一认证；多人共用前应由项目管理员评估账号与授权需求。

## 启动与功能

本机演示仍可直接运行 `start.bat` 或 `start.sh`，不配置数据库环境变量时使用 SQLite 和内置演示数据。共享部署请在应用服务器设置环境变量后运行 `python server.py --no-browser`，不要用默认本机配置期待其他电脑访问。

现有功能包括素材与模板维护、离线标签建议、报告草稿、CSV批量导入、素材CSV备份与恢复。页面侧边栏会显示实际配置的数据库类型。

## 既有 SQLite 数据

切换到 PostgreSQL 或 MySQL 后会连接新的数据库，原 `data/knowledge.db` 不会自动迁移。切换前请先备份旧目录和素材；当前界面支持素材 CSV 导入/导出，但不会迁移模板、账号和历史记录。正式切换前需单独安排完整数据迁移，并核对素材、模板和账号。

## 打包与开发

```sh
python -m unittest discover -s tests -v
python tools/build_offline_package.py --output-dir ./dist
```

打包脚本生成不含数据库密码和本地数据的 v1.3 ZIP。该 ZIP 仅包含程序文件与驱动安装说明；数据库驱动需按目标环境准备离线 wheelhouse。现有 v1.2 ZIP 保留作为 SQLite 单机版本归档。
