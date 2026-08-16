# -*- coding: utf-8 -*-
"""扫描所有输入设备录音音量，找出真正可用的麦克风。"""
import sounddevice as sd
import numpy as np

def main():
    devs = [d for d in sd.query_devices() if d["max_input_channels"] > 0]
    print("SAY SOMETHING CONTINUOUSLY for the whole scan (~30s)...", flush=True)
    results = []
    for d in devs:
        idx = int(d["index"])
        sr = int(d["default_samplerate"])
        try:
            a = sd.rec(int(2.5 * sr), samplerate=sr, channels=1, dtype="int16", device=idx)
            sd.wait()
            x = a.flatten().astype("float32")
            rms = float(np.sqrt(np.mean(x * x)))
            peak = float(np.max(np.abs(x)))
            results.append((rms, peak, idx, d["name"]))
            print("dev %2d RMS=%8.1f PEAK=%6.0f | %s" % (idx, rms, peak, d["name"][:48]), flush=True)
        except Exception as e:
            print("dev %2d ERR %s" % (idx, str(e)[:70]), flush=True)
    results.sort(reverse=True)
    if results:
        rms, peak, idx, name = results[0]
        print("BEST: dev %d RMS=%.1f PEAK=%.0f (%s)" % (idx, rms, peak, name))

if __name__ == "__main__":
    main()
