"""原生 macOS 桌面殼：NSWindow + WKWebView + 選單列圖示 + 全域快捷鍵（Randy 自有模組）。

x-hub 的 Windows 殼是 Tauri/wry + WebView2 + 自製標題列 + Windows 系統匣；這裡換成：
  - WKWebView：用 pyobjc 的 objc.loadBundle 動態載入系統 WebKit.framework（不需另裝 pyobjc-framework-WebKit）
  - 標準 macOS 標題列 / 選單（含 Edit 選單，讓 ⌘C/⌘V 在網頁輸入框可用）、視窗位置自動記憶
  - NSStatusItem 當「系統匣」；Carbon 熱鍵 ⌃⇧Space 顯示 / 隱藏
刻意不碰既有 Menu Bar（run_menubar.py）與它的前景化問題：這是獨立行程、獨立圖示。
"""
from __future__ import annotations

import logging

from . import hotkey_macos

log = logging.getLogger("randy_hub")
_WEBKIT_PATH = "/System/Library/Frameworks/WebKit.framework"


def webkit_available() -> bool:
    try:
        import objc  # noqa: F401
        from AppKit import NSApplication  # noqa: F401

        objc.loadBundle("WebKit", {}, bundle_path=_WEBKIT_PATH)
        objc.lookUpClass("WKWebView")
        return True
    except Exception:  # noqa: BLE001 — 只是探測能不能用
        return False


def _menu_item(title: str, action: str | None, key: str = "", target=None):
    from AppKit import NSMenuItem

    item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
    if target is not None:
        item.setTarget_(target)
    return item


def _submenu(title: str, items: list):
    from AppKit import NSMenu, NSMenuItem

    menu = NSMenu.alloc().initWithTitle_(title)
    for item in items:
        menu.addItem_(item if item is not None else NSMenuItem.separatorItem())
    holder = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
    holder.setSubmenu_(menu)
    return holder


def _main_menu(delegate):
    from AppKit import NSMenu

    bar = NSMenu.alloc().init()
    bar.addItem_(_submenu("Randy Hub", [
        _menu_item("顯示 Randy Hub", "show:", "", delegate), None,
        _menu_item("隱藏", "hide:", "h"), _menu_item("結束 Randy Hub", "terminate:", "q")]))
    bar.addItem_(_submenu("編輯", [
        _menu_item("還原", "undo:", "z"), _menu_item("重做", "redo:", "Z"), None,
        _menu_item("剪下", "cut:", "x"), _menu_item("拷貝", "copy:", "c"), _menu_item("貼上", "paste:", "v"),
        _menu_item("全選", "selectAll:", "a")]))
    bar.addItem_(_submenu("顯示方式", [_menu_item("重新載入", "reloadPage:", "r", delegate)]))
    bar.addItem_(_submenu("視窗", [_menu_item("縮到最小", "performMiniaturize:", "m"), _menu_item("關閉", "performClose:", "w")]))
    return bar


def _delegate_class(server, tray: bool):
    from AppKit import NSApplication
    from Foundation import NSObject

    class HubAppDelegate(NSObject):
        def applicationShouldTerminateAfterLastWindowClosed_(self, _app):
            return not tray

        def applicationShouldHandleReopen_hasVisibleWindows_(self, _app, _visible):
            self.show_(None)
            return True

        def applicationWillTerminate_(self, _note):
            server.shutdown()

        def show_(self, _sender):
            self.window.makeKeyAndOrderFront_(None)
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

        def reloadPage_(self, _sender):
            self.webview.reload_(None)

        def toggle(self):
            if self.window.isVisible() and self.window.isKeyWindow():
                self.window.orderOut_(None)
            else:
                self.show_(None)

        def tick_(self, _timer):
            """空轉：讓 Python 直譯器定期取得控制權，才能處理 SIGTERM / Ctrl+C。"""

    return HubAppDelegate


def _make_window(url: str, inspectable: bool):
    import objc
    from AppKit import NSBackingStoreBuffered, NSMakeRect, NSWindow
    from Foundation import NSURL, NSURLRequest

    objc.loadBundle("WebKit", {}, bundle_path=_WEBKIT_PATH)
    wk_view, wk_config = objc.lookUpClass("WKWebView"), objc.lookUpClass("WKWebViewConfiguration")
    style = 1 | 2 | 4 | 8  # titled | closable | miniaturizable | resizable
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(NSMakeRect(0, 0, 1360, 880), style, NSBackingStoreBuffered, False)
    window.setTitle_("Randy Hub")
    window.setReleasedWhenClosed_(False)
    window.center()
    window.setFrameAutosaveName_("RandyHubMainWindow")  # 記憶視窗位置與大小
    webview = wk_view.alloc().initWithFrame_configuration_(window.contentView().bounds(), wk_config.alloc().init())
    webview.setAutoresizingMask_(2 | 16)  # 寬高隨視窗
    if inspectable and webview.respondsToSelector_("setInspectable:"):
        webview.setInspectable_(True)
    window.contentView().addSubview_(webview)
    webview.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(url)))
    return window, webview


def _make_status_item(delegate):
    from AppKit import NSMenu, NSStatusBar, NSVariableStatusItemLength

    status = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
    status.button().setTitle_("◆ Hub")
    menu = NSMenu.alloc().init()
    for item in (_menu_item("顯示 Randy Hub", "show:", "", delegate), _menu_item("重新載入", "reloadPage:", "", delegate),
                 _menu_item("結束 Randy Hub", "terminate:", "")):
        menu.addItem_(item)
    status.setMenu_(menu)
    return status


def run_native(server, *, tray: bool = True, hotkey: bool = True, inspectable: bool = False) -> None:
    from AppKit import NSApplication, NSApplicationActivationPolicyRegular

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    delegate = _delegate_class(server, tray).alloc().init()
    delegate.window, delegate.webview = _make_window(server.ui_url, inspectable)
    app.setDelegate_(delegate)
    app.setMainMenu_(_main_menu(delegate))
    if tray:
        delegate.status_item = _make_status_item(delegate)
    if hotkey:
        hotkey_macos.register(delegate.toggle)
    _install_graceful_exit(app, delegate)
    delegate.window.makeKeyAndOrderFront_(None)
    app.activateIgnoringOtherApps_(True)
    log.info("原生視窗已開啟（tray=%s, hotkey=%s）", tray, hotkey)
    app.run()


def _install_graceful_exit(app, delegate) -> None:
    """SIGTERM / Ctrl+C → NSApp terminate: → applicationWillTerminate_ → 停 service、關伺服器、清 run 檔。"""
    import signal

    from Foundation import NSTimer

    def _terminate(*_args):
        log.info("收到結束訊號，優雅關閉")
        app.terminate_(None)

    signal.signal(signal.SIGTERM, _terminate)
    signal.signal(signal.SIGINT, _terminate)
    delegate.signal_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        0.5, delegate, "tick:", None, True)
