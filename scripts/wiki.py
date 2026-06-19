"""
gtnh.huijiwiki.com MediaWiki API 工具
用法：直接运行或 import 后调用各函数
"""

import urllib.request
import urllib.parse
import http.cookiejar
import json
import os
import sys
import io

# 强制 UTF-8 输出，解决 Windows GBK 编码乱码
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


def _load_dotenv():
    """加载项目根目录的 .env 文件到 os.environ（不覆盖已有环境变量）。"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # 从脚本所在目录向上搜索 .env
    search = [script_dir]
    parent = os.path.dirname(script_dir)
    search.append(parent)
    search.append(os.path.dirname(parent))
    for d in search:
        env_path = os.path.join(d, ".env")
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as _f:
                for _line in _f:
                    _line = _line.strip()
                    if not _line or _line.startswith("#"):
                        continue
                    _k, _sep, _v = _line.partition("=")
                    _k, _v = _k.strip(), _v.strip()
                    if _k and _v:
                        os.environ.setdefault(_k, _v)
            return


_load_dotenv()

BASE = os.environ.get("BASE_URL", "https://gtnh.huijiwiki.com/api.php")
AUTHKEY = os.environ.get("AUTHKEY", "")
USERNAME = os.environ.get("EDITOR_USERNAME", "")
PASSWORD = os.environ.get("EDITOR_PASSWORD", "")
WIKITEXT_DIR = os.path.join(os.path.dirname(__file__), "wikitext")

# ── 底层 HTTP ──────────────────────────────────────────────

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
_logged_in = False


def _split_namespace(title: str):
    head, sep, tail = title.partition(":")
    return (head, tail) if sep else ("", title)


def _canonical_namespace(ns: str) -> str:
    aliases = {
        "模板": "Template",
        "Template": "Template",
        "模块": "Module",
        "Module": "Module",
        "零件": "Gadget",
        "Gadget": "Gadget",
        "零件定义": "Gadget definition",
        "Gadget definition": "Gadget definition",
        "分类": "Category",
        "Category": "Category",
        "文件": "File",
        "File": "File",
    }
    return aliases.get(ns, ns)


# 存储目录统一使用中文名（避免 Module/ 和 模块/ 分家）
_STORAGE_NS = {
    "Template": "模板",
    "Module": "模块",
    "Gadget": "零件",
    "Gadget definition": "零件定义",
    "Category": "分类",
    "File": "文件",
    "模板": "模板",
    "模块": "模块",
    "零件": "零件",
    "零件定义": "零件定义",
    "分类": "分类",
    "文件": "文件",
}


# 需要保留原始文件扩展名的命名空间
_FILES_NS = {"File"}


def _encode_title_part(part: str) -> str:
    """Encode filesystem-hostile characters in a title segment."""
    return (
        part.replace("%", "%25")
        .replace(":", "%3A")
        .replace("\\", "%5C")
        .replace("*", "%2A")
        .replace("?", "%3F")
        .replace('"', "%22")
        .replace("<", "%3C")
        .replace(">", "%3E")
        .replace("|", "%7C")
    )


def _decode_title_part(part: str) -> str:
    """Decode a title segment produced by _encode_title_part."""
    for src, dst in (
        ("%7C", "|"),
        ("%3E", ">"),
        ("%3C", "<"),
        ("%22", '"'),
        ("%3F", "?"),
        ("%2A", "*"),
        ("%5C", "\\"),
        ("%3A", ":"),
        ("%25", "%"),
    ):
        part = part.replace(src, dst)
    return part


def _contentmodel_to_ext(cm: str) -> str:
    """将 MediaWiki contentmodel 映射到文件扩展名"""
    mapping = {
        "wikitext": ".wikitext",
        "Scribunto": ".lua",
        "javascript": ".js",
        "css": ".css",
        "json": ".json",
        "HtmlMustache": ".html",
        "html": ".html",
        "sanitized-css": ".css",
    }
    return mapping.get(cm, ".wikitext")


def _title_to_storage_relpath(title: str, contentmodel: str = None) -> str:
    ns, leaf = _split_namespace(title)
    canonical_ns = _canonical_namespace(ns)
    leaf_parts = [_encode_title_part(p) for p in leaf.split("/")]
    # 扩展名优先级：contentmodel > 命名空间规则 > 默认 .wikitext
    if contentmodel:
        ext = _contentmodel_to_ext(contentmodel)
    elif canonical_ns in _FILES_NS:
        # 文件名本身已带扩展名，不需要外加
        ext = ""
    elif canonical_ns == "Gadget definition":
        ext = ".json"
    elif leaf.endswith(".js") and canonical_ns in {"MediaWiki", "Gadget"}:
        ext = ""
    elif leaf.endswith(".css") and canonical_ns in {"MediaWiki", "Gadget"}:
        ext = ""
    elif canonical_ns == "Module":
        ext = ".lua"
    else:
        ext = ".wikitext"
    # 避免双重后缀：若 leaf 已以此扩展名结尾则不再追加
    if ext and leaf.lower().endswith(ext.lower()):
        ext = ""
    if ns:
        parts = [_STORAGE_NS.get(ns, ns)] + leaf_parts
    else:
        parts = ["0"] + leaf_parts
    return os.path.join(*parts) + ext


def _storage_relpath_to_title(rel_path: str) -> str:
    # 只剥离 _title_to_storage_relpath 主动追加的后缀
    # .css / .js 在 Gadget/MediaWiki 等命名空间中原生属于页面名，不能剥离
    if rel_path.endswith(".wikitext"):
        rel = rel_path[: -len(".wikitext")]
    elif rel_path.endswith(".lua"):
        rel = rel_path[: -len(".lua")]
    elif rel_path.endswith(".html"):
        rel = rel_path[: -len(".html")]
    else:
        rel = rel_path
    parts = [_decode_title_part(p) for p in rel.split(os.sep)]
    if parts[0] == "0":
        # 主命名空间
        return "/".join(parts[1:])
    else:
        # 带命名空间：第一段是命名空间，剩余用 / 连接为页面名
        return parts[0] + ":" + "/".join(parts[1:])


def _apply_legacy_title_fallback(title: str) -> str:
    """Support older saved filenames like MediaWiki_Common.js.wikitext."""
    if ":" in title or "/" in title:
        return title
    legacy_namespaces = {
        "MediaWiki",
        "Template",
        "Category",
        "User",
        "Module",
        "File",
        "Help",
        "Project",
        "Special",
        "Talk",
        "Gadget",
        "Gadget definition",
        "模板",
        "模块",
        "分类",
        "零件",
        "零件定义",
    }
    head, sep, tail = title.partition("_")
    if sep and head in legacy_namespaces and tail:
        return f"{head}:{tail}"
    return title


def _get(params: dict) -> dict:
    url = BASE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url, headers={"X-authkey": AUTHKEY, "User-Agent": "WikiBot/1.0"}
    )
    return json.loads(opener.open(req).read())


def _post(params: dict) -> dict:
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(
        BASE,
        data=data,
        headers={
            "X-authkey": AUTHKEY,
            "User-Agent": "WikiBot/1.0",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    return json.loads(opener.open(req).read())


# ── 认证 ───────────────────────────────────────────────────


def login(username: str, password: str) -> bool:
    global _logged_in
    r = _get({"action": "query", "meta": "tokens", "type": "login", "format": "json"})
    login_token = r["query"]["tokens"]["logintoken"]
    r = _post(
        {
            "action": "login",
            "format": "json",
            "lgname": username,
            "lgpassword": password,
            "lgtoken": login_token,
        }
    )
    _logged_in = r["login"]["result"] == "Success"
    if not _logged_in:
        raise RuntimeError(
            f"登录失败: {r['login'].get('reason', r['login']['result'])}"
        )
    print(f"已登录: {r['login']['lgusername']}")
    return True


def get_csrf() -> str:
    r = _get({"action": "query", "meta": "tokens", "format": "json"})
    return r["query"]["tokens"]["csrftoken"]


# ── 读取 ───────────────────────────────────────────────────


def get_wikitext(title: str) -> str:
    """拉取页面 wikitext 源码"""
    r = _get(
        {
            "action": "query",
            "titles": title,
            "prop": "revisions",
            "rvprop": "content",
            "rvslots": "main",
            "format": "json",
        }
    )
    pages = r["query"]["pages"]
    page = next(iter(pages.values()))
    if "missing" in page:
        raise ValueError(f"页面不存在: {title}")
    return page["revisions"][0]["slots"]["main"]["*"]


def save_wikitext(title: str, content: str = None) -> str:
    """拉取页面并保存到 ./wikitext/ 下的对应本地文件，返回文件路径"""
    contentmodel = None
    if content is None:
        # 一次 API 调用同时拿内容和元信息（含 contentmodel）
        r = _get(
            {
                "action": "query",
                "titles": title,
                "prop": "revisions|info",
                "rvprop": "content",
                "rvslots": "main",
                "format": "json",
            }
        )
        pages = r["query"]["pages"]
        page = next(iter(pages.values()))
        if "missing" in page:
            raise ValueError(f"页面不存在: {title}")
        content = page["revisions"][0]["slots"]["main"]["*"]
        contentmodel = page.get("contentmodel")
    rel_path = _title_to_storage_relpath(title, contentmodel)
    path = os.path.join(WIKITEXT_DIR, rel_path)
    dir_path = os.path.dirname(path)
    os.makedirs(dir_path, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"已保存: {path}")
    return path


def download_file(title: str) -> str:
    """下载文件命名空间中的实际文件（非 wikitext 描述页），返回本地路径"""
    # 获取文件 URL
    r = _get(
        {
            "action": "query",
            "titles": title,
            "prop": "imageinfo",
            "iiprop": "url",
            "format": "json",
        }
    )
    pages = r["query"]["pages"]
    page = next(iter(pages.values()))
    if "missing" in page:
        raise ValueError(f"文件不存在: {title}")
    url = page["imageinfo"][0]["url"]
    # 确定本地路径
    rel_path = _title_to_storage_relpath(title)
    path = os.path.join(WIKITEXT_DIR, rel_path)
    dir_path = os.path.dirname(path)
    os.makedirs(dir_path, exist_ok=True)
    # 下载二进制
    req = urllib.request.Request(
        url, headers={"X-authkey": AUTHKEY, "User-Agent": "WikiBot/1.0"}
    )
    with opener.open(req) as resp:
        data = resp.read()
    with open(path, "wb") as f:
        f.write(data)
    print(f"已下载: {path} ({len(data)} bytes)")
    return path


def get_category_members(category: str, limit: int = 500) -> list[str]:
    """列出分类下所有页面标题"""
    titles = []
    params = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": f"Category:{category}",
        "cmlimit": limit,
        "format": "json",
    }
    while True:
        r = _get(params)
        titles += [m["title"] for m in r["query"]["categorymembers"]]
        if "continue" not in r:
            break
        params.update(r["continue"])
    return titles


# ── 写入 ───────────────────────────────────────────────────


def edit_page(title: str, text: str, summary: str = "", minor: bool = False,
              bot: bool = False) -> dict:
    """编辑或创建页面"""
    csrf = get_csrf()
    params = {
        "action": "edit",
        "format": "json",
        "title": title,
        "text": text,
        "summary": summary,
        "token": csrf,
    }
    if minor:
        params["minor"] = "1"
    if bot:
        params["bot"] = "1"
    r = _post(params)
    if "error" in r:
        raise RuntimeError(f"编辑失败: {r['error']['info']}")
    edit = r["edit"]
    if "nochange" in edit:
        print(f"无变化: {title}")
    else:
        print(f"编辑成功: {title} (revid={edit['newrevid']})")
    return edit


def delete_page(title: str, reason: str = "", bot: bool = False) -> dict:
    """删除页面"""
    csrf = get_csrf()
    params = {
        "action": "delete",
        "format": "json",
        "title": title,
        "token": csrf,
    }
    if reason:
        params["reason"] = reason
    if bot:
        params["bot"] = "1"
    r = _post(params)
    if "error" in r:
        raise RuntimeError(f"删除失败: {r['error']['info']}")
    print(f"已删除: {title}")
    return r


def create_redirect(from_title: str, to_title: str, summary: str = "",
                    bot: bool = False) -> dict:
    """创建重定向页面"""
    if not summary:
        summary = f"创建重定向至{to_title}"
    return edit_page(from_title, f"#REDIRECT [[{to_title}]]", summary, bot=bot)


def batch_save_wikitext(titles: list[str]) -> list[str]:
    """批量拉取并保存多个页面"""
    paths = []
    for i, title in enumerate(titles, 1):
        ns, _ = _split_namespace(title)
        is_file = _canonical_namespace(ns) in _FILES_NS
        action = "下载文件" if is_file else "拉取"
        print(f"[{i}/{len(titles)}] {action}: {title}")
        try:
            if is_file:
                paths.append(download_file(title))
            else:
                paths.append(save_wikitext(title))
        except Exception as e:
            print(f"  失败: {e}")
    return paths


def upload_file(path: str, summary: str = "") -> dict:
    """从本地页面文件上传，页面标题从相对 WIKITEXT_DIR 的路径还原"""
    abs_path = os.path.abspath(path)
    wikitext_dir = os.path.abspath(WIKITEXT_DIR)
    if abs_path.startswith(wikitext_dir + os.sep):
        rel = os.path.relpath(abs_path, wikitext_dir)
    else:
        rel = os.path.basename(abs_path)
    title = _apply_legacy_title_fallback(_storage_relpath_to_title(rel))
    with open(abs_path, "r", encoding="utf-8") as f:
        text = f.read()
    return edit_page(title, text, summary)


def batch_upload_dir(directory: str = None, summary: str = "") -> list[str]:
    """上传目录下所有 wiki 页面文件"""
    directory = directory or WIKITEXT_DIR
    files = []
    for root, _, names in os.walk(directory):
        for name in names:
            if name.endswith((".wikitext", ".js", ".css", ".lua", ".json")):
                files.append(os.path.join(root, name))
    if not files:
        print(f"目录下没有可上传的页面文件: {directory}")
        return []
    ok = []
    for i, path in enumerate(files, 1):
        rel_path = os.path.relpath(path, directory)
        print(f"[{i}/{len(files)}] 上传: {rel_path}")
        try:
            upload_file(path, summary)
            ok.append(path)
        except Exception as e:
            print(f"  失败: {e}")
    return ok


# ── 文件上传（File 命名空间，二进制） ─────────────────────────


def upload_image(path: str, summary: str = "", bot: bool = False) -> dict:
    """上传本地图片到 wiki（File 命名空间，二进制 multipart）。

    Args:
        path: 本地图片文件路径
        summary: 上传摘要
        bot: 标记为机器人编辑
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"文件不存在: {path}")
    filename = os.path.basename(path)
    csrf = get_csrf()
    boundary = "----WikiBotBoundary"
    body = io.BytesIO()

    def add_field(name, value):
        body.write(f"--{boundary}\r\n".encode())
        body.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.write(f"{value}\r\n".encode())

    add_field("action", "upload")
    add_field("format", "json")
    add_field("token", csrf)
    add_field("filename", filename)
    add_field("ignorewarnings", "1")
    if bot:
        add_field("bot", "1")
    if summary:
        add_field("comment", summary)

    with open(path, "rb") as f:
        file_data = f.read()
    body.write(f"--{boundary}\r\n".encode())
    body.write(
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode()
    )
    body.write(b"Content-Type: application/octet-stream\r\n\r\n")
    body.write(file_data)
    body.write(b"\r\n")
    body.write(f"--{boundary}--\r\n".encode())

    req = urllib.request.Request(
        BASE,
        data=body.getvalue(),
        headers={
            "X-authkey": AUTHKEY,
            "User-Agent": "WikiBot/1.0",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    r = json.loads(opener.open(req).read())
    if "error" in r:
        raise RuntimeError(f"上传失败: {r['error']['info']}")
    result = r["upload"]
    print(f"上传成功: File:{result['filename']} ({len(file_data)} bytes)")
    return result


def batch_upload_images(directory: str, summary: str = "",
                        limit: int = None) -> list[str]:
    """批量上传目录下的所有图片文件。

    Args:
        directory: 图片目录路径
        summary: 上传摘要
        limit: 最多上传数量，None 为全部
    """
    exts = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".ico"}
    files = sorted(
        f for f in os.listdir(directory)
        if os.path.splitext(f)[1].lower() in exts
    )
    if not files:
        print(f"目录下没有图片: {directory}")
        return []
    if limit:
        files = files[:limit]
    ok = []
    for i, name in enumerate(files, 1):
        path = os.path.join(directory, name)
        print(f"[{i}/{len(files)}] 上传图片: {name}")
        try:
            upload_image(path, summary)
            ok.append(path)
        except Exception as e:
            print(f"  失败: {e}")
    return ok


# ── 命令行 ─────────────────────────────────────────────────


def _cmd_pull(args):
    """pull <页面1> [页面2 ...]  |  pull -f <文件1> [文件2 ...]  |  pull -c <分类>  |  pull -d [目录]"""
    import argparse

    p = argparse.ArgumentParser(prog="wiki.py pull")
    p.add_argument(
        "items",
        nargs="*",
        help="页面标题；配合 -f 则为本地文件路径；配合 -d 则为目录路径",
    )
    p.add_argument("-c", "--category", help="拉取整个分类")
    p.add_argument(
        "-f",
        "--file",
        action="store_true",
        help="将参数视为本地文件路径（而非页面标题），更新到最新",
    )
    p.add_argument(
        "-d",
        "--dir",
        action="store_true",
        help="拉取目录下所有 wiki 文件（与 push -d 对称）",
    )
    p.add_argument("-u", "--user", default=USERNAME)
    p.add_argument("-p", "--password", default=PASSWORD)
    ns = p.parse_args(args)
    login(ns.user, ns.password)
    titles = []

    if ns.category:
        titles = get_category_members(ns.category)
        print(f"分类「{ns.category}」共 {len(titles)} 个页面")
    elif ns.dir:
        directory = ns.items[0] if ns.items else WIKITEXT_DIR
        files = []
        for root, _, names in os.walk(directory):
            for name in names:
                if name.endswith((".wikitext", ".js", ".css", ".lua", ".json")):
                    files.append(os.path.join(root, name))
        if not files:
            print(f"目录下没有 wiki 文件: {directory}")
            return
        for fp in files:
            rel = os.path.relpath(fp, os.path.abspath(WIKITEXT_DIR))
            titles.append(_apply_legacy_title_fallback(_storage_relpath_to_title(rel)))
    elif ns.file:
        for filepath in ns.items:
            abs_path = os.path.abspath(filepath)
            wikitext_dir = os.path.abspath(WIKITEXT_DIR)
            if abs_path.startswith(wikitext_dir + os.sep):
                rel = os.path.relpath(abs_path, wikitext_dir)
            else:
                rel = os.path.basename(abs_path)
            titles.append(_apply_legacy_title_fallback(_storage_relpath_to_title(rel)))
    else:
        titles = ns.items

    if not titles:
        p.print_help()
        return
    batch_save_wikitext(titles)


def _cmd_push(args):
    """push <页面1> [页面2 ...] [-s 摘要]  或  push -f <文件1> [文件2 ...]  或  push -d [目录]"""
    import argparse

    p = argparse.ArgumentParser(prog="wiki.py push")
    p.add_argument(
        "path",
        nargs="*",
        default=None,
        help="页面标题（默认）；配合 -f 则为文件路径；与 -d 配合则为目录路径",
    )
    p.add_argument(
        "-f",
        "--file",
        action="store_true",
        help="将参数视为本地文件路径（而非页面标题）",
    )
    p.add_argument(
        "-d", "--dir", action="store_true", help="推送整个目录（风险操作，须显式指定）"
    )
    p.add_argument(
        "--upload", action="store_true", help="将参数视为本地图片文件，上传到 File 命名空间"
    )
    p.add_argument(
        "--uploaddir", action="store_true", help="将参数视为图片目录，批量上传到 File 命名空间"
    )
    p.add_argument("-s", "--summary", default="", help="编辑摘要")
    p.add_argument("-u", "--user", default=USERNAME)
    p.add_argument("-p", "--password", default=PASSWORD)
    ns = p.parse_args(args)
    login(ns.user, ns.password)
    if ns.uploaddir:
        directory = ns.path[0] if ns.path else "icons"
        batch_upload_images(directory, ns.summary)
    elif ns.upload:
        if not ns.path:
            print("错误：请指定至少一个图片文件路径")
            return
        for filepath in ns.path:
            print(f"上传图片: {filepath}")
            try:
                upload_image(filepath, ns.summary)
            except Exception as e:
                print(f"  失败: {e}")
    elif ns.dir:
        directory = ns.path[0] if ns.path else None
        batch_upload_dir(directory, ns.summary)
    elif ns.file:
        if not ns.path:
            print("错误：请指定至少一个文件路径")
            return
        for filepath in ns.path:
            print(f"上传: {filepath}")
            try:
                upload_file(filepath, ns.summary)
            except Exception as e:
                print(f"  失败: {e}")
    else:
        if not ns.path:
            print("错误：请指定至少一个页面标题，或使用 -d 推送目录")
            return
        for title in ns.path:
            rel_path = _title_to_storage_relpath(title)
            filepath = os.path.join(WIKITEXT_DIR, rel_path)
            print(f"上传: {title} ({rel_path})")
            try:
                upload_file(filepath, ns.summary)
            except Exception as e:
                print(f"  失败: {e}")


if __name__ == "__main__":
    import sys

    commands = {"pull": _cmd_pull, "push": _cmd_push}

    if len(sys.argv) < 2 or sys.argv[1] not in commands:
        print("用法:")
        print("  python wiki.py pull <页面1> [页面2 ...]         拉取指定页面")
        print(
            "  python wiki.py pull -f <文件1> [文件2 ...]      通过本地文件路径拉取最新内容"
        )
        print(
            "  python wiki.py pull -d [目录]                   拉取目录下所有 wiki 文件"
        )
        print("  python wiki.py pull -c <分类名>                 拉取整个分类")
        print("  python wiki.py push <页面1> [页面2 ...] [-s 摘要]  通过页面标题上传")
        print(
            "  python wiki.py push -f <文件1> [文件2 ...] [-s 摘要]  通过本地路径上传页面"
        )
        print(
            "  python wiki.py push -d [目录] [-s 摘要]            上传目录下所有页面文件"
        )
        print(
            "  python wiki.py push --upload <图片1> [图片2 ...] [-s 摘要]  上传图片到 File 命名空间"
        )
        print(
            "  python wiki.py push --uploaddir [目录] [-s 摘要]       批量上传目录下所有图片"
        )
        sys.exit(0)

    commands[sys.argv[1]](sys.argv[2:])
