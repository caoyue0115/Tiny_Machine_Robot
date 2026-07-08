#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze_hang_logs.py —— 从设备串口日志里判定“语音交互放一半卡死”的真实结局。

背景（已对着 main/audio_out.c、main/main.c 核实过）：
  播放任务 audio_stream_task 在写 codec 时是持递归锁的
  （:657 -> audio_out_write_pcm_chunk :825 audio_out_lock -> :540 阻塞的
   esp_codec_dev_write -> :836 unlock）。关闭函数
   audio_out_close_pcm_stream_with_metrics 的“超时分支”在 **没持锁** 的情况下
   vTaskDelete 掉这个可能正持锁的播放任务（:993），紧接着 :995 又去
   audio_out_lock()（portMAX_DELAY 递归锁）。若删掉的正是持锁任务 =>
   孤儿锁 => :995 永久阻塞 => 整条 pipeline 冻住 => 再也触发不了，且
   CONFIG_ESP_TASK_WDT_PANIC 未开 + 阻塞不喂狗 => 不重启。

因此每一轮交互的结局只可能是下面几种，本脚本自动判定并统计：

  normal-end            正常放完并收尾（pipeline task finished，result=ESP_OK）
  upstream-truncation   上游流截断 / 写失败，但仍正常收尾（不是死锁）
  forced-delete-ok      走了危险的超时强删分支，但这次侥幸没孤儿锁、收了尾
                        （危险信号：说明超时分支已被触发，只是没抽中持锁瞬间）
  B-DEADLOCK            超时强删后彻底静默、无收尾、无重启
                        => 孤儿锁死锁（audio_out.c:995）——你分析的那条
  A-HANG                只有 close waiting、没等到 forcing delete 就静默
                        => 关闭卡在 :942 拿锁上，codec 写“一次性”彻底不返回
                        （根因在 esp_codec_dev_write / I2S / PA，不是孤儿锁）
  crash-reboot          出现 Guru Meditation / Backtrace / rst:0x / abort 等
                        => 崩溃重启（不是卡死）

用法：
  # 读已抓好的日志文件（最常用：复现一次卡死、Ctrl-C 停止抓取后跑这个）
  python3 analyze_hang_logs.py --file hang.log

  # 边读串口边判定，并同时存档（需 pyserial: pip install pyserial）
  python3 analyze_hang_logs.py --port COM5 --baud 115200 --tee hang.log

  # 从管道读
  idf.py monitor | python3 analyze_hang_logs.py -

判定“卡死”依赖“最后一轮起了头但既没收尾也没重启，然后日志到此为止”。
所以复现卡死后，让它静默几秒再停止抓取，末尾的静默就是死锁指纹。
"""

import argparse
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CLOSE_TIMEOUT_MS = 5000  # 与固件 DEMO_REALTIME_AUDIO_CLOSE_WAIT_TIMEOUT_MS 一致

# ---- 一轮交互的边界 ----
RE_ROUND_START = re.compile(r"pipeline task started for trigger=(\S+)")
RE_ROUND_END   = re.compile(r"pipeline task finished")

# ---- 播放 / 关闭阶段 ----
RE_PLAY_DONE   = re.compile(r"Realtime jitter playback task done\b.*?result=(\S+)")
RE_CLOSE_WAIT  = re.compile(r"Realtime jitter close waiting\b")
RE_TAIL_ACCEPT = re.compile(r"Realtime jitter close accepting tail remainder\b")
RE_FORCE_DEL   = re.compile(r"Realtime jitter close timeout forcing task delete\b.*?wait_ms=([\d.]+)")
RE_WRITE_FAIL  = re.compile(r"Buffered PCM write failed:")

# ---- 崩溃 / 重启指纹 ----
RE_CRASH = re.compile(
    r"Guru Meditation|Backtrace:|abort\(\) was called|assert failed|"
    r"rst:0x[0-9a-fA-F]|ESP-ROM:|CORRUPT HEAP|StoreProhibited|LoadProhibited|"
    r"Rebooting\.\.\.|task_wdt: Task watchdog got triggered")


class Round:
    __slots__ = ("idx", "trigger", "finished", "play_result",
                 "close_wait", "tail_accept", "force_delete", "force_wait_ms",
                 "write_fail", "crash", "crash_snip")

    def __init__(self, idx, trigger):
        self.idx = idx
        self.trigger = trigger
        self.finished = False
        self.play_result = None
        self.close_wait = 0
        self.tail_accept = False
        self.force_delete = False
        self.force_wait_ms = None
        self.write_fail = False
        self.crash = False
        self.crash_snip = None

    def outcome(self, is_last):
        # 崩溃优先
        if self.crash:
            return "crash-reboot"
        if self.finished:
            if self.force_delete:
                return "forced-delete-ok"
            if self.write_fail or (self.play_result not in (None, "ESP_OK")):
                return "upstream-truncation"
            return "normal-end"
        # 没收尾：只有最后一轮的“未收尾”才可能是卡死；
        # 中间轮未收尾但后面又起了新一轮，通常是日志缺行，标 incomplete。
        if self.force_delete:
            return "B-DEADLOCK" if is_last else "forced-delete-nofinish"
        if self.close_wait > 0:
            return "A-HANG" if is_last else "close-nofinish"
        return "incomplete"


OUTCOME_DESC = {
    "normal-end":            "正常放完并收尾",
    "upstream-truncation":   "上游截断/写失败但仍收尾（非死锁）",
    "forced-delete-ok":      "触发了危险超时强删分支，但侥幸没孤儿锁、收了尾（危险信号）",
    "forced-delete-nofinish":"超时强删后未收尾，但后面又有新一轮（疑似日志缺行）",
    "close-nofinish":        "关闭中未收尾，但后面又有新一轮（疑似日志缺行）",
    "B-DEADLOCK":            ">>> 孤儿锁死锁：超时强删持锁播放任务，:995 永久阻塞 <<<",
    "A-HANG":                ">>> codec 写彻底不返回：关闭卡在 :942 拿锁 <<<",
    "crash-reboot":          "崩溃重启（不是卡死，另一条路）",
    "incomplete":            "起了头但信息不足/被截断",
}


class Analyzer:
    def __init__(self):
        self.rounds = []
        self.cur = None
        self.preamble_crash = None  # 第一轮之前就出现的崩溃指纹（例如上电 rst 之外的）

    def _push(self):
        if self.cur is not None:
            self.rounds.append(self.cur)

    def feed(self, line):
        mstart = RE_ROUND_START.search(line)
        if mstart:
            self._push()
            self.cur = Round(len(self.rounds) + 1, mstart.group(1))
            return

        # 崩溃指纹：归属当前轮；若还没有轮，记到 preamble
        if RE_CRASH.search(line):
            snip = line.strip()[:80]
            if self.cur is not None:
                self.cur.crash = True
                if self.cur.crash_snip is None:
                    self.cur.crash_snip = snip
            else:
                self.preamble_crash = snip
            return

        if self.cur is None:
            return  # 第一轮起点之前的普通日志忽略

        if RE_ROUND_END.search(line):
            self.cur.finished = True
            return
        m = RE_PLAY_DONE.search(line)
        if m:
            self.cur.play_result = m.group(1)
            return
        if RE_FORCE_DEL.search(line):
            self.cur.force_delete = True
            mm = RE_FORCE_DEL.search(line)
            self.cur.force_wait_ms = mm.group(1)
            return
        if RE_CLOSE_WAIT.search(line):
            self.cur.close_wait += 1
            return
        if RE_TAIL_ACCEPT.search(line):
            self.cur.tail_accept = True
            return
        if RE_WRITE_FAIL.search(line):
            self.cur.write_fail = True
            return

    def finish(self):
        self._push()
        self.cur = None

    def report(self):
        self.finish()
        rounds = self.rounds
        n = len(rounds)

        print()
        print("=" * 72)
        print("语音交互卡死判定  (放一半停 / 不重启 / 再也触发不了)")
        print("=" * 72)
        if not rounds:
            print("没有解析到任何一轮 (未见 'pipeline task started')。")
            if self.preamble_crash:
                print("但日志里有崩溃指纹: %s" % self.preamble_crash)
            print("确认抓的是设备串口输出，且日志覆盖了一次触发。")
            print("=" * 72)
            return

        results = []
        for i, r in enumerate(rounds):
            is_last = (i == n - 1)
            results.append((r, r.outcome(is_last)))

        # 统计
        tally = {}
        for _, o in results:
            tally[o] = tally.get(o, 0) + 1

        print("解析到交互轮数 : %d" % n)
        for o in ("normal-end", "upstream-truncation", "forced-delete-ok",
                  "forced-delete-nofinish", "close-nofinish",
                  "A-HANG", "B-DEADLOCK", "crash-reboot", "incomplete"):
            if o in tally:
                print("  %-22s : %d   %s" % (o, tally[o], OUTCOME_DESC[o]))
        print("-" * 72)

        # 逐轮明细
        print("逐轮明细:")
        print("  #    trigger        close_wait  force_del  play_result   outcome")
        for r, o in results:
            print("  %-4d %-14s %-11d %-10s %-13s %s" % (
                r.idx,
                (r.trigger or "-")[:14],
                r.close_wait,
                ("wait=%s" % r.force_wait_ms) if r.force_delete else "-",
                r.play_result or "-",
                o))
        print("-" * 72)

        # 最后一轮的结论（卡死一般发生在这里）
        last_r, last_o = results[-1]
        print("最后一轮 (#%d) 结局: %s" % (last_r.idx, last_o))
        if last_o == "B-DEADLOCK":
            print("  实锤你之前的分析：超时(5s)后 forcing task delete 删掉了正持锁的")
            print("  播放任务 => 孤儿递归锁 => audio_out.c:995 audio_out_lock 永久阻塞。")
            print("  修复方向: (1) 别 vTaskDelete 持锁/持 I2S 的任务，改标志位+带超时 join；")
            print("            (2) audio_out_lock 用有限超时替代 portMAX_DELAY 兜底。")
        elif last_o == "A-HANG":
            print("  不是孤儿锁，是 esp_codec_dev_write 一次性彻底不返回：")
            print("  关闭函数卡在 audio_out.c:942 拿锁上（锁被仍活着但卡住的播放任务持有）。")
            print("  修复方向: 查 I2S/DMA/PA 为何不返回；关闭前先 esp_codec_dev_close 打断写。")
        elif last_o == "crash-reboot":
            print("  是崩溃重启，不是卡死。看 Backtrace / rst 原因，走另一条排查路径。")
            if last_r.crash_snip:
                print("  指纹: %s" % last_r.crash_snip)
        elif last_o in ("normal-end", "upstream-truncation", "forced-delete-ok"):
            print("  这份日志最后一轮是正常收尾的——没抓到卡死那一刻。")
            print("  复现卡死后，务必让设备再静默几秒再停止抓取，末尾静默才是死锁指纹。")
        else:
            print("  信息不足。请确认卡死后日志末尾是“静默无新行”，且抓全了 close 阶段。")
        print("=" * 72)


def run_serial(port, baud, tee, analyzer):
    try:
        import serial  # noqa
    except ImportError:
        sys.exit("需要 pyserial：pip install pyserial （或改用 --file / 管道输入）")
    ser = serial.Serial(port, baud, timeout=1)
    tee_fp = open(tee, "w", encoding="utf-8", errors="replace") if tee else None
    print("[i] 读取串口 %s @ %d，复现卡死后 Ctrl-C 结束并判定..." % (port, baud))
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
    ap = argparse.ArgumentParser(description="判定语音交互卡死是 A型/B型死锁 还是 崩溃重启")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--port", help="串口设备，如 COM5 或 /dev/ttyUSB0")
    src.add_argument("--file", help="已抓好的日志文件")
    src.add_argument("stdin", nargs="?", help="传 '-' 从标准输入读")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--tee", help="读串口时同时把原始日志存到该文件")
    args = ap.parse_args()

    analyzer = Analyzer()
    if args.port:
        run_serial(args.port, args.baud, args.tee, analyzer)
    elif args.file:
        with open(args.file, "r", encoding="utf-8", errors="replace") as fp:
            run_stream(fp, analyzer)
    else:
        run_stream(sys.stdin, analyzer)
    analyzer.report()


if __name__ == "__main__":
    main()
