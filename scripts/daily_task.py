"""每日 Wiki 同步 —— 从 Paratranz API 拉取项目统计，写入 wiki 页面"""

from datetime import datetime, timezone, timedelta
import json
import os
import ssl
import urllib.request
import urllib.error
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wiki

beijing_tz = timezone(timedelta(hours=8))
now = datetime.now(beijing_tz)

PARATRANZ_PROJECT_API = "https://paratranz.cn/api/projects/4964"
PARATRANZ_ISSUES_API = "https://paratranz.cn/api/projects/4964/issues"
WIKI_PAGE = "Data:ParatranzStats.json"

# Paratranz 会拦截非浏览器 User-Agent；另外部分 Python 版本对某些 HTTPS 服务器
# 有兼容性问题，使用宽松的 SSL 上下文
_SSL_CONTEXT = ssl.create_default_context()
_SSL_CONTEXT.check_hostname = False
_SSL_CONTEXT.verify_mode = ssl.CERT_NONE


def _fetch_json(url, label):
    """拉取 JSON API，返回解析后的对象。"""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30, context=_SSL_CONTEXT) as resp:
        return json.loads(resp.read())


def sync_paratranz_stats():
    """从 Paratranz API 拉取项目统计 + issues，写入 wiki 的 Data:ParatranzStats.json 页面。"""
    # 1. 拉取项目统计
    print(f"📡 正在从 Paratranz API 获取项目数据...")
    try:
        data = _fetch_json(PARATRANZ_PROJECT_API, "project")
    except urllib.error.URLError as e:
        print(f"   ❌ 网络错误: {e}")
        raise
    except json.JSONDecodeError as e:
        print(f"   ❌ JSON 解析失败: {e}")
        raise
    print(f"   ✅ 获取成功 (project={data.get('name', '?')}, "
          f"total={data.get('stats', {}).get('total', '?')})")

    # 2. 拉取 issues，并入 data
    print(f"📡 正在从 Paratranz API 获取 issues...")
    try:
        issues = _fetch_json(PARATRANZ_ISSUES_API, "issues")
    except urllib.error.URLError as e:
        print(f"   ⚠️  issues 网络错误: {e}，跳过")
        issues = None
    except json.JSONDecodeError as e:
        print(f"   ⚠️  issues JSON 解析失败: {e}，跳过")
        issues = None

    if issues is not None:
        data["issues"] = [
            {k: r[k] for k in ("id", "title", "updatedAt")}
            for r in issues.get("results", [])
        ]
        print(f"   ✅ 获取成功 (issues={len(data['issues'])})")
    else:
        data["issues"] = []

    data["syncAt"] = now.strftime("%Y-%m-%d %H:%M:%S")

    json_text = json.dumps(data, ensure_ascii=False, indent=2)

    # 以 Bot 账号登录 wiki
    bot_user = os.environ.get("BOT_USERNAME", "")
    bot_pass = os.environ.get("BOT_PASSWORD", "")
    if bot_user and bot_pass:
        wiki.login(bot_user, bot_pass)

    print(f"📝 正在写入 Wiki 页面: {WIKI_PAGE}")
    result = wiki.edit_page(
        WIKI_PAGE,
        json_text,
        summary=f"每日自动同步 Paratranz 项目统计 ({now.strftime('%Y-%m-%d')})",
        bot=True,
    )
    print(f"   ✅ Wiki 同步完成")
    return result


if __name__ == "__main__":
    sync_paratranz_stats()
