# -*- coding: utf-8 -*-
"""meeting_asr.py - 本地会议录音转文字稿（千问 Token Plan / qwen-audio-3.0-asr-flash）。

用法:
    PYTHONUTF8=1 python scripts/meeting_asr.py <audio_path>
        [--lang zh] [--slice-sec 240] [--concurrency 3]
        [--hotwords <热词表路径，默认 脚本上级/config/hotwords.txt>]
        [--context <领域背景路径，默认 脚本上级/config/context_prompt.txt>]
        [--upload base64|oss]
        [--consent]

说明:
    - 全程只调 token-plan 端点，模型固定 qwen-audio-3.0-asr-flash
    - 输入方式仅 Base64 Data URL（M1 实测 oss 兜底通道在 token-plan 端点不可用）
    - 切片级缓存断点续跑: cache/<sha1(file)>/slice_<idx>.json（锚定脚本上一级目录，与 CWD 无关）
    - 输出: <同名>.transcript.md（[mm:ss] 段落标记）+ .transcript.json + 用量报告
    - Key 读取: 环境变量 QWEN_TOKEN_PLAN_KEY -> Windows 用户级注册表；不打印、不落盘
"""

import argparse
import atexit
import base64
import concurrent.futures as futures
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

import requests

BASE_URL = "https://token-plan.cn-beijing.maas.aliyuncs.com"
GEN_PATH = "/api/v1/services/aigc/multimodal-generation/generation"
MODEL = "qwen-audio-3.0-asr-flash"
BITRATE = "32k"
SAMPLE_RATE = "16000"
MAX_SLICE_SEC = 300          # 模型单请求上限 5 分钟
MIN_SLICE_SEC = 60           # 官方 FAQ: 避免切得过短
MAX_DATA_URL_CHARS = 10 * 1024 * 1024  # Base64 编码后上限（参考 qwen3-asr 10MB）
RETRY_SLEEPS = [2, 8, 32]
REQUEST_TIMEOUT = 180
# 缓存锚定在脚本所在目录的上一级（仓库根 / skill 目录），与运行时 CWD 无关，
# 避免换目录运行导致缓存全 miss 而重复计费
SCRIPT_PARENT = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), os.pardir))
CACHE_ROOT = os.path.join(SCRIPT_PARENT, "cache")
CONSENT_MARK = os.path.join(CACHE_ROOT, ".consent_ok")
# 默认热词/领域背景同样锚定脚本上一级，CWD 无关；可用 --hotwords/--context 覆盖
DEFAULT_HOTWORDS = os.path.join(SCRIPT_PARENT, "config", "hotwords.txt")
DEFAULT_CONTEXT = os.path.join(SCRIPT_PARENT, "config", "context_prompt.txt")


class FatalError(Exception):
    """不可恢复错误（401/403/参数问题），立即终止。"""


def log(msg):
    print(msg, flush=True)


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


def check_ffmpeg():
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise FatalError(
                f"未找到 {tool}，请先安装（winget install Gyan.FFmpeg）并重开终端"
            )


def run_cmd(cmd, timeout=600):
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"命令超时({timeout}s): {os.path.basename(str(cmd[0]))}")


def ffprobe_duration(path):
    try:
        r = run_cmd([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", path,
        ])
    except RuntimeError as e:
        raise FatalError(str(e))
    if r.returncode != 0:
        raise FatalError(f"ffprobe 读取时长失败: {r.stderr[-300:]}")
    try:
        return float(r.stdout.strip())
    except ValueError:
        raise FatalError(f"ffprobe 输出无法解析为时长: {r.stdout.strip()[:100]}")


def sha1_file(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def transcode(src, dst):
    """整文件转 16kHz 单声道 32kbps mp3（缓存复用）；tmp+replace，防中断残留毒化缓存。"""
    if os.path.exists(dst):
        return
    tmp = dst + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    try:
        r = run_cmd([
            "ffmpeg", "-y", "-i", src,
            "-ac", "1", "-ar", SAMPLE_RATE, "-b:a", BITRATE,
            "-f", "mp3", tmp,
        ])
    except RuntimeError as e:
        raise FatalError(str(e))
    if r.returncode != 0:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise FatalError(f"ffmpeg 转码失败: {r.stderr[-300:]}")
    os.replace(tmp, dst)


def cut_slice(mp3_path, start, dur, dst):
    r = run_cmd([
        "ffmpeg", "-y", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
        "-i", mp3_path,
        "-ac", "1", "-ar", SAMPLE_RATE, "-b:a", BITRATE,
        dst,
    ])
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg 切片失败(start={start}): {r.stderr[-300:]}")


def resolve_default(path, fallback_name):
    """默认配置文件缺失时，回退到 assets/ 下的同名文件（skill 分发形态）。"""
    if os.path.exists(path):
        return path
    alt = os.path.join(SCRIPT_PARENT, "assets", fallback_name)
    return alt if os.path.exists(alt) else path


def load_context_messages(hotwords_path, context_path):
    """组装上下文增强消息（user 热词文本 + assistant 应答，成对置于音频消息之前）。

    返回 (messages, ctx_hash)；ctx_hash 参与缓存校验，热词/背景变更后旧缓存自动失效。
    """
    parts = []
    if hotwords_path and os.path.exists(hotwords_path):
        with open(hotwords_path, encoding="utf-8") as f:
            words = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
        if words:
            parts.append("以下是本次录音中可能出现的专有名词/热词，请在识别时优先参考："
                         + "、".join(words))
    if context_path and os.path.exists(context_path):
        with open(context_path, encoding="utf-8") as f:
            ctx = f.read().strip()
        if ctx:
            parts.append("以下是本次录音的领域背景，请在识别时参考：\n" + ctx)
    ctx_hash = hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()
    if not parts:
        return [], ctx_hash
    return [
        {"role": "user", "content": [{"type": "input_text", "text": "\n".join(parts)}]},
        {"role": "assistant", "content": [{"type": "text", "text": "好的，我会在识别中参考这些词汇。"}]},
    ], ctx_hash


SPECIAL_TOKEN_RE = re.compile(r"<\|[^|]*\|>")


def clean_text(text):
    """过滤模型输出中偶发残留的特殊 token（如 <|im_end|>）。"""
    if not isinstance(text, str):
        return text
    return SPECIAL_TOKEN_RE.sub("", text).strip()


def extract_text(data):
    """按 M1 实测结构取文本：output.text -> 顶层 text -> output.sentence.text。"""
    if not isinstance(data, dict):
        return None
    out = data.get("output")
    if isinstance(out, dict):
        if out.get("text"):
            return clean_text(out["text"])
        sent = out.get("sentence")
        if isinstance(sent, dict) and sent.get("text"):
            return clean_text(sent["text"])
    if data.get("text"):
        return clean_text(data["text"])
    sent = data.get("sentence")
    if isinstance(sent, dict) and sent.get("text"):
        return clean_text(sent["text"])
    return None


class Throttle:
    """429 限流状态：触发后所有请求串行化。"""

    def __init__(self):
        self.event = threading.Event()
        self.lock = threading.Lock()

    def hit(self):
        self.event.set()

    def wrap(self, fn):
        if self.event.is_set():
            with self.lock:
                return fn()
        return fn()


def mask_sensitive(text):
    """响应文本落入日志/缓存前脱敏（纵深防御，端点理论上不会回显凭据）。"""
    text = re.sub(r"sk-sp-[A-Za-z0-9_-]+", "sk-sp-***", text)
    return re.sub(r"Bearer\s+\S+", "Bearer ***", text)


def interruptible_sleep(abort, seconds):
    """退避等待，可被 abort 中断；被中断时抛 FatalError 终止。"""
    if abort.wait(seconds):
        raise FatalError("收到终止信号，停止重试")


def call_api(key, data_url, context_messages, throttle, abort):
    """单次切片调用，返回 (text, usage)。重试/限流/致命错误在此处理。"""
    messages = context_messages + [
        {"role": "user", "content": [
            {"type": "input_audio", "input_audio": {"data": data_url}}
        ]}
    ]
    body = {
        "model": MODEL,
        "input": {"messages": messages},
        "parameters": {"format": "mp3", "sample_rate": SAMPLE_RATE},
    }
    headers = {
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
        "X-DashScope-SSE": "disable",
    }
    url = BASE_URL + GEN_PATH

    last_err = "unknown"
    for attempt in range(len(RETRY_SLEEPS) + 1):
        if abort.is_set():
            raise FatalError("收到终止信号，停止重试")
        try:
            r = throttle.wrap(lambda: requests.post(
                url, headers=headers, json=body, timeout=REQUEST_TIMEOUT))
        except requests.RequestException as e:
            last_err = f"网络异常: {e.__class__.__name__}"
            if attempt < len(RETRY_SLEEPS):
                interruptible_sleep(abort, RETRY_SLEEPS[attempt])
                continue
            raise RuntimeError(last_err)

        if r.status_code == 200:
            try:
                data = r.json()
            except ValueError:
                raise RuntimeError("响应非 JSON: " + mask_sensitive(r.text[:200]))
            text = extract_text(data)
            if text is None:
                raise RuntimeError("响应无文本字段: "
                                   + mask_sensitive(
                                       json.dumps(data, ensure_ascii=False)[:300]))
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else None
            return text, usage

        if r.status_code in (401, 403):
            raise FatalError(
                f"HTTP {r.status_code} 鉴权失败，请检查 sk-sp- Key 与套餐余量（不重试）")
        if r.status_code == 429:
            throttle.hit()
            retry_after = r.headers.get("Retry-After")
            wait = min(float(retry_after), 120.0) \
                if retry_after and retry_after.isdigit() \
                else RETRY_SLEEPS[min(attempt, len(RETRY_SLEEPS) - 1)]
            last_err = "HTTP 429 限流"
            if attempt < len(RETRY_SLEEPS):
                log(f"  [429] 限流，降并发为 1，{wait:.0f}s 后重试")
                interruptible_sleep(abort, wait)
                continue
            raise RuntimeError(last_err)
        if 500 <= r.status_code < 600:
            last_err = f"HTTP {r.status_code} 服务端错误"
            if attempt < len(RETRY_SLEEPS):
                interruptible_sleep(abort, RETRY_SLEEPS[attempt])
                continue
            raise RuntimeError(last_err)

        # 其他 4xx（含 Base64 被拒）：oss 兜底已实测不可用，直接判失败
        body_preview = mask_sensitive(r.text[:300].replace("\n", " "))
        raise RuntimeError(f"HTTP {r.status_code} 请求被拒: {body_preview}")

    raise RuntimeError(last_err)


def fmt_ts(seconds):
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def slice_cache_path(cache_dir, idx):
    return os.path.join(cache_dir, f"slice_{idx:04d}.json")


def load_slice_cache(cache_dir, idx, offset=None, dur=None, ctx_hash=None):
    """读取切片缓存；字段不完整、与本次切片计划不匹配（如改了 --slice-sec）
    或上下文（热词/背景）已变更时视为未命中。"""
    p = slice_cache_path(cache_dir, idx)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict) or rec.get("idx") != idx:
        return None
    if rec.get("status") == "ok" and not isinstance(rec.get("text"), str):
        return None
    if offset is not None and abs(rec.get("offset_sec", -1) - round(offset, 3)) > 0.01:
        return None
    if dur is not None and abs(rec.get("dur_sec", -1) - round(dur, 3)) > 0.01:
        return None
    if ctx_hash is not None and rec.get("ctx_hash", "") != ctx_hash:
        return None
    return rec


def save_slice_cache(cache_dir, rec):
    p = slice_cache_path(cache_dir, rec["idx"])
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def merge_usage(u1, u2):
    """合并两段切片的 usage（自动减半场景）。"""
    if not u1:
        return u2
    if not u2:
        return u1
    merged = dict(u1)
    if isinstance(u1.get("duration"), (int, float)) and \
            isinstance(u2.get("duration"), (int, float)):
        merged["duration"] = u1["duration"] + u2["duration"]
    return merged


def process_slice(idx, offset, dur, mp3_path, cache_dir, key,
                  context_messages, ctx_hash, throttle, abort):
    if abort.is_set():
        return {"idx": idx, "offset_sec": round(offset, 3), "status": "pending"}, False
    cached = load_slice_cache(cache_dir, idx, offset, dur, ctx_hash)
    if cached and cached.get("status") == "ok":
        log(f"  [切片 {idx}] 命中缓存，跳过 (offset={fmt_ts(offset)})")
        cached["_from_cache"] = True
        return cached, False

    slice_mp3 = os.path.join(cache_dir, f"slice_{idx:04d}.mp3")
    rec = {"idx": idx, "offset_sec": round(offset, 3),
           "dur_sec": round(dur, 3), "ctx_hash": ctx_hash, "status": "pending"}

    def transcribe_range(off, d):
        """切音频并调 API；Base64 超限时自动减半重试（SPEC 2.5）。"""
        cut_slice(mp3_path, off, d, slice_mp3)
        if abort.is_set():
            raise FatalError("收到终止信号")
        with open(slice_mp3, "rb") as f:
            data_url = "data:audio/mpeg;base64," + \
                base64.b64encode(f.read()).decode("ascii")
        if len(data_url) > MAX_DATA_URL_CHARS:
            half = d / 2
            if half < 1.0:
                raise RuntimeError("切片已极小但 Base64 仍超限，放弃")
            log(f"  [切片 {idx}] Base64 超上限，自动减半为两段 (offset={fmt_ts(off)})")
            t1, u1 = transcribe_range(off, half)
            t2, u2 = transcribe_range(off + half, d - half)
            return t1 + "\n" + t2, merge_usage(u1, u2)
        return call_api(key, data_url, context_messages, throttle, abort)

    try:
        t0 = time.time()
        text, usage = transcribe_range(offset, dur)
        cost = time.time() - t0
        rec.update({"status": "ok", "text": text,
                    "usage": usage, "cost_sec": round(cost, 1)})
        log(f"  [切片 {idx}] 完成 (offset={fmt_ts(offset)}, {cost:.1f}s, "
            f"usage={json.dumps(usage, ensure_ascii=False) if usage else '无'})")
    except FatalError:
        abort.set()
        save_slice_cache(cache_dir, rec)
        raise
    except Exception as e:  # noqa: BLE001 - 切片级失败保留状态，下次重跑补齐
        rec.update({"status": "failed", "error": str(e)[:500]})
        log(f"  [切片 {idx}] 失败 (offset={fmt_ts(offset)}): {e}")
    finally:
        if os.path.exists(slice_mp3):
            os.remove(slice_mp3)
    save_slice_cache(cache_dir, rec)
    return rec, True


def check_consent(consent_flag):
    if os.path.exists(CONSENT_MARK):
        return
    if not consent_flag:
        raise FatalError(
            "首次使用需显式确认：音频将通过互联网发送至阿里云 token-plan 端点处理。"
            "确认请附加 --consent 参数重跑（只需确认一次）。")
    os.makedirs(CACHE_ROOT, exist_ok=True)
    with open(CONSENT_MARK, "w", encoding="utf-8") as f:
        json.dump({"consent_at": datetime.datetime.now().astimezone().isoformat(
            timespec="seconds")}, f, ensure_ascii=False)
    log("[OK] 已记录上云处理确认（cache/.consent_ok）")


def pid_alive(pid):
    """探测进程是否存活（用于 stale lock 接管）。"""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not h:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def main():
    ap = argparse.ArgumentParser(description="本地会议录音转文字稿（Token Plan ASR）")
    ap.add_argument("audio", help="音频文件路径（mp3/m4a/wav/flac 等）")
    ap.add_argument("--lang", default="zh", help="音频主语言（记录用，默认 zh）")
    ap.add_argument("--slice-sec", type=int, default=240, help="切片秒数(60-300)")
    ap.add_argument("--concurrency", type=int, default=3, help="并发请求数")
    ap.add_argument("--hotwords", default=DEFAULT_HOTWORDS, help="热词表路径")
    ap.add_argument("--context", default=DEFAULT_CONTEXT, help="领域背景路径")
    ap.add_argument("--upload", choices=["base64", "oss"], default="base64",
                    help="输入方式（oss 通道 M1 实测不可用，仅保留接口）")
    ap.add_argument("--consent", action="store_true", help="首次使用确认上云处理")
    args = ap.parse_args()

    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        log("[WARN] stdout 编码非 UTF-8，建议以 PYTHONUTF8=1 运行，避免乱码")
    if not (MIN_SLICE_SEC <= args.slice_sec <= MAX_SLICE_SEC):
        raise FatalError(f"--slice-sec 需在 {MIN_SLICE_SEC}~{MAX_SLICE_SEC} 之间")
    if not (1 <= args.concurrency <= 8):
        raise FatalError("--concurrency 需在 1~8 之间")
    if args.upload == "oss":
        raise FatalError(
            "oss 上传通道 M1 实测在 token-plan 端点不可用（getPolicy 404），"
            "请使用默认的 base64 方式")
    if not os.path.isfile(args.audio):
        raise FatalError(f"音频文件不存在或不是普通文件: {args.audio}")

    check_consent(args.consent)
    check_ffmpeg()
    key, key_src = get_api_key()
    if not key:
        raise FatalError("未找到 QWEN_TOKEN_PLAN_KEY（环境变量与用户级注册表均无）")

    audio_path = os.path.abspath(args.audio)
    log(f"[信息] 文件: {audio_path}")
    log(f"[信息] Key 来源: {key_src}; 模型: {MODEL}")

    file_hash = sha1_file(audio_path)
    duration = ffprobe_duration(audio_path)
    log(f"[信息] 时长: {duration:.1f}s ({fmt_ts(duration)})")

    cache_dir = os.path.join(CACHE_ROOT, file_hash)
    os.makedirs(cache_dir, exist_ok=True)

    # 进程锁：同一文件只允许一个转写进程，避免并发重复请求重复计费；
    # 发现 stale lock（持有进程已死）时自动接管
    lock_path = os.path.join(cache_dir, ".lock")
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            with open(lock_path, encoding="utf-8") as f:
                old_pid = int(f.read().strip() or "0")
        except (OSError, ValueError):
            old_pid = 0
        if pid_alive(old_pid):
            raise FatalError(
                f"另一个转写进程(PID {old_pid})正在处理同一文件，请等其完成")
        log(f"[WARN] 发现残留锁（PID {old_pid or '未知'} 已退出），自动接管")
        os.remove(lock_path)
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise FatalError("锁文件竞争（另一进程抢先接管），请重试")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    atexit.register(lambda: os.path.exists(lock_path) and os.remove(lock_path))

    mp3_path = os.path.join(cache_dir, "full_16k.mp3")
    transcode(audio_path, mp3_path)
    log("[信息] 预处理完成: 16kHz 单声道 32kbps mp3")

    # 切片计划；尾部不足 1 秒的退化段并入上一段（但不得突破模型单请求上限）
    plan = []
    offset = 0.0
    idx = 0
    while offset < duration:
        d = min(float(args.slice_sec), duration - offset)
        if d < 1.0 and plan:
            i0, o0, d0 = plan[-1]
            if d0 + d <= MAX_SLICE_SEC:
                plan[-1] = (i0, o0, d0 + d)
                break
            # 并入会超过模型单请求上限，保留为独立小切片（宁短勿超）
        plan.append((idx, offset, d))
        idx += 1
        offset += d
    log(f"[信息] 切片计划: {len(plan)} 段，每段 <= {args.slice_sec}s，并发 {args.concurrency}")

    if args.hotwords == DEFAULT_HOTWORDS:
        args.hotwords = resolve_default(args.hotwords, "hotwords.txt")
    if args.context == DEFAULT_CONTEXT:
        args.context = resolve_default(args.context, "context_prompt.txt")
    context_messages, ctx_hash = load_context_messages(args.hotwords, args.context)
    if context_messages:
        log("[信息] 已加载热词/领域上下文（上下文增强）")
    for p, dflt, name in ((args.hotwords, DEFAULT_HOTWORDS, "热词表"),
                          (args.context, DEFAULT_CONTEXT, "领域背景")):
        if p != dflt and not os.path.exists(p):
            log(f"[WARN] 指定的{name}文件不存在: {p}")

    throttle = Throttle()
    abort = threading.Event()
    results = {}
    new_requests = 0
    pool = futures.ThreadPoolExecutor(max_workers=args.concurrency)
    try:
        futs = {
            pool.submit(process_slice, i, off, d, mp3_path, cache_dir,
                        key, context_messages, ctx_hash, throttle, abort): i
            for i, off, d in plan
        }
        for fut in futures.as_completed(futs):
            rec, sent = fut.result()
            results[rec["idx"]] = rec
            new_requests += 1 if sent else 0
        pool.shutdown(wait=True)
    except FatalError as e:
        abort.set()
        pool.shutdown(wait=False, cancel_futures=True)
        raise FatalError(f"转写终止: {e}")
    except KeyboardInterrupt:
        abort.set()
        pool.shutdown(wait=False, cancel_futures=True)
        log("\n[中断] 收到 Ctrl+C，已完成切片已缓存，重跑将断点续传")
        sys.exit(130)

    # 合并输出（缓存中的文本也过一遍特殊 token 清洗）
    ordered = [results[i] for i, _, _ in plan if i in results]
    for r in ordered:
        if r.get("status") == "ok" and isinstance(r.get("text"), str):
            r["text"] = clean_text(r["text"])
    ok = [r for r in ordered if r.get("status") == "ok"]
    failed = [r for r in ordered if r.get("status") != "ok"]
    cache_hits = sum(1 for r in ordered if r.get("_from_cache"))

    billed = sum((r.get("usage") or {}).get("duration", 0) for r in ok)
    base_noext = os.path.splitext(audio_path)[0]
    md_path = base_noext + ".transcript.md"
    json_path = base_noext + ".transcript.json"

    tmp_md = md_path + ".tmp"
    with open(tmp_md, "w", encoding="utf-8") as f:
        for r in ordered:
            if r.get("status") == "ok":
                f.write(f"[{fmt_ts(r['offset_sec'])}] {r['text']}\n\n")
            else:
                f.write(f"[{fmt_ts(r['offset_sec'])}] "
                        f"[切片 {r['idx']} 转写失败: "
                        f"{r.get('error', '未知错误')}；重跑命令可补齐]\n\n")
    os.replace(tmp_md, md_path)

    doc = {
        "file": os.path.basename(audio_path),
        "duration_sec": round(duration, 1),
        "lang": args.lang,
        "model": MODEL,
        "created_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "slices": [
            {"idx": r["idx"], "offset_sec": r["offset_sec"],
             "status": r["status"],
             **({"text": r["text"]} if r.get("status") == "ok" else
                {"error": r.get("error", "")})}
            for r in ordered
        ],
        "usage": {"requests": new_requests,
                  "cache_hits": cache_hits,
                  "failed": len(failed),
                  "billed_sec": billed, "audio_tokens": None},
    }
    tmp_json = json_path + ".tmp"
    with open(tmp_json, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    os.replace(tmp_json, json_path)

    log("\n===== 用量报告 =====")
    log(f"音频时长: {duration:.1f}s | 切片: {len(plan)} (成功 {len(ok)}, 失败 {len(failed)})")
    log(f"本次新请求: {new_requests} | 缓存命中: {cache_hits}")
    log(f"计费时长(成功切片 usage.duration 合计): {billed}s | audio_tokens: 无该字段(M1 实测)")
    log(f"输出: {md_path}")
    log(f"输出: {json_path}")
    if failed:
        log(f"[提示] {len(failed)} 个切片失败已在 md 中占位标记，重跑本命令将只补失败切片")
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except FatalError as e:
        log(f"[FAIL] {e}")
        sys.exit(2)
