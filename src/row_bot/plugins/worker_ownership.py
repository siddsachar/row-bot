"""Stdlib native lifetime containment shared by prepared plugin subprocesses.

Job objects/process groups own descendants; they are not OS access sandboxes.
"""
from __future__ import annotations

import threading
import time

class WindowsJob:
    """Private noninheritable kill-on-close job; no breakaway flags are allowed."""

    def __init__(self, process):
        import ctypes
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [("user_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                ("flags", wintypes.DWORD), ("minimum", ctypes.c_size_t), ("maximum", ctypes.c_size_t),
                ("active_limit", wintypes.DWORD), ("affinity", ctypes.c_size_t),
                ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]

        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", ctypes.c_ulonglong * 6),
                ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]

        class Accounting(ctypes.Structure):
            _fields_ = [("times", ctypes.c_longlong * 4), ("faults", wintypes.DWORD),
                ("total", wintypes.DWORD), ("active", wintypes.DWORD), ("terminated", wintypes.DWORD)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.TerminateJobObject.restype = wintypes.BOOL
        kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        kernel.QueryInformationJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self._kernel, self._ctypes, self._accounting = kernel, ctypes, Accounting
        self._stopped = False
        self._handle = kernel.CreateJobObjectW(None, None)
        if not self._handle:
            raise RuntimeError("worker_ownership_unavailable")
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if (not kernel.SetInformationJobObject(self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits))
                or not kernel.AssignProcessToJobObject(self._handle, int(process._handle))):
            kernel.CloseHandle(self._handle)
            self._handle = None
            raise RuntimeError("worker_ownership_unavailable")

    def close(self) -> bool:
        if self._handle is None:
            return self._stopped
        handle = self._handle
        self._handle = None
        stopped = False
        try:
            if not self._kernel.TerminateJobObject(handle, 130):
                return False
            deadline = time.monotonic() + 3
            while True:
                info = self._accounting()
                if not self._kernel.QueryInformationJobObject(handle, 1, self._ctypes.byref(info), self._ctypes.sizeof(info), None):
                    break
                if info.active == 0:
                    stopped = True
                    break
                if time.monotonic() >= deadline:
                    break
                threading.Event().wait(0.01)
        finally:
            self._kernel.CloseHandle(handle)  # Also kills any remaining members.
        self._stopped = stopped
        return stopped
