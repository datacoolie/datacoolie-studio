"""Available machine/process resources, including visible Linux cgroup limits."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path, PurePosixPath


def available_cpu_count() -> int:
    counts = []
    if host_count := os.cpu_count():
        counts.append(host_count)
    process_count = getattr(os, "process_cpu_count", lambda: None)()
    if process_count:
        counts.append(process_count)
    try:
        counts.append(len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        if sys.platform == "win32":
            affinity = _windows_affinity_count()
            if affinity:
                counts.append(affinity)
    cpu_limits, _ = _cgroup_limits()
    return max(
        1, min((count for count in [*counts, *cpu_limits] if count > 0), default=1)
    )


def available_memory_bytes() -> int | None:
    physical = _physical_memory_bytes()
    _, limits = _cgroup_limits()
    if physical and physical > 0:
        limits.append(physical)
    return min(limits) if limits else None


def _windows_affinity_count() -> int | None:
    import ctypes

    try:
        kernel = ctypes.windll.kernel32
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        kernel.GetProcessAffinityMask.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_size_t),
            ctypes.POINTER(ctypes.c_size_t),
        )
        process_mask, system_mask = ctypes.c_size_t(), ctypes.c_size_t()
        if kernel.GetProcessAffinityMask(
            kernel.GetCurrentProcess(),
            ctypes.byref(process_mask),
            ctypes.byref(system_mask),
        ):
            return process_mask.value.bit_count() or None
    except (AttributeError, OSError, TypeError):
        pass
    return None


def _physical_memory_bytes() -> int | None:
    if sys.platform == "win32":
        import ctypes

        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                *[
                    (name, ctypes.c_ulonglong)
                    for name in (
                        "ullTotalPhys",
                        "ullAvailPhys",
                        "ullTotalPageFile",
                        "ullAvailPageFile",
                        "ullTotalVirtual",
                        "ullAvailVirtual",
                        "ullAvailExtendedVirtual",
                    )
                ],
            ]

        try:
            status = MemoryStatusEx()
            status.dwLength = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullTotalPhys) or None
        except (AttributeError, OSError, TypeError):
            pass
        return None
    try:
        size, pages = int(os.sysconf("SC_PAGE_SIZE")), int(os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, OSError, ValueError):
        return None
    return size * pages if size > 0 and pages > 0 else None


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return ""


def _is_directory(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _cgroup_directories():
    """Resolve mounted controller paths and all visible allocation ancestors."""
    memberships = []
    for line in _read_text(Path("/proc/self/cgroup")).splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3:
            memberships.append(
                (set(parts[1].split(",")) - {""}, PurePosixPath(parts[2]))
            )
    mounts = []
    for line in _read_text(Path("/proc/self/mountinfo")).splitlines():
        try:
            before, after = line.split(" - ", 1)
            fields, filesystem = before.split(), after.split()
            if filesystem[0] not in {"cgroup", "cgroup2"}:
                continue

            def unescape(text):
                return re.sub(
                    r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), text
                )

            root, mount = PurePosixPath(unescape(fields[3])), Path(unescape(fields[4]))
            controllers = set(filesystem[2].split(",")) | set(filesystem[1].split(","))
        except (ValueError, IndexError):
            continue
        mounts.append((root, mount, controllers, filesystem[0] == "cgroup2"))
    for member_controllers, member in memberships:
        if not member.is_absolute() or ".." in member.parts:
            continue
        direct, namespaced = [], []
        for root, mount, controllers, unified in mounts:
            if (unified and member_controllers) or (
                not unified and not member_controllers.intersection(controllers)
            ):
                continue
            try:
                relative = member.relative_to(root)
                mappings = direct
            except ValueError:
                # A cgroup namespace reports membership relative to its own root.
                relative = member.relative_to("/")
                mappings = namespaced
            directory = mount.joinpath(*relative.parts)
            if _is_directory(directory):
                mappings.append((directory, mount, unified))
        # Unrelated sibling bind mounts must not inject their root limits when
        # the actual membership is already resolvable in the mounted hierarchy.
        for directory, mount, unified in direct or namespaced:
            while True:
                yield directory, unified, member_controllers
                if directory == mount:
                    break
                directory = directory.parent


def _positive_integer(text: str) -> int | None:
    try:
        value = int(text)
        return value if value > 0 else None
    except ValueError:
        return None


def _cpuset_count(text: str) -> int | None:
    try:
        ranges = []
        for part in text.split(","):
            bounds = part.split("-")
            if len(bounds) not in (1, 2):
                return None
            start, end = int(bounds[0]), int(bounds[-1])
            if start < 0 or end < start:
                return None
            ranges.append((start, end))
        count, previous = 0, -1
        for start, end in sorted(ranges):
            count += max(0, end - max(start, previous + 1) + 1)
            previous = max(previous, end)
        return count or None
    except ValueError:
        return None


def _cgroup_limits() -> tuple[list[int], list[int]]:
    cpu_limits, memory_limits = [], []
    if not sys.platform.startswith("linux"):
        return cpu_limits, memory_limits
    for directory, unified, controllers in _cgroup_directories():
        if unified or "cpu" in controllers:
            values = (
                _read_text(directory / "cpu.max").split()
                if unified
                else [
                    _read_text(directory / "cpu.cfs_quota_us"),
                    _read_text(directory / "cpu.cfs_period_us"),
                ]
            )
            if len(values) == 2:
                quota, period = map(_positive_integer, values)
                if quota and period:
                    cpu_limits.append(max(1, quota // period))
        if unified or "cpuset" in controllers:
            names = (
                ("cpuset.cpus.effective", "cpuset.cpus")
                if unified
                else ("cpuset.effective_cpus", "cpuset.cpus")
            )
            text = _read_text(directory / names[0]) or _read_text(directory / names[1])
            count = _cpuset_count(text)
            if count:
                cpu_limits.append(count)
        if unified or "memory" in controllers:
            value = _positive_integer(
                _read_text(
                    directory / ("memory.max" if unified else "memory.limit_in_bytes")
                )
            )
            # v1 uses near-LONG_MAX values for unlimited memory; v2 says 'max'.
            if value and (unified or value < 1 << 60):
                memory_limits.append(value)
    return cpu_limits, memory_limits
