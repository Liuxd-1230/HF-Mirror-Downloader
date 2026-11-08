"""验证两项修复：①最大化/还原按钮 ②搜索窗为独立顶层窗口（不叠字）。"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pywinauto import Application
from pywinauto.findwindows import find_elements


def connect_main():
    elems = find_elements(title_re="HF Mirror Downloader.*")
    print("匹配元素:", [(e.process_id, e.rectangle.width(), e.rectangle.height(), e.name) for e in elems])
    big = max(elems, key=lambda e: e.rectangle.width() * e.rectangle.height())
    app = Application(backend="uia").connect(process=big.process_id, timeout=10)
    win = app.window(handle=big.handle)
    win.set_focus()
    time.sleep(0.5)
    return app, win


def titlebar_buttons(win, top):
    return sorted(
        [b for b in win.descendants(control_type="Button")
         if not (b.window_text() or "").strip() and b.rectangle().top < top + 60],
        key=lambda b: b.rectangle().left,
    )


def test_maximize(app, win):
    print("[1] 最大化 / 还原按钮")
    before = win.rectangle()
    btns = titlebar_buttons(win, before.top)
    print("标题栏按钮数:", len(btns))
    if len(btns) < 2:
        print("  ❌ 未找到标题栏按钮")
        return False
    btns[1].click_input()
    time.sleep(1.2)
    after = win.rectangle()
    ok_max = after.width() > before.width() + 200
    print(f"  最大化: {before.width()}x{before.height()} → {after.width()}x{after.height()}",
          "✅" if ok_max else "❌")
    btns2 = titlebar_buttons(win, after.top)
    ok_restore = False
    if len(btns2) >= 2:
        btns2[1].click_input()
        time.sleep(1.0)
        back = win.rectangle()
        ok_restore = abs(back.width() - before.width()) < 60
        print(f"  还原: {back.width()}x{back.height()}", "✅" if ok_restore else "❌")
    else:
        print("  ❌ 还原按钮未找到")
    return ok_max and ok_restore


def test_search_window(app, win):
    print("[2] 搜索窗独立顶层窗口")
    url_edit = min(win.descendants(control_type="Edit"), key=lambda e: e.rectangle().top)
    url_edit.set_edit_text("qwen")
    parse_btn = next(b for b in win.descendants(control_type="Button")
                     if (b.window_text() or "").strip() == "解析仓库")
    parse_btn.click_input()
    found = None
    for _ in range(25):
        time.sleep(0.4)
        try:
            found = [e for e in find_elements(title="模型搜索")]
            if found:
                break
        except Exception:  # noqa: BLE001
            continue
    if not found:
        titles = [w.window_text() for w in app.windows()]
        print("  ❌ 未出现独立搜索窗；顶层窗口:", titles)
        return False
    print("  ✅ 搜索窗为独立顶层窗口")
    dlg = app.window(handle=found[0].handle)
    dlg.set_focus()
    time.sleep(0.6)
    from PIL import ImageGrab
    r = dlg.rectangle()
    dpr = dlg.element_info.__dict__.get('_dpi_scale', 1) or 1
    img = ImageGrab.grab(bbox=(int(r.left), int(r.top), int(r.right), int(r.bottom)))
    out = Path(__file__).resolve().parents[1] / "build_search_dlg.png"
    img.save(out)
    print("  截图:", out.name, f"({img.width}x{img.height})")
    dlg.close()
    return True


def main() -> int:
    app, win = connect_main()
    r1 = test_maximize(app, win)
    r2 = test_search_window(app, win)
    print()
    print("结果:", "全部通过 ✅" if r1 and r2 else f"maximize={r1}, search={r2}")
    return 0 if r1 and r2 else 1


if __name__ == "__main__":
    sys.exit(main())
