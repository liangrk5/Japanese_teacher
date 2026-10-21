"""命令行入口：``nihongo serve`` 启动网页，``nihongo doctor`` 检查模型连通性。"""

from __future__ import annotations

import argparse
import sys
import webbrowser

from .config import config_home, data_home, load_settings
from .llm import LLMClient, LLMError


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .server import create_app

    settings = load_settings()
    if not settings.configured:
        print(
            "提示：模型尚未配置，假名和单词复习可以正常使用，AI 功能需要先配置凭据。\n"
            f"  参考 credentials.example.env 写入 {config_home() / 'credentials.env'}",
            file=sys.stderr,
        )
    url = f"http://127.0.0.1:{args.port}/"
    print(f"日语学习助手已启动：{url}")
    if args.open:
        webbrowser.open(url)
    uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, log_level="warning")
    return 0


def cmd_app(args: argparse.Namespace) -> int:
    try:
        from .desktop import run
    except ImportError:
        print("桌面窗口需要 pywebview（macOS 上 uv sync 会自动安装）", file=sys.stderr)
        return 1
    return run(args.port)


def cmd_doctor(_: argparse.Namespace) -> int:
    settings = load_settings()
    print(f"配置目录：{config_home()}")
    print(f"数据目录：{data_home()}")
    print(f"网关：{settings.base_url or '（未设置 NIHONGO_TEXT_BASE_URL）'}")
    print(f"密钥：{'已设置' if settings.api_key else '（未设置 NIHONGO_TEXT_API_KEY）'}")
    print(f"调用方：{settings.caller or '（未设置 NIHONGO_TEXT_CALLER）'}")
    for role, model in settings.models.items():
        print(f"模型 {role:<6} {model}")
    if not settings.configured:
        return 1
    llm = LLMClient(settings)
    ok = True
    for role in sorted(set(settings.models)):
        try:
            reply = llm.chat([{"role": "user", "content": "こんにちは"}], role=role, max_tokens=30)
            print(f"[通过] {role}: {reply.strip()[:40]}")
        except LLMError as exc:
            ok = False
            print(f"[失败] {role}: {exc}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nihongo", description="零基础日语学习助手")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="启动本地网页")
    serve.add_argument("--port", type=int, default=4567)
    serve.add_argument("--open", action="store_true", help="启动后打开浏览器")
    serve.set_defaults(func=cmd_serve)
    app = sub.add_parser("app", help="以 macOS 应用窗口打开")
    app.add_argument("--port", type=int, default=None)
    app.set_defaults(func=cmd_app)
    doctor = sub.add_parser("doctor", help="检查配置和模型连通性")
    doctor.set_defaults(func=cmd_doctor)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
