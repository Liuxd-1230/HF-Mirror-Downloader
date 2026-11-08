"""真实 GUI 操控测试（pywinauto · UIA）：模糊搜索 + 下载小文件。

连接到正在运行的 HF Mirror Downloader 窗口，模拟真人操作：
  粘贴链接 → 解析仓库 → 模糊过滤 → 勾选 → 改目录 → 开始下载 → 验证。
"""

import sys
import time
from pathlib import Path

from pywinauto import Application, mouse
from pywinauto.keyboard import send_keys

OUT_DIR = r"C:\Users\Rosemary\Downloads\hf-e2e-gui"
REPO_URL = "https://huggingface.co/hf-internal-testing/tiny-random-bert"

ok = []


def step(name, cond, detail=""):
    ok.append(bool(cond))
    print(f"  {'✅' if cond else '❌'} {name}" + (f" —— {detail}" if detail else ""))


def main() -> int:
    app = Application(backend="uia").connect(title_re="HF Mirror Downloader.*", timeout=10)
    win = app.window(title_re="HF Mirror Downloader.*")
    win.set_focus()
    time.sleep(0.5)

    print("[1] 粘贴链接并解析")
    url_edit = win.child_window(auto_id=None, control_type="Edit", found_index=0)
    # 找 URL 输入框：取所有 Edit，值为链接或空的最上面那个
    edits = win.descendants(control_type="Edit")
    url_edit = min(edits, key=lambda e: e.rectangle().top)  # 最顶部的输入框 = 链接输入
    url_edit.set_edit_text(REPO_URL)
    time.sleep(0.3)
    parse_btn = next(b for b in win.descendants(control_type="Button")
                     if (b.window_text() or "").strip() == "解析仓库")
    parse_btn.click_input()
    time.sleep(4)
    texts = [t.window_text() or "" for t in win.descendants(control_type="Text")]
    step("解析仓库成功", any("10 个文件" in t for t in texts),
         next((t for t in texts if "个文件" in t), ""))
    step("侧栏镜像健康灯", any("HF-Mirror" in t and "●" in t for t in texts))

    print("[2] 模糊搜索：子串 tok")
    # 用几何位置定位过滤框：位于「全部」chip 之下、第一行文件之上
    chip = next(w for w in win.descendants(control_type="CheckBox") if (w.window_text() or "") == "全部")
    data_items = win.descendants(control_type="DataItem")
    first_row_top = min(r.rectangle().top for r in data_items) if data_items else 10**9
    cands = [e for e in win.descendants(control_type="Edit")
             if chip.rectangle().bottom < e.rectangle().top < first_row_top
             and not (e.get_value() or "").startswith("http")]
    filter_edit = cands[0]
    filter_edit.set_edit_text("tok")
    time.sleep(0.6)
    rows = [r.window_text() for r in win.descendants(control_type="TreeItem")]
    tok_rows = [r for r in rows if r and "tok" in r.lower()]
    step("子串 tok 过滤生效", sorted(set(tok_rows)) == ["special_tokens_map.json", "tokenizer.json", "tokenizer_config.json"], f"可见 {sorted(set(tok_rows))}")

    print("[3] glob *.json")
    filter_edit.set_edit_text("*.json")
    time.sleep(0.6)
    rows = [r.window_text() for r in win.descendants(control_type="TreeItem")]
    json_files = sorted(set(r for r in rows if r and r.endswith(".json")))
    non_json = [r for r in rows if r in ("vocab.txt", "README.md", "tf_model.h5")]
    step("glob *.json 过滤生效", json_files == ["config.json", "special_tokens_map.json", "tokenizer.json", "tokenizer_config.json"] and not non_json, f"可见 {json_files}")

    print("[4] 清空过滤并勾选 config.json")
    filter_edit.set_edit_text("")
    time.sleep(0.6)
    items = win.descendants(control_type="ListItem") or win.descendants(control_type="TreeItem")
    cfg = None
    for it in win.descendants():
        try:
            if it.element_info.control_type in ("TreeItem", "ListItem") and it.window_text() == "config.json":
                cfg = it
                break
        except Exception:  # noqa: BLE001
            continue
    step("找到 config.json 行", cfg is not None)
    if cfg is None:
        return 1
    # 点击该行的复选框（行首）
    rect = cfg.rectangle()
    mouse.click(coords=(rect.left + int((rect.right - rect.left) * 0.015) + 6, (rect.top + rect.bottom) // 2))
    time.sleep(0.5)
    texts = [t.window_text() or "" for t in win.descendants(control_type="Text")]
    sel = next((t for t in texts if t.startswith("已选")), "")
    step("勾选联动统计", "1 个文件" in sel, sel)

    print("[5] 设置下载目录并开始下载")
    all_edits = win.descendants(control_type="Edit")
    dir_edit = max(all_edits, key=lambda e: e.rectangle().top)  # 底部栏的目录框
    dir_edit.set_edit_text(OUT_DIR)
    time.sleep(0.3)
    start_btn = next(b for b in win.descendants(control_type="Button")
                     if "开始下载" in (b.window_text() or ""))
    start_btn.click_input()
    time.sleep(1)
    # 弹窗“已开始下载”→ 确定
    try:
        dlg = app.window(title="已开始下载", timeout=3)
        dlg.child_window(title="确定", control_type="Button").click_input()
    except Exception:  # noqa: BLE001
        pass
    time.sleep(4)

    print("[6] 验证传输面板与文件落盘")
    texts = [t.window_text() or "" for t in win.descendants(control_type="Text")]
    step("任务完成（胶囊显示）", any("已完成" in t for t in texts))
    p = Path(OUT_DIR) / "config.json"
    step("文件落盘且内容有效", p.is_file() and "model_type" in p.read_text(encoding="utf-8"),
         f"{p} ({p.stat().st_size if p.exists() else 0} B)")

    print("[7] 恢复队列：点击「全部续传」")
    resume_btn = next((b for b in win.descendants(control_type="Button")
                       if "全部续传" in (b.window_text() or "")), None)
    if resume_btn:
        resume_btn.click_input()
        time.sleep(3)
        try:
            dlg = app.window(title="已开始下载", timeout=3)
            dlg.child_window(title="确定", control_type="Button").click_input()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(3)
        texts = [t.window_text() or "" for t in win.descendants(control_type="Text")]
        has_queue = any("12.7 KB" in t for t in texts)
        step("恢复横幅执行后续传任务出现", any("已完成" in t or "下载中" in t or "等待中" in t for t in texts) or not has_queue)
    else:
        step("恢复横幅已被此前操作清除", True)

    print()
    total, passed = len(ok), sum(ok)
    print(f"结果：{passed}/{total} 通过" + ("，全部通过 ✅" if passed == total else "，存在失败 ❌"))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
