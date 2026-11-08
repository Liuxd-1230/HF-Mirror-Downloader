"""真实镜像 E2E：解析仓库 → 模糊搜索过滤 → 勾选 → 完整 UI 流程下载小文件。

覆盖真实用户路径（无 Fake 服务器，直连 hf-mirror.net）：
  1. 粘贴链接 → 解析仓库（真实 API，含大小）
  2. 过滤框输入模糊子串 / glob，验证可见行收缩
  3. 勾选目标文件 → 验证「已选/磁盘/开始下载按钮」联动
  4. on_start() 走完整提交路径（磁盘检查、目录创建、批次提交）
  5. 轮询引擎至完成 → 校验文件内容 → 校验历史入库 → 校验队列文件已清空

运行：.venv/Scripts/python scripts/e2e_search_download.py
"""

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

from PySide6.QtWidgets import QApplication, QMessageBox

from app.core.download_engine import DownloadEngine
from app.core.queue_store import QueueStore
from app.models import TaskState
from app.services.paths import config_dir
from app.ui.main_window import MainWindow
from app.ui.theme import stylesheet

REPO_URL = "https://huggingface.co/hf-internal-testing/tiny-random-bert"
OUT_DIR = ROOT / "build_e2e" / "downloads"

STEPS: list[tuple[str, bool, str]] = []


def step(name: str, ok: bool, detail: str = "") -> None:
    STEPS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f" —— {detail}" if detail else ""))


def wait_until(fn, timeout=60, what="条件"):
    deadline = time.time() + timeout
    app = QApplication.instance()
    while time.time() < deadline:
        app.processEvents()
        v = fn()
        if v:
            return v
        time.sleep(0.1)
    raise TimeoutError(f"等待 {what} 超时")


def main() -> int:
    print("E2E：模糊搜索 + 下载（真实镜像 hf-mirror.net）")

    # 弹窗自动应答（GUI 模态在无头测试里无法点击）
    QMessageBox.information = lambda *a, **k: None
    QMessageBox.question = lambda *a, **k: QMessageBox.Yes

    app = QApplication([])
    w = MainWindow()
    app.setStyleSheet(stylesheet(w.settings_service.settings.theme))
    w.show()

    # 准备：临时下载目录 + 打开静默
    w.settings_service.settings.local_dir = str(OUT_DIR)
    w.download_page.dir_edit.setText(str(OUT_DIR))
    page = w.download_page
    queue_path = Path(config_dir()) / "queue.json"
    QueueStore(queue_path).clear()

    # 1. 解析仓库（真实镜像）
    print("[1] 解析仓库")
    page.input_edit.setText(REPO_URL)
    page.on_parse_clicked()
    wait_until(lambda: page.file_panel.tree.topLevelItemCount() > 0, what="文件列表")
    files_all = page.file_panel.tree.topLevelItemCount()
    sizes_known = sum(
        1 for i in range(files_all)
        if page.file_panel.tree.topLevelItem(i).data(1, 0x0100 + 1) not in (None, -1)
    )
    step("解析仓库并列出文件", files_all > 0, f"{files_all} 个文件，其中 {sizes_known} 个带大小")
    step("文件大小未缺失（三级降级生效）", sizes_known == files_all)

    # 2. 模糊搜索：子串 "tok"
    print("[2] 模糊搜索 · 子串")
    page.file_panel.filter_edit.setText("tok")
    wait_until(lambda: sum(1 for i in range(files_all) if not page.file_panel.tree.topLevelItem(i).isHidden()) in (2, 3),
               what="子串过滤生效")
    visible = [page.file_panel.tree.topLevelItem(i).text(0) for i in range(files_all)
               if not page.file_panel.tree.topLevelItem(i).isHidden()]
    step("子串 'tok' 过滤", all("tok" in v.lower() for v in visible), f"可见 {visible}")

    # 3. 模糊搜索：glob "*.json"
    page.file_panel.filter_edit.setText("*.json")
    wait_until(lambda: sum(1 for i in range(files_all) if not page.file_panel.tree.topLevelItem(i).isHidden()) == 3,
               what="glob 过滤生效")
    visible2 = [page.file_panel.tree.topLevelItem(i).text(0) for i in range(files_all)
                if not page.file_panel.tree.topLevelItem(i).isHidden()]
    step("glob '*.json' 过滤", all(v.endswith(".json") for v in visible2), f"可见 {visible2}")

    # 4. 全不选 → 勾选最小文件 config.json（约 1KB）
    print("[3] 勾选与联动")
    page.file_panel.select_none()
    target_item = next(
        page.file_panel.tree.topLevelItem(i) for i in range(files_all)
        if page.file_panel.tree.topLevelItem(i).text(0) == "config.json"
    )
    target_item.setCheckState(0, __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.Checked)
    count, total = page.file_panel.checked_stats()
    step("勾选目标文件", count == 1 and 0 < total < 4096, f"已选 {count} 个 · {total} B")
    step("开始按钮联动文案", "开始下载 · 1 个文件" in page.btn_start.text(), page.btn_start.text().strip())

    # 5. 完整 UI 提交路径
    print("[4] 提交下载（on_start 完整路径）")
    page.on_start()
    wait_until(lambda: page.engine.snapshot() and page.engine.snapshot()[0]["state"]
               in (TaskState.DOWNLOADING, TaskState.PENDING, TaskState.COMPLETED), what="任务进入队列")
    step("批次已提交", len(page.engine.snapshot()) == 1)

    # 6. 等待完成
    print("[5] 等待下载完成")
    wait_until(lambda: page.engine.snapshot()[0]["state"] is TaskState.COMPLETED, timeout=90, what="下载完成")
    snap = page.engine.snapshot()[0]
    step("任务完成", True, f"本地路径 {snap['local_path']}")

    # 7. 校验落盘内容
    p = Path(snap["local_path"])
    content = p.read_text(encoding="utf-8")
    step("文件落盘且内容有效", p.is_file() and "model_type" in content,
         f"{p.stat().st_size} B，位于 {p}")

    # 8. 历史入库
    rows = w.history_store.query("config.json")
    step("历史记录入库", any(r["status"] == "completed" and r["filename"] == "config.json" for r in rows))

    # 9. 队列文件应清空（全部完成后无待续队列）
    time.sleep(1.0)  # 等 _tick 落盘
    app.processEvents()
    step("队列持久化文件已清空", not queue_path.exists() or QueueStore(queue_path).load() == [])

    # 10. 重新下载应“秒传”
    page.file_panel.filter_edit.setText("")
    page.file_panel.select_all()  # 全选后重新提交 → 已存在文件应立即完成
    page.on_start()
    wait_until(lambda: all(s["state"] is TaskState.COMPLETED for s in page.engine.snapshot()
                           if s["filename"] == "config.json"), timeout=60, what="重复下载秒传")
    again = next(s for s in page.engine.snapshot() if s["filename"] == "config.json")
    step("重复下载秒传（跳过已有文件）", again["note"] == "文件已存在，跳过下载", again["note"])

    print()
    failed = [s for s in STEPS if not s[1]]
    print(f"结果：{len(STEPS) - len(failed)}/{len(STEPS)} 通过" + (f"，失败：{[s[0] for s in failed]}" if failed else "，全部通过 ✅"))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
