# -*- coding: utf-8 -*-
"""M1 spike: 验证 token-plan 端点五项假设（仅调试用，非交付脚本）。

用法:
    PYTHONUTF8=1 python scripts/m1_spike.py <audio_path> [--base BASE_URL]

五项假设:
    1. qwen-audio-3.0-asr-flash 在 token-plan 端点可调通（200 + 转写文本）
    2. Base64 Data URL 输入被接受
    3. 响应字段结构（预期 output.text）
    4. 是否返回 usage（audio_tokens/seconds）
    5. uploads?action=getPolicy 在 token-plan 端点是否可用（oss 兜底通道）

Key 读取顺序: 环境变量 QWEN_TOKEN_PLAN_KEY -> Windows 用户级注册表（setx 写入处）。
Key 不打印、不落盘；Base64 不回显，只打印长度。
"""

import argparse
import base64
import json
import os
import subprocess
import sys

DEFAULT_BASE = "https://token-plan.cn-beijing.maas.aliyuncs.com"
GEN_PATH = "/api/v1/services/aigc/multimodal-generation/generation"
MODEL = "qwen-audio-3.0-asr-flash"
SPIKE_SECONDS = 30


def get_api_key():
    key = os.environ.get("QWEN_TOKEN_PLAN_KEY", "").strip()
    if key:
        return key, "env"
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            key, _ = winreg.QueryValueEx(k, "QWEN_TOKEN_PLAN_KEY")
        key = str(key).strip()
        if key:
            return key, "registry"
    except (OSError, ImportError):
        pass
    return None, None


def transcode(src, dst):
    """ffmpeg 转 16kHz 单声道 32kbps mp3，截取前 SPIKE_SECONDS 秒。"""
    cmd = [
        "ffmpeg", "-y", "-i", src,
        "-t", str(SPIKE_SECONDS),
        "-ac", "1", "-ar", "16000", "-b:a", "32k",
        dst,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print("[FAIL] ffmpeg 转码失败:", r.stderr[-500:])
        sys.exit(2)
    size = os.path.getsize(dst)
    print(f"[OK] 转码完成: {dst} ({size} bytes, {SPIKE_SECONDS}s 16kHz mono 32kbps)")


def post_json(url, headers, body, timeout=120):
    import requests

    r = requests.post(url, headers=headers, json=body, timeout=timeout)
    return r


def show_response(tag, r):
    print(f"\n===== {tag} =====")
    print("HTTP", r.status_code)
    try:
        data = r.json()
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return data
    except ValueError:
        print(r.text[:3000])
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio", help="测试音频路径（任意格式，取前 30s）")
    ap.add_argument("--base", default=DEFAULT_BASE, help="端点 base URL")
    args = ap.parse_args()

    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        print("[WARN] stdout 非 UTF-8，请以 PYTHONUTF8=1 运行")

    key, key_src = get_api_key()
    if not key:
        print("[FAIL] 未找到 QWEN_TOKEN_PLAN_KEY（环境变量与用户级注册表均无）")
        sys.exit(2)
    print(f"[OK] Key 来源: {key_src}, 前缀: {key[:6]}..., 长度: {len(key)}")

    mp3_path = os.path.join("cache", "m1_spike_16k.mp3")
    os.makedirs("cache", exist_ok=True)
    transcode(args.audio, mp3_path)

    with open(mp3_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    data_url = "data:audio/mpeg;base64," + b64
    print(f"[OK] Base64 编码完成，Data URL 长度: {len(data_url)} 字符（不回显内容）")

    body = {
        "model": MODEL,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_audio", "input_audio": {"data": data_url}}
                    ],
                }
            ]
        },
        "parameters": {"format": "mp3", "sample_rate": "16000"},
    }
    headers = {
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
        "X-DashScope-SSE": "disable",
    }

    url = args.base.rstrip("/") + GEN_PATH
    print(f"\n[1/2] POST {url}  (model={MODEL})")
    try:
        r = post_json(url, headers, body)
    except Exception as e:  # noqa: BLE001 - spike 阶段打印一切异常
        print("[FAIL] 请求异常:", repr(e))
        sys.exit(1)
    data = show_response("ASR generation 响应", r)

    print("\n--- 五项假设判定 ---")
    ok200 = r.status_code == 200
    text = None
    if isinstance(data, dict):
        out = data.get("output") or {}
        if isinstance(out, dict):
            text = out.get("text")
            if text is None and isinstance(out.get("output"), dict):
                text = (out["output"].get("sentence") or {}).get("text")
    print(f"1. 模型可调通(200+文本): {'YES' if ok200 and text else 'NO/待查'}")
    print(f"2. Base64 Data URL 被接受: {'YES' if ok200 else 'NO/待查(看上方错误码)'}")
    if isinstance(data, dict):
        top_keys = list(data.keys())
        out_keys = list((data.get("output") or {}).keys()) if isinstance(data.get("output"), dict) else None
        print(f"3. 响应字段结构: 顶层={top_keys} output={out_keys} -> text={'有' if text else '无'}")
        usage = data.get("usage")
        print(f"4. usage 字段: {json.dumps(usage, ensure_ascii=False) if usage else '无'}")
    else:
        print("3. 响应字段结构: 非 JSON 响应")
        print("4. usage 字段: 无法判定")

    # 假设5: uploads getPolicy
    up_url = args.base.rstrip("/") + "/api/v1/uploads?action=getPolicy&model=" + MODEL
    print(f"\n[2/2] GET {up_url.split('?')[0]}?action=getPolicy&model={MODEL}")
    try:
        import requests

        r2 = requests.get(up_url, headers={"Authorization": "Bearer " + key}, timeout=60)
        d2 = show_response("uploads getPolicy 响应", r2)
        has_policy = isinstance(d2, dict) and bool(d2.get("data"))
        print(f"5. getPolicy 可用: {'YES' if r2.status_code == 200 and has_policy else 'NO/待查'} (HTTP {r2.status_code})")
    except Exception as e:  # noqa: BLE001
        print("5. getPolicy 可用: 请求异常", repr(e))


if __name__ == "__main__":
    main()
