"""Request-local translations for server-rendered pages and browser messages."""

import json
import math
from functools import partial
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

DEFAULT_LOCALE = "zh-CN"
LOCALES = {"zh-CN": "简体中文", "ja": "日本語", "en": "English"}
LOCALE_COOKIE = "gev_language"
MESSAGES = json.loads(
    (Path(__file__).parent / "locales" / "messages.json").read_text(encoding="utf-8")
)
CLIENT_MESSAGES = {
    "branches_failed": "分支加载失败。权限可能已变更，或服务暂不可用。",
    "retry": "重试",
    "job_succeeded": "分析已完成，正在刷新…",
    "job_pending": "请求已保存，等待队列恢复。",
    "job_finished": "任务已结束，请刷新或联系管理员查看失败记录。",
    "job_running": "后台分析中…",
    "job_unavailable": "任务状态暂不可用，请刷新后重试。",
    "log_loading": "正在读取本地提交历史…",
    "log_failed": "提交历史加载失败。请刷新或重试。",
    "log_hint": "选择分支筛选历史，选择提交查看详情。",
    "hash_copied": "已复制提交哈希。",
    "copy_failed": "无法复制，请手动复制提交哈希。",
}


class Message(str):
    """Keep interpolation parameters available until the response is localized."""

    def __new__(cls, source: str, **values):
        message = super().__new__(cls, source.format(**values))
        message.source = source
        message.values = values
        return message


def normalize_locale(value: str | None) -> str | None:
    if not value or len(value) > 35:
        return None
    language = value.strip().lower().replace("_", "-")
    parts = language.split("-")
    if not all(part.isascii() and part.isalnum() for part in parts):
        return None
    return {"zh": "zh-CN", "ja": "ja", "en": "en"}.get(parts[0])


def select_locale(request: Request) -> str:
    explicit = normalize_locale(request.query_params.get("lang"))
    saved = normalize_locale(request.cookies.get(LOCALE_COOKIE))
    if explicit or saved:
        return explicit or saved
    preferred = []
    for index, entry in enumerate(request.headers.get("accept-language", "").split(",")):
        language, *parameters = entry.strip().split(";")
        quality = 1.0
        try:
            for parameter in parameters:
                name, separator, value = parameter.strip().partition("=")
                if name.lower() == "q" and separator:
                    quality = float(value)
        except ValueError:
            continue
        locale = DEFAULT_LOCALE if language == "*" else normalize_locale(language)
        if locale and math.isfinite(quality) and 0 < quality <= 1:
            preferred.append((-quality, index, locale))
    return min(preferred)[2] if preferred else DEFAULT_LOCALE


def translate(locale: str, message: str, **values) -> str:
    if isinstance(message, Message):
        values = {**message.values, **values}
        message = message.source
    values = {
        key: translate(locale, value) if isinstance(value, Message) else value
        for key, value in values.items()
    }
    text = MESSAGES.get(message, {}).get(locale, message)
    return text.format(**values) if values else text


def template_context(request: Request) -> dict:
    locale = getattr(request.state, "locale", None) or select_locale(request)
    translator = partial(translate, locale)
    return {
        "locale": locale,
        "locales": LOCALES,
        "t": translator,
        "client_messages": {
            key: translator(value) for key, value in CLIENT_MESSAGES.items()
        },
    }


def create_templates() -> Jinja2Templates:
    return Jinja2Templates(
        directory=str(Path(__file__).parent / "templates"),
        context_processors=[template_context],
    )
