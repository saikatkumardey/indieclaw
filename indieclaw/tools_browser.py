from __future__ import annotations

from claude_agent_sdk import tool


def _text(t: str) -> dict:
    return {"content": [{"type": "text", "text": t}]}


async def _get_page(chat_id: str):
    from .browser import BrowserManager
    return await BrowserManager.get().get_page(chat_id)


@tool(
    "browse",
    "Navigate to a URL in a headless browser. Renders JavaScript. "
    "Returns page title and visible text content. Use for JS-heavy pages that WebFetch can't read. "
    "Creates a persistent browser session per chat_id — subsequent browser_* calls reuse it.",
    {"chat_id": str, "url": str},
)
async def browse(args: dict) -> dict:
    from .browser import navigate_page
    try:
        page = await _get_page(str(args["chat_id"]))
        result = await navigate_page(page, str(args["url"]))
        return _text(f"Title: {result['title']}\nURL: {result['url']}\n\n{result['text']}")
    except Exception as e:
        return _text(f"Browser error: {e}")


@tool(
    "browser_click",
    "Click an element on the current browser page by CSS selector.",
    {"chat_id": str, "selector": str},
)
async def browser_click(args: dict) -> dict:
    from .browser import click_element
    try:
        page = await _get_page(str(args["chat_id"]))
        result = await click_element(page, str(args["selector"]))
        return _text(result)
    except Exception as e:
        return _text(f"Browser error: {e}")


@tool(
    "browser_type",
    "Type text into a form field by CSS selector. Clears existing content first.",
    {"chat_id": str, "selector": str, "text": str},
)
async def browser_type(args: dict) -> dict:
    from .browser import type_into
    try:
        page = await _get_page(str(args["chat_id"]))
        result = await type_into(page, str(args["selector"]), str(args["text"]))
        return _text(result)
    except Exception as e:
        return _text(f"Browser error: {e}")


@tool(
    "browser_screenshot",
    "Take a screenshot of the current browser page. Returns the file path. "
    "Use Read tool to view the image or telegram_send_file to send it.",
    {"chat_id": str},
)
async def browser_screenshot(args: dict) -> dict:
    from .browser import take_screenshot
    try:
        page = await _get_page(str(args["chat_id"]))
        path = await take_screenshot(page, str(args["chat_id"]))
        return _text(f"Screenshot saved: {path}")
    except Exception as e:
        return _text(f"Browser error: {e}")


@tool(
    "browser_eval",
    "Execute JavaScript on the current browser page and return the result.",
    {"chat_id": str, "javascript": str},
)
async def browser_eval(args: dict) -> dict:
    from .browser import evaluate_js
    try:
        page = await _get_page(str(args["chat_id"]))
        result = await evaluate_js(page, str(args["javascript"]))
        return _text(result)
    except Exception as e:
        return _text(f"Browser error: {e}")
