# iKuuu 自动签到

iKuuu 多账号每日自动签到，适配**青龙面板**，也能本地跑或挂 GitHub Actions。

- 多账号，cookie 一行一个
- 自动探测可用域名（iKuuu 经常换域名，脚本内置 18 个候选）
- cookie 失效自动识别，不会静默失败
- 支持青龙内置推送 / Server 酱
- 日志脱敏，cookie 和密码不会明文出现在面板日志里

## 为什么是 Cookie 模式

这不是偷懒，是接口限制。

iKuuu 的登录接口 `POST /auth/login` 在 `phase=password` 阶段**强制校验极验 Geetest 验证码**，请求体里必须带有效的 `captcha_result`。纯 HTTP 请求拿不到这个值，服务端会直接返回：

```json
{"phase":"password","result":"captcha_failed","msg":"系统无法接受您的验证结果，请刷新页面后重试。"}
```

所以纯脚本走账号密码登录是走不通的（除非接打码平台或跑无头浏览器过验证码，代价都不小）。

而签到接口 `POST /user/checkin` 只认 `PHPSESSID`，**不需要验证码**。因此正确做法是：浏览器里登录一次拿到 cookie，脚本每天用它签到。cookie 有效期通常很长，过期了重新取一次即可。

脚本仍然保留了账号密码模式，仅作为接口万一放宽时的兼容路径，被拦时会明确提示改用 cookie。

## 快速开始（青龙面板）

### 1. 安装依赖

青龙面板 → 依赖管理 → Python3 → 新建依赖：

```
requests
```

### 2. 获取 cookie

浏览器登录 <https://ikuuu.top>，按 `F12` 打开开发者工具：

- **方式 A（推荐）**：`Application`（应用）→ `Cookies` → `https://ikuuu.top` → 找到 `PHPSESSID`，复制它的值
- **方式 B**：`Network`（网络）→ 刷新页面 → 点任意请求 → `Request Headers` → 复制 `Cookie` 整行

拿到的内容长这样：

```
PHPSESSID=8f3a1c9e2b7d4a6f0e5c8b1d3a7f9e2c
```

### 3. 配置环境变量

青龙面板 → 环境变量 → 新建：

| 名称 | 值 |
|------|-----|
| `IKUUU_COOKIE` | `PHPSESSID=你的值` |

**多账号**：值里换行分隔，一行一个账号。

```
PHPSESSID=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa11
PHPSESSID=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb22
```

> 青龙界面里直接按回车换行即可；用 API 或 `.env` 写入时用 `\n`。

### 4. 建定时任务

青龙面板 → 定时任务 → 新建：

| 字段 | 值 |
|------|-----|
| 名称 | iKuuu 签到 |
| 命令 | `task ikuuu_checkin.py` |
| 定时规则 | `30 8 * * *` |

也可以直接把 `ikuuu_checkin.py` 丢进 `/ql/scripts/`，脚本头部自带 `cron:` 和 `new Env()` 元信息，青龙会自动识别。

## 环境变量一览

| 变量 | 必填 | 说明 |
|------|------|------|
| `IKUUU_COOKIE` | 是 | 多账号 cookie，换行分隔 |
| `IKUUU_ACCOUNTS` | 否 | `邮箱:密码`，仅 cookie 缺失时尝试，基本用不上 |
| `IKUUU_HOST` | 否 | 强制指定域名，如 `ikuuu.top`，跳过自动探测 |
| `IKUUU_DELAY` | 否 | 账号间隔秒数，默认 `3` |
| `IKUUU_RETRY` | 否 | 单账号重试次数，默认 `2` |
| `IKUUU_TIMEOUT` | 否 | 请求超时秒数，默认 `15` |
| `IKUUU_NO_NOTIFY` | 否 | 设 `1` 关闭推送 |
| `IKUUU_COOKIE_FILE` | 否 | cookie 缓存路径，默认脚本同目录 |

## 通知

优先级：

1. 青龙内置 `notify.py` —— 在面板「通知设置」里配好任意渠道（TG、企业微信、钉钉、PushPlus…），脚本自动调用
2. `SERVERCHAN_SENDKEY` 或 `PUSH_KEY` —— Server 酱 SendKey
3. 都没有就只打日志

## 本地运行

```bash
pip install -r requirements.txt

# Windows PowerShell
$env:IKUUU_COOKIE="PHPSESSID=xxxx"
$env:PYTHONIOENCODING="utf-8"
python ikuuu_checkin.py

# Linux / macOS
IKUUU_COOKIE="PHPSESSID=xxxx" python3 ikuuu_checkin.py
```

多账号在 shell 里用 `&` 分隔也行：

```bash
IKUUU_COOKIE="PHPSESSID=aaa&PHPSESSID=bbb" python3 ikuuu_checkin.py
```

## 取 cookie 的辅助工具

嫌手动复制麻烦，可以用 `get_cookie.py`：它开一个真实浏览器，你手动登录，脚本自动抓 cookie 并写入 `cookies.txt`。

```bash
pip install playwright
playwright install chromium
python get_cookie.py            # 默认 ikuuu.top
python get_cookie.py ikuuu.pw   # 换域名
```

支持连续登录多个账号，逐个收集。这个工具只在本地用，不要放青龙里跑。

## GitHub Actions

仓库自带 `.github/workflows/checkin.yml`，每天北京时间 08:30 自动跑。

1. Fork 或直接用本仓库
2. `Settings` → `Secrets and variables` → `Actions` → `New repository secret`
3. Name 填 `IKUUU_COOKIE`，Value 填你的 cookie（多账号换行）
4. `Actions` 页面手动触发一次验证

> 公开仓库里 cookie 放在 Secrets 中是加密的，不会暴露。但请注意 GitHub 的 Actions 日志对公开仓库是公开可见的，本脚本已对 cookie 做脱敏处理。

## 常见问题

**签到提示「cookie 已失效」**
cookie 过期了。重新从浏览器取一次，更新 `IKUUU_COOKIE`。

**提示「登录需图形验证码」**
说明走到了账号密码模式。请改用 cookie 模式，这是设计如此，不是 bug。

**提示「所有候选域名均不可用」**
iKuuu 又换域名了，或者青龙容器网络不通。把新域名填到 `IKUUU_HOST` 试试。

**签到成功但没收到推送**
检查面板「通知设置」是否配置了渠道，或者单独设置 `SERVERCHAN_SENDKEY`。

**会不会封号**
脚本请求频率很低（一天一次，多账号间隔 3 秒），行为与手动签到一致。但不做任何保证，风险自担。

## 免责声明

本项目仅供学习 Python 网络请求与自动化技术使用。请遵守目标网站的用户协议，不要用于商业用途或大规模滥用。使用本脚本产生的一切后果由使用者自行承担。

## License

[MIT](LICENSE)
