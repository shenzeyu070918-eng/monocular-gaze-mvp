"""Camera reads with a first-frame deadline and interruptible bounded retries.

The caller owns and releases the camera. This iterator never releases a handle
to interrupt a read; a stop request takes effect after the blocking read returns.
"""
import threading
import time


def read_camera_frames(camera, options, *, stop=None, clock_ns=time.perf_counter_ns,
                       monotonic=time.monotonic):
    """Yield (image, read_return_ns), excluding failed reads from frame counts.

    Warmup starts on iteration, immediately before the first read attempt. A
    valid first frame ends warmup early. Thereafter read_retry_count is the
    number of additional attempts allowed after an initial failed read; any
    successful frame resets that consecutive-failure budget.
    """
    stop = stop if stop is not None else threading.Event()
    deadline = monotonic() + options.warmup_timeout_s
    seen_frame, failures = False, 0

    def no_frame():
        return RuntimeError('从未读到画面：摄像头预热等待超时；请检查「系统设置 → 隐私与安全性 → 摄像头」'
                            '中终端的权限，并关闭占用摄像头的网页或其他程序后重试。')

    while not stop.is_set():
        if not seen_frame and failures and monotonic() >= deadline:
            raise no_frame()
        ok, image = camera.read()
        captured_ns = clock_ns()  # Immediately after OpenCV read() returns.
        if stop.is_set():
            return
        if ok and image is not None and image.size > 0:
            seen_frame, failures = True, 0
            yield image, captured_ns
            continue
        failures += 1
        if seen_frame:
            if failures > options.read_retry_count:
                raise RuntimeError(f'运行中途读帧失败：连续 {failures} 次未取得有效画面，'
                                   f'已用尽 {options.read_retry_count} 次重试。')
            delay = options.read_retry_interval_s
        else:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise no_frame()
            delay = min(options.read_retry_interval_s, remaining)
        if stop.wait(delay):
            return
