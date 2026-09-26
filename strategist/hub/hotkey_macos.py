"""全域快捷鍵（macOS Carbon RegisterEventHotKey，經 ctypes；Randy 自有模組）。

x-hub 用 tauri-plugin-global-shortcut；這裡不引入新相依，直接用 Carbon 的熱鍵 API：
不需要「輔助使用」權限（NSEvent 全域監聽才需要），失敗時只記 log、不影響 App。
"""
from __future__ import annotations

import ctypes
import logging

log = logging.getLogger("randy_hub")

CMD, SHIFT, OPTION, CONTROL = 1 << 8, 1 << 9, 1 << 11, 1 << 12
KEY_SPACE = 49
_EVENT_CLASS_KEYBOARD = int.from_bytes(b"keyb", "big")
_EVENT_HOTKEY_PRESSED = 5
_SIGNATURE = int.from_bytes(b"RHub", "big")


class _EventTypeSpec(ctypes.Structure):
    _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]


class _EventHotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
_keepalive: list = []  # ctypes 回呼與 ref 必須活著，否則會被回收導致當機


def register(callback, key_code: int = KEY_SPACE, modifiers: int = CONTROL | SHIFT) -> bool:
    """註冊一組全域熱鍵；callback 在主執行緒（Cocoa run loop）被呼叫。"""
    try:
        carbon = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
        carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
        carbon.InstallEventHandler.argtypes = [ctypes.c_void_p, _HANDLER, ctypes.c_ulong,
                                               ctypes.POINTER(_EventTypeSpec), ctypes.c_void_p, ctypes.c_void_p]
        carbon.RegisterEventHotKey.argtypes = [ctypes.c_uint32, ctypes.c_uint32, _EventHotKeyID, ctypes.c_void_p,
                                               ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]

        def _on_hotkey(_call_ref, _event, _user_data):
            try:
                callback()
            except Exception:  # noqa: BLE001 — 回呼裡不能讓例外穿回 C
                log.exception("熱鍵回呼失敗")
            return 0

        handler = _HANDLER(_on_hotkey)
        spec = _EventTypeSpec(_EVENT_CLASS_KEYBOARD, _EVENT_HOTKEY_PRESSED)
        target = carbon.GetApplicationEventTarget()
        status = carbon.InstallEventHandler(target, handler, 1, ctypes.byref(spec), None, None)
        ref = ctypes.c_void_p()
        status2 = carbon.RegisterEventHotKey(key_code, modifiers, _EventHotKeyID(_SIGNATURE, 1), target, 0, ctypes.byref(ref))
        _keepalive.extend([carbon, handler, spec, ref])
        ok = status == 0 and status2 == 0
        log.info("全域快捷鍵註冊%s（status=%s/%s）", "成功" if ok else "失敗", status, status2)
        return ok
    except (OSError, AttributeError) as exc:
        log.warning("全域快捷鍵不可用：%s", exc)
        return False
