from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
from datetime import datetime, timezone

from loguru import logger

from . import workspace

SCREENSHOTS_DIR = workspace.HOME / "screenshots"

_IDLE_TIMEOUT = 600
_LP_HOST = "127.0.0.1"
_LP_PORT = 9222


class BrowserManager:

    _instance: BrowserManager | None = None

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._lp_process: subprocess.Popen | None = None  # Lightpanda subprocess
        self._using_lightpanda = False
        self._contexts: dict[str, object] = {}  # chat_id -> BrowserContext
        self._pages: dict[str, object] = {}  # chat_id -> Page
        self._last_used: dict[str, float] = {}  # chat_id -> timestamp
        self._lock = asyncio.Lock()

    @classmethod
    def get(cls) -> BrowserManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def _cleanup_lightpanda(self) -> None:
        if self._lp_process and self._lp_process.poll() is None:
            self._lp_process.terminate()
        self._lp_process = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                logger.debug("playwright stop failed during cleanup", exc_info=True)
            self._playwright = None
        self._browser = None

    async def _start_lightpanda(self) -> bool:
        lp_bin = shutil.which("lightpanda")
        if not lp_bin:
            return False

        try:
            self._lp_process = subprocess.Popen(
                [lp_bin, "serve", "--host", _LP_HOST, "--port", str(_LP_PORT)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            await asyncio.sleep(0.5)

            if self._lp_process.poll() is not None:
                stderr = self._lp_process.stderr.read().decode()[:200] if self._lp_process.stderr else ""
                logger.warning("Lightpanda exited immediately: {}", stderr)
                self._lp_process = None
                return False

            from playwright.async_api import async_playwright
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.connect_over_cdp(
                f"http://{_LP_HOST}:{_LP_PORT}",
            )
            self._using_lightpanda = True
            logger.info("Browser connected (Lightpanda via CDP)")
            return True
        except Exception as e:
            logger.warning("Lightpanda connection failed, falling back to Chromium: {}", e)
            await self._cleanup_lightpanda()
            return False

    async def _ensure_browser(self):
        if self._browser is not None:
            return

        if await self._start_lightpanda():
            SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
            return

        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
        )
        self._using_lightpanda = False
        SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        logger.info("Browser launched (headless Chromium)")

    @property
    def backend(self) -> str:
        if self._browser is None:
            return "none"
        return "lightpanda" if self._using_lightpanda else "chromium"

    async def get_page(self, chat_id: str):
        async with self._lock:
            await self._ensure_browser()

            if chat_id in self._pages:
                self._last_used[chat_id] = time.monotonic()
                return self._pages[chat_id]

            if self._using_lightpanda:
                context = await self._browser.new_context()
            else:
                context = await self._browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    user_agent=(
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    ),
                )
            try:
                page = await context.new_page()
            except Exception:
                await context.close()
                raise
            self._contexts[chat_id] = context
            self._pages[chat_id] = page
            self._last_used[chat_id] = time.monotonic()
            return page

    async def close_session(self, chat_id: str) -> None:
        async with self._lock:
            await self._close_session_unlocked(chat_id)

    async def _close_session_unlocked(self, chat_id: str) -> None:
        self._pages.pop(chat_id, None)
        self._last_used.pop(chat_id, None)
        if ctx := self._contexts.pop(chat_id, None):
            try:
                await ctx.close()
            except Exception:
                logger.debug("browser context close failed", exc_info=True)

    async def close_all(self) -> None:
        async with self._lock:
            for chat_id in list(self._contexts.keys()):
                await self._close_session_unlocked(chat_id)
            if self._browser:
                try:
                    await self._browser.close()
                except Exception:
                    logger.debug("browser close failed", exc_info=True)
                self._browser = None
            if self._playwright:
                try:
                    await self._playwright.stop()
                except Exception:
                    logger.debug("playwright stop failed", exc_info=True)
                self._playwright = None
            if self._lp_process and self._lp_process.poll() is None:
                self._lp_process.terminate()
                self._lp_process = None

    async def cleanup_idle(self) -> None:
        async with self._lock:
            now = time.monotonic()
            stale = [
                cid for cid, ts in self._last_used.items()
                if now - ts > _IDLE_TIMEOUT
            ]
            for cid in stale:
                logger.info("Closing idle browser context for {}", cid)
                await self._close_session_unlocked(cid)

async def navigate_page(page, url: str, timeout_ms: int = 30000) -> dict:
    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    await page.wait_for_timeout(1000)
    title = await page.title()
    url_final = page.url
    text = await page.inner_text("body")
    return {"title": title, "url": url_final, "text": text.strip()[:4000]}


async def take_screenshot(page, chat_id: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = SCREENSHOTS_DIR / f"{chat_id}_{ts}.png"
    await page.screenshot(path=str(path), full_page=False)
    return str(path)


async def click_element(page, selector: str, timeout_ms: int = 5000) -> str:
    await page.click(selector, timeout=timeout_ms)
    await page.wait_for_timeout(500)
    return f"Clicked '{selector}'"


async def type_into(page, selector: str, text: str, timeout_ms: int = 5000) -> str:
    await page.fill(selector, text, timeout=timeout_ms)
    return f"Typed into '{selector}'"


async def evaluate_js(page, js: str) -> str:
    result = await page.evaluate(js)
    return str(result)[:4000] if result is not None else "undefined"
