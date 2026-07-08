#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze_wait_logs.py —— 从设备串口日志里统计 waiting_speech 的误触发。

复现思路（不用你精准说话）：
  1. 把设备放在**安静**环境里。
  2. 反复触发交互（按键 / 唤醒），但**全程不说话**。
  3. 理论上每次都应 waiting_speech 超时；只要出现 speech_detected，
     就是设备把“自己提示音尾音 / PA pop / 回声”误判成了用户说话 = bug。
  4. 本脚本统计误触发率、触发时刻分布、max_level，把 bug 量化。

它解析这些固件已有日志行（main/audio_in.c, main/main.c）：
  prompt_play_end_ms=...
  stage=waiting_speech event=armed elapsed_ms=...
  stage=waiting_speech event=speech_detected elapsed_ms=... max_level=... speech_prefix_bytes=...
  stage=waiting_speech event=timeout elapsed_ms=... max_level=...
  speech_start_ms=...
  以及新一轮起点：'-> starting pipeline' / 'pipeline_start'

用法：
  # 直接读串口（需要 pyserial: pip install pyserial），并同时存档
  python3 analyze_wait_logs.py --port /dev/ttyUSB0 --baud 115200 --tee run.log

  # 读已经抓好的日志文件
  python3 analyze_wait_logs.py --file run.log

  # 从管道读（例如 idf.py monitor | python3 analyze_wait_logs.py -）
  idf.py monitor | python3 analyze_wait_logs.py -

按 Ctrl-C 结束时打印汇总。读文件时读完即打印。
"""

import argparse
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ARM_MS = 150  # 与固件 DEMO_WAITING_SPEECH_ARM_MS 一致，用来判断“触发得多快”

RE_NEW_RUN   = re.compile(r"(->\s*starting pipeline|pipeline_start\b)")
RE_PROMPT_END = re.compile(r"prompt_play_end_ms=([\d.]+)")
RE_ARMED     = re.compile(r"event=armed\s+elapsed_ms=(\d+)")
RE_DETECTED  = re.compile(
    r"event=speech_detected\s+elapsed_ms=(\d+)\s+max_level=(\d+)"
    r"(?:\s+speech_prefix_bytes=(\d+))?")
RE_TIMEOUT   = re.compile(r"event=timeout\s+elapsed_ms=(\d+)\s+max_level=(\d+)")


class Run:
    __slots__ = ("idx", "prompt_end", "armed_ms", "outcome",
                 "elapsed_ms", "max_level", "prefix_bytes")

    def __init__(self, idx):
        self.idx = idx
        self.prompt_end = None
        self.armed_ms = None
        self.outcome = None          # 'detected' | 'timeout' | None(未完成)
        self.elapsed_ms = None
        self.max_level = None
        self.prefix_bytes = None


class Analyzer:
    def __init__(self, fast_ms):
        self.runs = []
        self.cur = None
        self.fast_ms = fast_ms       # armed 后多少 ms 内触发算“快速自触发”

    def _start_run(self):
        if self.cur is not None:
            self.runs.append(self.cur)
        self.cur = Run(len(self.runs) + 1)

    def feed(self, line):
        if RE_NEW_RUN.search(line):
            self._start_run()
            return
        if self.cur is None:
            self.cur = Run(1)

        m = RE_PROMPT_END.search(line)
        if m:
            self.cur.prompt_end = float(m.group(1)); return
        m = RE_ARMED.search(line)
        if m:
            self.cur.armed_ms = int(m.group(1)); return
        m = RE_DETECTED.search(line)
        if m:
            self.cur.outcome = "detected"
            self.cur.elapsed_ms = int(m.group(1))
            self.cur.max_level = int(m.group(2))
            self.cur.prefix_bytes = int(m.group(3)) if m.group(3) else None
            return
        m = RE_TIMEOUT.search(line)
        if m:
            self.cur.outcome = "timeout"
            self.cur.elapsed_ms = int(m.group(1))
            self.cur.max_level = int(m.group(2))
            return

    def finish(self):
        if self.cur is not None:
            self.runs.append(self.cur)
            self.cur = None

    # ---- 汇总 ----
    def report(self):
        self.finish()
        completed = [r for r in self.runs if r.outcome in ("detected", "timeout")]
        det = [r for r in completed if r.outcome == "detected"]
        tmo = [r for r in completed if r.outcome == "timeout"]

        print()
        print("=" * 66)
        print("waiting_speech 误触发统计  (安静环境、无人说话时)")
        print("=" * 66)
        print("完成的交互轮数 : %d" % len(completed))
        print("  speech_detected : %d   <- 安静环境下这些都是误触发" % len(det))
        print("  timeout         : %d   <- 期望的正常结果" % len(tmo))
        if completed:
            rate = 100.0 * len(det) / len(completed)
            print("误触发率        : %.1f%%" % rate)
        print("-" * 66)

        if det:
            levels = sorted(r.max_level for r in det)
            elapsed = sorted(r.elapsed_ms for r in det)
            fast = [r for r in det
                    if r.armed_ms is not None
                    and (r.elapsed_ms - r.armed_ms) <= self.fast_ms]
            # 没有 armed 日志时，用 elapsed <= ARM_MS + fast_ms 近似
            if not any(r.armed_ms is not None for r in det):
                fast = [r for r in det if r.elapsed_ms <= ARM_MS + self.fast_ms]

            print("误触发 max_level : min=%d  中位=%d  max=%d   (阈值 450)"
                  % (levels[0], levels[len(levels) // 2], levels[-1]))
            print("触发时刻 elapsed : min=%d  中位=%d  max=%d  ms"
                  % (elapsed[0], elapsed[len(elapsed) // 2], elapsed[-1]))
            print("其中“快速触发”(armed 后 <=%d ms) : %d / %d"
                  % (self.fast_ms, len(fast), len(det)))
            print()
            if fast:
                print(">> 判读: 存在快速+安静误触发，强烈指向【提示音尾音/PA pop/回声】")
                print("         被 VAD 当成用户说话（即 AEC 关闭导致的输入污染）。")
            else:
                print(">> 判读: 有误触发但都较晚，可能是环境噪声而非提示音尾音；")
                print("         结合 vad_sim.py 对录音复核。")
            print()
            print("逐条误触发明细:")
            print("  run   prompt_end  armed  elapsed  max_level  prefix_bytes")
            for r in det:
                print("  %-5d %-11s %-6s %-8d %-10d %s" % (
                    r.idx,
                    ("%.0f" % r.prompt_end) if r.prompt_end is not None else "-",
                    ("%d" % r.armed_ms) if r.armed_ms is not None else "-",
                    r.elapsed_ms, r.max_level,
                    ("%d" % r.prefix_bytes) if r.prefix_bytes is not None else "-"))
        else:
            print("没有捕获到 speech_detected：本批次未复现自触发。")
            print("可增加触发次数，或把设备音量调到实际使用值再测。")
        print("=" * 66)


def run_serial(port, baud, tee, analyzer):
    try:
        import serial  # noqa
    except ImportError:
        sys.exit("需要 pyserial：pip install pyserial （或改用 --file / 管道输入）")
    ser = serial.Serial(port, baud, timeout=1)
    tee_fp = open(tee, "w", encoding="utf-8", errors="replace") if tee else None
    print("[i] 读取串口 %s @ %d，Ctrl-C 结束并汇总..." % (port, baud))
    try:
        buf = b""
        while True:
            data = ser.read(4096)
            if not data:
                continue
            buf += data
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode("utf-8", errors="replace").rstrip("\r")
                if tee_fp:
                    tee_fp.write(line + "\n"); tee_fp.flush()
                analyzer.feed(line)
    except KeyboardInterrupt:
        pass
    finally:
        if tee_fp:
            tee_fp.close()
        ser.close()


def run_stream(fp, analyzer):
    try:
        for line in fp:
            analyzer.feed(line.rstrip("\n").rstrip("\r"))
    except KeyboardInterrupt:
        pass


def main():
    ap = argparse.ArgumentParser(description="统计 waiting_speech 自触发")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--port", help="串口设备，如 /dev/ttyUSB0 或 COM5")
    src.add_argument("--file", help="已抓好的日志文件")
    src.add_argument("stdin", nargs="?", help="传 '-' 从标准输入读")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--tee", help="读串口时同时把原始日志存到该文件")
    ap.add_argument("--fast-ms", type=int, default=200,
                    help="armed 后多少 ms 内触发算“快速自触发”(默认 200)")
    args = ap.parse_args()

    analyzer = Analyzer(fast_ms=args.fast_ms)
    if args.port:
        run_serial(args.port, args.baud, args.tee, analyzer)
    elif args.file:
        with open(args.file, "r", encoding="utf-8", errors="replace") as fp:
            run_stream(fp, analyzer)
    else:  # stdin '-'
        run_stream(sys.stdin, analyzer)
    analyzer.report()


if __name__ == "__main__":
    main()
