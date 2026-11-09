#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
iKuuu Cookie 获取助手（本地运行，不在青龙里跑）

iKuuu 登录有极验验证码，纯脚本过不去。这个工具会打开一个真实浏览器，
你手动完成登录（含验证码），脚本自动把 PHPSESSID 抓出来并打印，
复制到青龙环境变量 IKUUU_COOKIE 即可。

用法
----
    pip install playwright
    playwright install chromium
    python get_cookie.py            # 默认打开 ikuuu.top
    python get_cookie.py ikuuu.pw   # 指定域名

结束后会生成 cookies.txt，里面每行一个账号的 cookie，可直接贴进青龙。
"""

import re
import sys
import time

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("缺少依赖 playwright，请先执行：")
    print("  pip install playwright")
    print("  playwright install chromium")
    sys.exit(1)

DOMAIN = (sys.argv[1] if len(sys.argv) > 1 else "ikuuu.top").strip()
LOGIN_URL = f"https://{DOMAIN}/auth/login"
OUT_FILE = "cookies.txt"
TIMEOUT = 300  # 最长等待 5 分钟


def collect_cookie(context):
    """从浏览器上下文里拼出 cookie 字符串，必须有 PHPSESSID。"""
    parts = []
    has_sess = False
    for c in context.cookies():
        name, value = c.get("name"), c.get("value")
        if not name or not value:
            continue
        if name == "PHPSESSID":
            has_sess = True
        parts.append(f"{name}={value}")
    return ("; ".join(parts), has_sess)


def main():
    print("=" * 54)
    print("iKuuu Cookie 获取助手")
    print("=" * 54)
    print(f"即将打开：{LOGIN_URL}")
    print("请在浏览器里完成登录（含验证码），登录成功会自动抓取。")
    print("多账号的话，登录完一个后退出登录，再登录下一个，脚本会依次收集。")
    print()

    saved = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--start-maximized"])
        context = browser.new_context(no_viewport=True)
        page = context.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)

        deadline = time.time() + TIMEOUT
        last_cookie = ""
        idle = 0

        while time.time() < deadline:
            time.sleep(2)
            try:
                url = page.url
            except Exception:
                print("浏览器已关闭，结束。")
                break

            cookie_str, has_sess = collect_cookie(context)
            logged = "/auth/login" not in url and "/user" in url

            if logged and has_sess:
                if cookie_str != last_cookie:
                    saved.append(cookie_str)
                    last_cookie = cookie_str
                    print(f"[+] 已抓取第 {len(saved)} 个账号的 cookie（{len(cookie_str)} 字节）")
                    print("    如需继续添加账号，请点右上角退出登录，再登录下一个。")
                    print("    全部完成后直接关闭浏览器窗口即可。")
                idle = 0
            else:
                idle += 1
                # 长时间没有新登录动作，且已抓到过，自动收工
                if saved and idle > 30:
                    print("[i] 60 秒无新动作，自动结束。")
                    break

        try:
            browser.close()
        except Exception:
            pass

    if not saved:
        print("\n[!] 没有抓到有效 cookie。请确认登录是否成功（地址栏应变成 /user）。")
        return 1

    # 去重
    uniq = list(dict.fromkeys(saved))
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(uniq) + "\n")

    print("\n" + "=" * 54)
    print(f"共收集 {len(uniq)} 个账号，已写入 {OUT_FILE}")
    print("=" * 54)
    for i, c in enumerate(uniq, 1):
        sid = re.search(r"PHPSESSID=([^;]+)", c)
        print(f"  账号 {i}：PHPSESSID={sid.group(1) if sid else '?'}")
    print()
    print("下一步：把上面每行的完整 cookie 复制到青龙面板的 IKUUU_COOKIE 变量，")
    print("多账号用换行分隔。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
