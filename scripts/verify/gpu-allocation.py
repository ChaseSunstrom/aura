#!/usr/bin/env python3
"""Prove a container can really *use* the GPUs, not merely see the device nodes.

`nvidia-smi` inside a container only proves the device node and the management
library are reachable. It does not prove the CUDA driver will hand out a context
or a byte of VRAM: a mismatched userspace library, a missing /dev/nvidia-uvm or a
card with no headroom all pass `nvidia-smi` and then fail the first allocation.

So this drives the CUDA driver API through ctypes -- cuInit, cuDeviceGet,
cuCtxCreate, cuMemAlloc, cuMemFree -- which is the same path any framework takes
and needs no torch, no CUDA toolkit and no multi-gigabyte image. Exit status is
the gate: 0 only if every visible device actually allocated.
"""
from __future__ import annotations

import ctypes
import sys

#: Allocation size per device. Small on purpose -- this shares the cards with
#: vLLM, and the question is "does an allocation succeed", not "how much is left".
PROBE_BYTES = 64 * 1024 * 1024

CUDA_SUCCESS = 0


def _err(cuda: ctypes.CDLL, code: int) -> str:
    name = ctypes.c_char_p()
    cuda.cuGetErrorName(code, ctypes.byref(name))
    return (name.value or b"?").decode()


def main() -> int:
    try:
        cuda = ctypes.CDLL("libcuda.so.1")
    except OSError as exc:
        print(f"FAIL: libcuda.so.1 not loadable in this container: {exc}")
        return 1

    rc = cuda.cuInit(0)
    if rc != CUDA_SUCCESS:
        print(f"FAIL: cuInit -> {_err(cuda, rc)} ({rc})")
        return 1

    count = ctypes.c_int()
    rc = cuda.cuDeviceGetCount(ctypes.byref(count))
    if rc != CUDA_SUCCESS:
        print(f"FAIL: cuDeviceGetCount -> {_err(cuda, rc)} ({rc})")
        return 1
    if count.value == 0:
        print("FAIL: CUDA reports 0 devices")
        return 1

    ver = ctypes.c_int()
    cuda.cuDriverGetVersion(ctypes.byref(ver))
    print(f"CUDA driver version: {ver.value // 1000}.{(ver.value % 1000) // 10}")
    print(f"devices visible: {count.value}")

    failed = 0
    for i in range(count.value):
        dev = ctypes.c_int()
        if cuda.cuDeviceGet(ctypes.byref(dev), i) != CUDA_SUCCESS:
            print(f"  device {i}: FAIL cuDeviceGet")
            failed += 1
            continue

        name = ctypes.create_string_buffer(96)
        cuda.cuDeviceGetName(name, 96, dev)

        ctx = ctypes.c_void_p()
        rc = cuda.cuCtxCreate_v2(ctypes.byref(ctx), 0, dev)
        if rc != CUDA_SUCCESS:
            print(f"  device {i} ({name.value.decode()}): FAIL cuCtxCreate -> {_err(cuda, rc)}")
            failed += 1
            continue

        try:
            free, total = ctypes.c_size_t(), ctypes.c_size_t()
            cuda.cuMemGetInfo_v2(ctypes.byref(free), ctypes.byref(total))

            ptr = ctypes.c_void_p()
            rc = cuda.cuMemAlloc_v2(ctypes.byref(ptr), ctypes.c_size_t(PROBE_BYTES))
            if rc != CUDA_SUCCESS:
                print(
                    f"  device {i} ({name.value.decode()}): FAIL cuMemAlloc "
                    f"{PROBE_BYTES // 1024 // 1024} MiB -> {_err(cuda, rc)} "
                    f"(free {free.value // 1024 // 1024} MiB)"
                )
                failed += 1
                continue
            cuda.cuMemFree_v2(ptr)

            print(
                f"  device {i} ({name.value.decode()}): OK  "
                f"allocated {PROBE_BYTES // 1024 // 1024} MiB, "
                f"free {free.value // 1024 // 1024} / {total.value // 1024 // 1024} MiB"
            )
        finally:
            cuda.cuCtxDestroy_v2(ctx)

    if failed:
        print(f"FAIL: {failed} of {count.value} device(s) could not allocate")
        return 1
    print(f"PASS: all {count.value} device(s) allocated and freed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
