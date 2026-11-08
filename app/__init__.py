"""HF Mirror Downloader — Hugging Face 模型镜像下载器。

通过 hf-mirror 等镜像站点下载 Hugging Face 模型，
底层复用 huggingface_hub 官方能力（endpoint 直指镜像），
提供断点续传、并发下载、进度展示与本地历史记录。
"""

APP_NAME = "HF Mirror Downloader"
APP_ID = "HFMirrorDownloader"
APP_VERSION = "0.1.0"
