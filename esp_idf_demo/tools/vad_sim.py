#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vad_sim.py —— 离线复刻固件 waiting_speech 的 VAD 判定，用来验证
“提示音尾音 / PA pop / 回声被误判成用户说话” 这个自触发 bug。

它精确复现 main/audio_in.c: audio_in_wait_for_speech_start() 的算法：
  * 电平 level = 一个 chunk 内所有 int16(LE) 样本 |x| 的平均值
    (对应 audio_in_avg_abs_pcm16_le)
  * chunk = DEMO_AUDIO_CHUNK_BYTES = 2048 B = 1024 sample = 64 ms @16k
  * 开麦后前 DEMO_WAITING_SPEECH_ARM_MS = 150 ms 丢弃不判 (arming)
  * armed 之后：level >= START_THRESHOLD(450) 累计 hold；
    一旦某个 chunk 低于阈值，hold 清零 (audio_in.c:352)
  * hold 连续累计到 DEMO_SPEECH_START_HOLD_MS = 128 ms (=2 个 chunk)
    → 判定 speech_detected（= 误触发，如果此刻没人说话）

用法：
  # 直接分析固件里的提示音源文件（干净信号，看它本身电平是否就超阈值）
  python3 vad_sim.py ../spiffs/record_prompt_1.pcm

  # 分析你录下来的“提示音+之后安静”音频（手机贴麦克风录，最能反映真实声学路径）
  python3 vad_sim.py capture.wav --verbose

  # 只看某段（例如提示音结束后那段），跳过前 0.9s
  python3 vad_sim.py capture.wav --skip-ms 900

  # 自检：确认模拟器算法本身正确
  python3 vad_sim.py --selftest

输入格式：
  * .wav  —— 任意采样率/声道/位深，脚本会转成 16k/mono/s16le
  * 其它  —— 当作 raw PCM s16le mono 16k（和固件录音格式一致）
"""

import argparse
import struct
import sys
import wave

# Windows 控制台默认 GBK，统一切到 UTF-8，避免打印中文/符号时崩溃
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ---- 固件常量（与 main/config.h 保持一致，改了这里等于试参数） ----
SAMPLE_RATE        = 16000
BYTES_PER_SAMPLE   = 2
CHUNK_BYTES        = 2048           # DEMO_AUDIO_CHUNK_BYTES
ARM_MS             = 150            # DEMO_WAITING_SPEECH_ARM_MS
HOLD_MS            = 128            # DEMO_SPEECH_START_HOLD_MS
START_THRESHOLD    = 450           # DEMO_RECORD_VAD_START_THRESHOLD
SILENCE_THRESHOLD  = 300           # DEMO_RECORD_VAD_SILENCE_THRESHOLD (仅参考)

CHUNK_SAMPLES = CHUNK_BYTES // BYTES_PER_SAMPLE          # 1024
CHUNK_MS      = CHUNK_SAMPLES * 1000 // SAMPLE_RATE      # 64
HOLD_BYTES    = SAMPLE_RATE * HOLD_MS // 1000 * BYTES_PER_SAMPLE  # 4096
HOLD_CHUNKS   = HOLD_BYTES // CHUNK_BYTES                # 2


def avg_abs_pcm16_le(buf):
    """精确复刻 audio_in_avg_abs_pcm16_le：int16 LE 绝对值的整数均值。"""
    n = len(buf) // BYTES_PER_SAMPLE
    if n == 0:
        return 0
    total = 0
    # struct 一次性解包，比逐字节快
    samples = struct.unpack("<%dh" % n, buf[: n * BYTES_PER_SAMPLE])
    for s in samples:
        total += -s if s < 0 else s
    return total // n   # 固件是 uint64 sum / count，整数除法


def load_pcm_16k_mono(path):
    """读入音频，统一转成 16k/mono/s16le 的 bytes。"""
    if path.lower().endswith(".wav"):
        return _load_wav(path)
    with open(path, "rb") as f:
        return f.read()


def _load_wav(path):
    with wave.open(path, "rb") as w:
        ch = w.getnchannels()
        width = w.getsampwidth()
        rate = w.getframerate()
        n = w.getnframes()
        raw = w.readframes(n)
    if width != 2:
        sys.exit("暂只支持 16-bit WAV，当前 sampwidth=%d bytes" % width)
    samples = list(struct.unpack("<%dh" % (len(raw) // 2), raw))
    # 混成单声道
    if ch > 1:
        mono = [sum(samples[i:i + ch]) // ch for i in range(0, len(samples), ch)]
    else:
        mono = samples
    # 线性重采样到 16k
    if rate != SAMPLE_RATE:
        mono = _resample_linear(mono, rate, SAMPLE_RATE)
        print("  [i] 重采样 %d Hz -> %d Hz" % (rate, SAMPLE_RATE))
    if ch > 1:
        print("  [i] %d 声道 -> mono" % ch)
    return struct.pack("<%dh" % len(mono), *(_clip16(x) for x in mono))


def _clip16(x):
    return -32768 if x < -32768 else (32767 if x > 32767 else x)


def _resample_linear(samples, src_rate, dst_rate):
    if not samples:
        return samples
    n_out = int(len(samples) * dst_rate / src_rate)
    out = []
    step = src_rate / dst_rate
    for i in range(n_out):
        pos = i * step
        i0 = int(pos)
        frac = pos - i0
        s0 = samples[i0]
        s1 = samples[i0 + 1] if i0 + 1 < len(samples) else s0
        out.append(int(s0 + (s1 - s0) * frac))
    return out


def simulate(pcm, skip_ms=0, verbose=False):
    """复刻 wait_for_speech 主循环，返回结果 dict。"""
    skip_bytes = SAMPLE_RATE * skip_ms // 1000 * BYTES_PER_SAMPLE
    pcm = pcm[skip_bytes:]

    hold_bytes = 0
    max_level = 0
    armed = False
    detected_ms = None
    armed_ms = None
    timeline = []

    n_chunks = len(pcm) // CHUNK_BYTES
    for idx in range(n_chunks):
        chunk = pcm[idx * CHUNK_BYTES:(idx + 1) * CHUNK_BYTES]
        # 固件用读完 chunk 后的时刻判 arming；这里用音频时间等价建模
        chunk_end_ms = (idx + 1) * CHUNK_MS + skip_ms
        level = avg_abs_pcm16_le(chunk)
        if level > max_level:
            max_level = level

        state = "skip(arm)"
        if chunk_end_ms >= ARM_MS + skip_ms:
            if not armed:
                armed = True
                armed_ms = chunk_end_ms
            if level >= START_THRESHOLD:
                hold_bytes += CHUNK_BYTES
                state = "HOLD(%d/%d)" % (hold_bytes // CHUNK_BYTES, HOLD_CHUNKS)
                if hold_bytes >= HOLD_BYTES:
                    detected_ms = chunk_end_ms
                    timeline.append((chunk_end_ms, level, "*** speech_detected ***"))
                    break
            else:
                if hold_bytes:
                    state = "reset(hold=0)"
                else:
                    state = "below"
                hold_bytes = 0
        timeline.append((chunk_end_ms, level, state))

    return {
        "detected_ms": detected_ms,
        "armed_ms": armed_ms,
        "max_level": max_level,
        "n_chunks": n_chunks,
        "dur_ms": n_chunks * CHUNK_MS,
        "timeline": timeline,
        "verbose": verbose,
    }


def print_report(path, r):
    print("=" * 64)
    print("文件: %s" % path)
    print("时长: %d ms (%d 个 %dms 块)" % (r["dur_ms"], r["n_chunks"], CHUNK_MS))
    print("阈值: START=%d  ARM=%dms  HOLD=%dms(=%d 连续块)"
          % (START_THRESHOLD, ARM_MS, HOLD_MS, HOLD_CHUNKS))
    print("峰值电平 max_level = %d   (阈值 %d)" % (r["max_level"], START_THRESHOLD))
    print("-" * 64)
    if r["verbose"]:
        print(" t(ms)   level   state")
        for t, lvl, st in r["timeline"]:
            mark = ">=thr" if lvl >= START_THRESHOLD else ""
            print("%6d  %6d   %-16s %s" % (t, lvl, st, mark))
        print("-" * 64)
    if r["detected_ms"] is not None:
        after_arm = r["detected_ms"] - (r["armed_ms"] or ARM_MS)
        print("结果: [会误触发] speech_detected @ %d ms" % r["detected_ms"])
        print("      (armed 后仅 %d ms 就触发；若此刻无人说话 = 自触发 bug 复现)"
              % after_arm)
    else:
        print("结果: [OK] 未触发（这段音频不会让固件误判为用户说话）")
    print("=" * 64)


def selftest():
    """构造合成信号验证模拟器算法：
       静音段(应不触发) + 后接一段高电平(应触发)。"""
    def tone(ms, amp):
        n = SAMPLE_RATE * ms // 1000
        # 方波，|x| 恒等于 amp，便于对齐阈值判断
        return struct.pack("<%dh" % n, *([amp, -amp] * (n // 2)))
    # 300ms 静音(过 arm) + 200ms amp=600(>450，连续>128ms 应触发)
    pcm = tone(300, 0) + tone(200, 600)
    r = simulate(pcm)
    assert r["detected_ms"] is not None, "selftest 失败：应触发却没触发"
    # 全程 amp=100(<450) 不应触发
    r2 = simulate(tone(1000, 100))
    assert r2["detected_ms"] is None, "selftest 失败：不应触发却触发了"
    # amp=600 但只有 64ms(<128ms hold) 不应触发
    r3 = simulate(tone(300, 0) + tone(64, 600) + tone(300, 0))
    assert r3["detected_ms"] is None, "selftest 失败：单块超阈值不该触发"
    print("selftest 通过 [OK]  (阈值/arm/hold/清零 逻辑与固件一致)")


def main():
    ap = argparse.ArgumentParser(description="离线复刻固件 waiting_speech VAD，检测自触发")
    ap.add_argument("audio", nargs="?", help="音频文件 (.wav 或 raw s16le/16k/mono)")
    ap.add_argument("--skip-ms", type=int, default=0,
                    help="从开头跳过多少毫秒再开始判定 (例如只看提示音之后)")
    ap.add_argument("--verbose", action="store_true", help="打印逐块电平时间线")
    ap.add_argument("--threshold", type=int, default=None,
                    help="临时改 START_THRESHOLD 试参数")
    ap.add_argument("--arm-ms", type=int, default=None,
                    help="临时改 ARM_MS 试参数")
    ap.add_argument("--selftest", action="store_true", help="运行算法自检后退出")
    args = ap.parse_args()

    global START_THRESHOLD, ARM_MS
    if args.threshold is not None:
        START_THRESHOLD = args.threshold
    if args.arm_ms is not None:
        ARM_MS = args.arm_ms

    if args.selftest:
        selftest()
        return
    if not args.audio:
        ap.error("需要一个音频文件，或用 --selftest")

    pcm = load_pcm_16k_mono(args.audio)
    r = simulate(pcm, skip_ms=args.skip_ms, verbose=args.verbose)
    print_report(args.audio, r)
    sys.exit(1 if r["detected_ms"] is not None else 0)


if __name__ == "__main__":
    main()
