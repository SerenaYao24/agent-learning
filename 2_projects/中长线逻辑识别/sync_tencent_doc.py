#!/usr/bin/env python3
"""
将 interest_stock.md 同步到腾讯文档（使用 V2 Open API）。

使用方式:
  1. 在 .env 中配置凭证
  2. python sync_tencent_doc.py
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import requests

# ── 配置 ─────────────────────────────────────────────

CONFIG = {
    "client_id": "",
    "access_token": "",
    "open_id": "",
    "file_name": "interest_stock",
    "local_file": str(Path(__file__).parent / "interest_stock.md"),
    "state_file": str(Path(__file__).parent / ".tencent_doc_state.json"),
}

_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key, val = key.strip().upper(), val.strip()
        if key == "TENCENT_DOC_CLIENT_ID":
            CONFIG["client_id"] = val
        elif key == "TENCENT_DOC_ACCESS_TOKEN":
            CONFIG["access_token"] = val
        elif key == "TENCENT_DOC_OPEN_ID":
            CONFIG["open_id"] = val

API_BASE = "https://docs.qq.com/openapi/drive/v2"


def _headers():
    return {
        "Access-Token": CONFIG["access_token"],
        "Client-Id": CONFIG["client_id"],
        "Open-Id": CONFIG["open_id"],
    }


def _form_headers():
    h = _headers()
    h["Content-Type"] = "application/x-www-form-urlencoded"
    return h


# ── 文件夹操作 ──


def get_or_create_folder(name: str) -> str:
    """获取或创建文件夹，返回 folder ID"""
    # 列出根目录下的文件夹
    resp = requests.get(f"{API_BASE}/folders", headers=_headers())
    resp.raise_for_status()
    for f in resp.json().get("data", {}).get("list", []):
        if f.get("title") == name:
            return f["ID"]
    # 创建
    resp2 = requests.post(f"{API_BASE}/folders", headers=_form_headers(),
                          data={"title": name})
    if resp2.status_code == 200:
        return resp2.json()["data"]["ID"]
    print(f"创建文件夹失败: {resp2.status_code} {resp2.text}")
    resp2.raise_for_status()


def list_files_in_folder(folder_id: str) -> list:
    """列出文件夹中的文件"""
    resp = requests.get(f"{API_BASE}/folders/{folder_id}",
                        headers=_headers())
    resp.raise_for_status()
    return resp.json().get("data", {}).get("list", [])


def delete_file(file_id: str):
    resp = requests.delete(f"{API_BASE}/files/{file_id}", headers=_headers())
    if resp.status_code not in (200, 204, 404):
        print(f"删除文件失败: {resp.status_code} {resp.text}")
        resp.raise_for_status()


# ── 文件上传流程 ──


def create_import_info(file_md5: str, file_name: str, file_size: int) -> dict:
    """获取 COS 上传信息"""
    resp = requests.post(
        f"{API_BASE}/files/upload-url",
        headers=_form_headers(),
        data={"fileMD5": file_md5, "fileName": file_name,
              "fileSize": file_size},
    )
    if resp.status_code != 200:
        print(f"获取上传地址失败: {resp.status_code} {resp.text}")
        resp.raise_for_status()
    data = resp.json()
    if data.get("ret") != 0:
        raise Exception(f"获取上传地址失败: {data}")
    return data["data"]


def upload_to_cos(file_path: str, cos_url: str):
    """上传文件内容到腾讯云 COS"""
    with open(file_path, "rb") as f:
        content = f.read()
    resp = requests.put(cos_url, data=content,
                        headers={"Content-Type": "application/octet-stream"})
    if resp.status_code != 200:
        print(f"COS 上传失败: {resp.status_code} {resp.text}")
        resp.raise_for_status()


def async_import_document(file_md5: str, file_name: str, cos_file_key: str,
                          folder_id: str = None) -> str:
    """触发异步导入，返回 progress_query_id"""
    data = {"fileMD5": file_md5, "fileName": file_name,
            "COSFileKey": cos_file_key}
    if folder_id:
        data["parentfolderID"] = folder_id
    resp = requests.post(f"{API_BASE}/files/async-import",
                         headers=_form_headers(), data=data)
    if resp.status_code != 200:
        print(f"触发导入失败: {resp.status_code} {resp.text}")
        resp.raise_for_status()
    result = resp.json()
    if result.get("ret") != 0:
        raise Exception(f"触发导入失败: {result}")
    return result["data"]["progressQueryID"]


def poll_import(progress_query_id: str, timeout: int = 120) -> dict:
    """轮询导入进度，返回文件信息"""
    url = f"{API_BASE}/files/import-progress"
    start = time.time()
    while time.time() - start < timeout:
        resp = requests.get(url, headers=_headers(),
                            params={"progressQueryID": progress_query_id})
        if resp.status_code != 200:
            time.sleep(2)
            continue
        data = resp.json()
        if data.get("ret") == 0 and data["data"].get("ID"):
            return data["data"]
        print(".", end="", flush=True)
        time.sleep(2)
    raise TimeoutError(f"导入超时 ({timeout}s)")


# ── 主流程 ──


def main():
    for k in ("client_id", "access_token", "open_id"):
        if not CONFIG[k]:
            print(f"错误：未配置 {k}")
            sys.exit(1)

    local_file = CONFIG["local_file"]
    if not os.path.exists(local_file):
        print(f"错误：文件不存在: {local_file}")
        sys.exit(1)

    state_path = CONFIG["state_file"]
    state = {}
    if os.path.exists(state_path):
        state = json.loads(Path(state_path).read_text())

    print(f"本地文件: {local_file}")

    # ── 确保文件夹存在 ──
    folder_id = get_or_create_folder("中长线逻辑识别")
    print(f"目标文件夹 ID: {folder_id}")

    # ── 删除同名的旧文档 ──
    existing_files = list_files_in_folder(folder_id)
    for f in existing_files:
        if f.get("title") == CONFIG["file_name"]:
            try:
                delete_file(f["ID"])
                print(f"已删除旧文档: {f['ID']}")
            except Exception as e:
                print(f"删除旧文档失败: {e}")

    # ── 用 .txt 扩展名构建临时文件 ──
    # (Tencent Docs V2 API 不支持 .md 扩展名)
    tmp_dir = tempfile.mkdtemp()
    tmp_file = os.path.join(tmp_dir, f"{CONFIG['file_name']}.txt")
    shutil.copy2(local_file, tmp_file)

    try:
        md5 = hashlib.md5(open(tmp_file, "rb").read()).hexdigest()
        size = os.path.getsize(tmp_file)
        upload_name = f"{CONFIG['file_name']}.txt"

        # 第1步：获取 COS 上传地址
        print("获取上传地址...")
        cos_info = create_import_info(md5, upload_name, size)
        cos_url = cos_info["COSPutURL"]
        cos_key = cos_info["COSFileKey"]

        # 第2步：上传到 COS
        print("上传文件到 COS...")
        upload_to_cos(tmp_file, cos_url)

        # 第3步：触发异步导入
        print("触发导入...", end="", flush=True)
        progress_id = async_import_document(md5, upload_name, cos_key, folder_id)

        # 第4步：轮询等待完成
        print("等待导入完成", end="", flush=True)
        result = poll_import(progress_id)
        print(" OK")

        file_id = result["ID"]
        print(f"  文件 ID: {file_id}")
        print(f"  腾讯文档链接: https://docs.qq.com/doc/{file_id}")

        # ── 保存状态 ──
        Path(state_path).write_text(
            json.dumps({"file_id": file_id, "folder_id": folder_id},
                       indent=2, ensure_ascii=False))
        print(f"状态已保存至: {state_path}")

        # ── 设置公开编辑权限 ──
        try:
            resp = requests.patch(
                f"{API_BASE}/files/{file_id}/permission",
                headers=_form_headers(),
                data={"policy": "publicWrite", "copyEnabled": True,
                      "readerCommentEnabled": True},
            )
            if resp.status_code == 200:
                print("已设置公开编辑权限")
        except Exception as e:
            print(f"设置权限失败（可忽略）: {e}")

        print()
        print("✅ 同步完成！")

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
