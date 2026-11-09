#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cron: 30 8 * * *
new Env('iKuuu 自动签到')

iKuuu 多账号自动签到 —— 适配青龙面板 / 本地运行 / GitHub Actions。

说明
----
iKuuu 的登录接口（POST /auth/login）在 phase=password 时强制校验极验 Geetest
验证码（captcha_result），纯 HTTP 请求会被判 captcha_failed。所以本脚本以
Cookie 模式为主：从浏览器取一次 PHPSESSID，之后每天直接调 /user/checkin，
不需要登录、不碰验证码。账号密码模式保留作为兼容路径，被拦截时会明确提示。

环境变量
--------
IKUUU_COOKIE      主配置。多账号每行一个，值里含 PHPSESSID 即可，
                  例如 PHPSESSID=abc123; 其他 cookie 可以一并带上
IKUUU_ACCOUNTS    可选。每行一个 "邮箱:密码"，当 cookie 数量不足时补位尝试
IKUUU_HOST        可选。强制指定域名，如 ikuuu.top（默认自动探测）
IKUUU_DELAY       可选。账号间隔秒数，默认 3
IKUUU_RETRY       可选。单账号重试次数，默认 2
IKUUU_TIMEOUT     可选。单次请求超时秒数，默认 15
IKUUU_NO_NOTIFY   可选。设为 1 跳过消息推送
IKUUU_COOKIE_FILE 可选。cookie 缓存路径，默认脚本同目录 ikuuu_cookies.json
"""

import json
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone

try:
    import requests
except ImportError:
    print("缺少依赖 requests，请先安装：pip install requests")
    sys.exit(1)


# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------

# iKuuu 会轮换域名，按经验顺序探测，命中即用
BASE_DOMAINS = [
    "ikuuu.top",
    "ikuuu.one",
    "ikuuu.pw",
    "ikuuu.dev",
    "ikuuu.me",
    "ikuuu.fyi",
    "ikuuu.win",
    "ikuuu.eu",
    "ikuuu.uk",
    "ikuuu.nl",
    "ikuuu.de",
    "ikuuu.ch",
    "ikuuu.art",
    "ikuuu.boo",
    "ikuuu.ltd",
    "ikuuu.org",
    "ikuuu.live",
    "ikuuu.bar",
]

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
]

TZ_CN = timezone(timedelta(hours=8))
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


# --------------------------------------------------------------------------
# 通用工具
# --------------------------------------------------------------------------


def env(name, default=""):
    return (os.getenv(name) or default).strip()


def cfg_float(name, default):
    try:
        return float(env(name) or default)
    except ValueError:
        return float(default)


def log(msg=""):
    print(msg, flush=True)


def split_multi(raw):
    """把多行 / & / ; 分隔的配置拆成列表。"""
    if not raw:
        return []
    return [p.strip() for p in re.split(r"[\r\n&;]+", raw) if p.strip()]


def pick_ua():
    return random.choice(UA_POOL)


def mask(text, keep=4):
    """日志脱敏，避免把 cookie / 密码打进面板日志。"""
    text = str(text or "")
    if len(text) <= keep * 2:
        return "*" * len(text)
    return f"{text[:keep]}{'*' * max(3, len(text) - keep * 2)}{text[-keep:]}"


def mask_email(email):
    if not email or "@" not in email:
        return mask(email, 3)
    name, domain = email.split("@", 1)
    if len(name) > 2:
        name = name[:2] + "*" * (len(name) - 2)
    else:
        name = name[:1] + "*"
    return f"{name}@{domain}"


def extract_phpsessid(cookie_str):
    m = re.search(r"PHPSESSID=([^;,\s]+)", cookie_str or "")
    return m.group(1) if m else None


def apply_cookie(sess, domain, cookie_str):
    """把 'k=v; k2=v2' 形式的 cookie 全部塞进 session。"""
    for part in (cookie_str or "").split(";"):
        if "=" not in part:
            continue
        key, val = part.split("=", 1)
        key, val = key.strip(), val.strip()
        if key and val:
            sess.cookies.set(key, val, domain=domain)


def load_accounts():
    accounts = []
    for line in split_multi(env("IKUUU_ACCOUNTS")):
        if ":" not in line:
            log(f"[配置] 跳过格式不正确的账号条目（应为 邮箱:密码）：{mask(line)}")
            continue
        email, passwd = line.split(":", 1)
        email, passwd = email.strip(), passwd.strip()
        if email and passwd:
            accounts.append((email, passwd))
    return accounts


# --------------------------------------------------------------------------
# cookie 缓存
# --------------------------------------------------------------------------


class CookieStore:
    """把校验通过的 cookie 落盘复用，减少重复登录。"""

    def __init__(self, path=None):
        self.path = path or env("IKUUU_COOKIE_FILE") or os.path.join(
            SCRIPT_DIR, "ikuuu_cookies.json"
        )
        self.data = {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                self.data = raw
        except Exception:
            self.data = {}

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            log(f"[缓存] 写入失败：{exc}")

    def get(self, key):
        item = self.data.get(key) or {}
        cookie = item.get("cookie") or ""
        return cookie if extract_phpsessid(cookie) else ""

    def put(self, key, cookie):
        if not extract_phpsessid(cookie):
            return
        self.data[key] = {"cookie": cookie, "ts": int(time.time())}
        self.save()

    def drop(self, key):
        if key in self.data:
            self.data.pop(key, None)
            self.save()


# --------------------------------------------------------------------------
# 网络
# --------------------------------------------------------------------------


def new_session():
    sess = requests.Session()
    sess.headers.update(
        {
            "User-Agent": pick_ua(),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
    )
    return sess


def probe_domain(forced=None):
    """探测当前可用域名。登录页被 base64 混淆，只判断 HTTP 200 即可。"""
    candidates = [forced] if forced else BASE_DOMAINS
    timeout = min(cfg_float("IKUUU_TIMEOUT", 15), 10)

    for dom in candidates:
        try:
            resp = requests.get(
                f"https://{dom}/auth/login",
                timeout=timeout,
                headers={"User-Agent": pick_ua()},
                allow_redirects=True,
            )
            if resp.status_code == 200 and len(resp.text) > 1000:
                log(f"[域名] 使用域名：{dom}")
                return dom
            log(f"[域名] {dom} 返回 {resp.status_code}，换下一个")
        except Exception as exc:
            log(f"[域名] {dom} 不可达（{type(exc).__name__}），换下一个")
    return None


def is_logged_in(sess, domain):
    """未登录访问 /user 会被 302 到 /auth/login，据此判断 cookie 是否有效。"""
    try:
        resp = sess.get(
            f"https://{domain}/user",
            timeout=cfg_float("IKUUU_TIMEOUT", 15),
            allow_redirects=True,
        )
        return "/auth/login" not in resp.url and resp.status_code == 200
    except Exception:
        return False


def login(sess, domain, email, passwd):
    """
    账号密码登录。iKuuu 在 phase=password 时强制校验极验 captcha_result，
    纯 HTTP 请求会被判 captcha_failed —— 此时返回明确提示，引导改用 cookie。
    """
    timeout = cfg_float("IKUUU_TIMEOUT", 15)
    try:
        sess.get(f"https://{domain}/auth/login", timeout=timeout)
        resp = sess.post(
            f"https://{domain}/auth/login",
            data={
                "host": domain,
                "phase": "password",
                "email": email,
                "passwd": passwd,
                "remember_me": "on",
                "pageLoadedAt": str(int(time.time() * 1000)),
                "captcha_result": "",
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
                "Origin": f"https://{domain}",
                "Referer": f"https://{domain}/auth/login",
            },
            timeout=timeout,
            allow_redirects=True,
        )
    except Exception as exc:
        return None, f"请求异常 {type(exc).__name__}"

    # 万一接口放宽，直接下发了 PHPSESSID
    if sess.cookies.get("PHPSESSID") and is_logged_in(sess, domain):
        cookie_str = "; ".join(f"{k}={v}" for k, v in sess.cookies.get_dict().items())
        return cookie_str, "登录成功"

    try:
        data = resp.json()
    except Exception:
        return None, f"登录响应非 JSON（HTTP {resp.status_code}）"

    phase = data.get("phase")
    result = data.get("result")
    msg = str(data.get("msg") or "").strip()

    if result == "captcha_failed":
        return None, "登录需图形验证码（极验），请改用 IKUUU_COOKIE 模式"
    if phase == "totp":
        return None, "账号开启了二次验证（2FA），请改用 cookie 模式"
    if phase == "email_code":
        return None, "登录需邮箱验证码，请改用 cookie 模式"
    if phase == "reverse_email_verify":
        return None, "登录需邮件确认，请改用 cookie 模式"
    if phase == "authenticated":
        cookie_str = "; ".join(f"{k}={v}" for k, v in sess.cookies.get_dict().items())
        if extract_phpsessid(cookie_str):
            return cookie_str, "登录成功"
    return None, f"登录失败：{msg or result or '未知原因'}"


def checkin(sess, domain):
    """调用签到接口，返回 (是否达成, 描述)。"""
    timeout = cfg_float("IKUUU_TIMEOUT", 15)
    try:
        resp = sess.post(
            f"https://{domain}/user/checkin",
            data=b"",
            headers={
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
                "Origin": f"https://{domain}",
                "Referer": f"https://{domain}/user",
            },
            timeout=timeout,
            allow_redirects=False,
        )
    except Exception as exc:
        return False, f"请求异常 {type(exc).__name__}"

    # 未登录 / cookie 失效 → 302 跳登录页
    if resp.status_code in (301, 302, 303, 307):
        return False, "cookie 已失效（签到接口跳转到登录页）"
    if resp.status_code in (403, 429):
        return False, f"被风控拦截（HTTP {resp.status_code}）"

    body = (resp.text or "").strip()
    if not body.startswith("{"):
        return False, f"非 JSON 响应（HTTP {resp.status_code}）：{body[:100]}"

    try:
        data = json.loads(body)
    except Exception:
        return False, f"JSON 解析失败：{body[:100]}"

    ret = data.get("ret")
    msg = str(data.get("msg") or "").strip()
    traffic = data.get("traffic") or data.get("trafficInfo") or ""
    detail = " ".join(x for x in [msg, str(traffic)] if x).strip() or body[:100]

    if ret == 0:
        return True, detail or "签到成功"
    if ret == 1:
        return True, detail or "今日已签到"
    return False, f"ret={ret} {detail}"


# --------------------------------------------------------------------------
# 单账号流程
# --------------------------------------------------------------------------


def run_one(idx, total, email, passwd, cookie_input, domain, store, retry):
    label = mask_email(email) if "@" in (email or "") else mask(email, 6)
    tag = f"[{idx}/{total}] {label}"
    log(f"\n{tag} 开始处理")

    sess = new_session()
    cookie = cookie_input or store.get(email)
    source = "环境变量" if cookie_input else "本地缓存"

    if cookie:
        apply_cookie(sess, domain, cookie)
        if is_logged_in(sess, domain):
            log(f"{tag} {source} cookie 校验通过")
        else:
            log(f"{tag} {source} cookie 已失效")
            store.drop(email)
            cookie = ""

    if not cookie:
        if not passwd:
            return {"email": email, "ok": False, "msg": "cookie 已失效，且未提供账号密码"}
        log(f"{tag} 尝试账号密码登录")
        cookie, msg = login(sess, domain, email, passwd)
        if not cookie:
            return {"email": email, "ok": False, "msg": msg}
        log(f"{tag} {msg}")
        store.put(email, cookie)

    detail = "未知错误"
    for attempt in range(1, retry + 1):
        ok, detail = checkin(sess, domain)
        if ok:
            log(f"{tag} 签到结果：{detail}")
            return {"email": email, "ok": True, "msg": detail}

        log(f"{tag} 第 {attempt}/{retry} 次失败：{detail}")

        if "cookie 已失效" in detail and passwd:
            store.drop(email)
            sess = new_session()
            cookie, msg = login(sess, domain, email, passwd)
            if not cookie:
                return {"email": email, "ok": False, "msg": f"cookie 失效，重登失败：{msg}"}
            store.put(email, cookie)
            log(f"{tag} 重新登录成功，继续签到")
            continue

        if attempt < retry:
            time.sleep(random.uniform(2, 5))

    return {"email": email, "ok": False, "msg": detail}


# --------------------------------------------------------------------------
# 通知
# --------------------------------------------------------------------------


def send_notify(title, content):
    if env("IKUUU_NO_NOTIFY").lower() in ("1", "true", "yes", "on"):
        log("[通知] IKUUU_NO_NOTIFY 已开启，跳过推送")
        return

    # 1) 青龙内置通知（notify.py 会读取面板里配置的推送渠道）
    try:
        if SCRIPT_DIR not in sys.path:
            sys.path.insert(0, SCRIPT_DIR)
        from notify import send  # type: ignore

        send(title, content)
        log("[通知] 已通过青龙 notify 推送")
        return
    except Exception:
        pass

    # 2) Server 酱
    key = env("SERVERCHAN_SENDKEY") or env("PUSH_KEY")
    if key:
        try:
            resp = requests.post(
                f"https://sctapi.ftqq.com/{key}.send",
                data={"title": title, "desp": content},
                timeout=10,
            )
            log(f"[通知] Server 酱推送完成（HTTP {resp.status_code}）")
            return
        except Exception as exc:
            log(f"[通知] Server 酱推送失败：{exc}")

    log("[通知] 未配置推送渠道，仅输出日志")


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------


def build_tasks():
    """cookie 优先；cookie 不够时用邮箱密码补位。"""
    accounts = load_accounts()
    cookies = split_multi(env("IKUUU_COOKIE"))

    tasks = []
    seen = set()

    for raw in cookies:
        sid = extract_phpsessid(raw)
        if not sid:
            log(f"[配置] IKUUU_COOKIE 中有一条缺少 PHPSESSID，已跳过：{mask(raw)}")
            continue
        key = f"cookie-{sid[:10]}"
        if key in seen:
            continue
        seen.add(key)
        tasks.append({"email": key, "passwd": "", "cookie": raw})

    for email, passwd in accounts:
        if email in seen:
            continue
        seen.add(email)
        tasks.append({"email": email, "passwd": passwd, "cookie": ""})

    return tasks


def main():
    start = time.time()
    log("=" * 54)
    log("iKuuu 自动签到")
    log(f"运行时间：{datetime.now(TZ_CN).strftime('%Y-%m-%d %H:%M:%S')}")
    log("=" * 54)

    tasks = build_tasks()
    if not tasks:
        log("未检测到任何账号配置。")
        log("请在青龙面板添加环境变量 IKUUU_COOKIE，值为浏览器里的 PHPSESSID，")
        log("多账号用换行分隔，例如：")
        log("  PHPSESSID=aaaa1111")
        log("  PHPSESSID=bbbb2222")
        return 1

    log(f"共 {len(tasks)} 个账号待签到")

    domain = probe_domain(env("IKUUU_HOST") or None)
    if not domain:
        log("所有候选域名均不可用，请检查网络或手动设置 IKUUU_HOST")
        send_notify("iKuuu 签到失败", "所有候选域名均不可用，请检查网络或设置 IKUUU_HOST")
        return 1

    store = CookieStore()
    delay = cfg_float("IKUUU_DELAY", 3)
    retry = max(1, int(cfg_float("IKUUU_RETRY", 2)))

    results = []
    for i, task in enumerate(tasks, 1):
        try:
            results.append(
                run_one(
                    i, len(tasks), task["email"], task["passwd"], task["cookie"], domain, store, retry
                )
            )
        except Exception as exc:
            log(f"[{i}/{len(tasks)}] 未预期异常：{type(exc).__name__}: {exc}")
            results.append(
                {"email": task["email"], "ok": False, "msg": f"异常 {type(exc).__name__}"}
            )
        if i < len(tasks):
            time.sleep(delay + random.uniform(0, 2))

    ok_count = sum(1 for r in results if r["ok"])
    fail_count = len(results) - ok_count

    log("\n" + "=" * 54)
    log(f"汇总：成功 {ok_count} / 失败 {fail_count}，耗时 {time.time() - start:.1f}s")
    lines = []
    for r in results:
        line = f"{'✅' if r['ok'] else '❌'} {r['email']}：{r['msg']}"
        log("  " + line)
        lines.append(line)
    log("=" * 54)

    title = f"iKuuu 签到 {'全部成功' if fail_count == 0 else f'成功{ok_count} 失败{fail_count}'}"
    content = (
        f"## {title}\n\n"
        f"- 域名：{domain}\n"
        f"- 时间：{datetime.now(TZ_CN).strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"- 账号：{len(results)} 个\n\n" + "\n".join(f"- {x}" for x in lines)
    )
    send_notify(title, content)
    return 0


if __name__ == "__main__":
    sys.exit(main())
