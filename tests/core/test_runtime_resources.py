from __future__ import annotations

import pytest

from datacoolie_studio.core import config, runtime_resources as resources


@pytest.fixture
def linux_files(monkeypatch):
    files = {}
    monkeypatch.setattr(resources.sys, "platform", "linux")
    monkeypatch.setattr(
        resources, "_read_text", lambda path: files.get(path.as_posix(), "")
    )
    monkeypatch.setattr(
        resources,
        "_is_directory",
        lambda path: any(name.startswith(path.as_posix() + "/") for name in files),
    )
    monkeypatch.setattr(resources, "_physical_memory_bytes", lambda: 64 * 1024**3)
    monkeypatch.setattr(resources.os, "cpu_count", lambda: 64)
    monkeypatch.setattr(resources.os, "process_cpu_count", lambda: None, raising=False)
    monkeypatch.setattr(
        resources.os, "sched_getaffinity", lambda _: set(range(64)), raising=False
    )
    return files


def unified(files, membership="/team/worker", root="/"):
    files["/proc/self/cgroup"] = f"0::{membership}"
    files["/proc/self/mountinfo"] = f"30 20 0:30 {root} /cg rw - cgroup2 cgroup rw"


def test_native_resource_allocation_has_no_fixed_cpu_or_ram_cap(linux_files):
    assert resources.available_cpu_count() == 64
    assert resources.available_memory_bytes() == 64 * 1024**3
    assert config._default_analytics_duckdb_memory_limit() == "45875MiB"


def test_cgroup_v2_limits_apply_parent_quota_affinity_and_memory(
    linux_files, monkeypatch
):
    unified(linux_files)
    linux_files.update(
        {
            "/cg/team/worker/cpu.max": "max 100000",
            "/cg/team/cpu.max": "250000 100000",
            "/cg/team/worker/cpuset.cpus.effective": "0-7",
            "/cg/team/worker/memory.max": "max",
            "/cg/team/memory.max": str(512 * 1024**2),
        }
    )
    assert resources.available_cpu_count() == 2
    assert resources.available_memory_bytes() == 512 * 1024**2
    assert config._default_analytics_duckdb_memory_limit() == "358MiB"
    monkeypatch.setattr(resources.os, "sched_getaffinity", lambda _: {1}, raising=False)
    assert resources.available_cpu_count() == 1
    monkeypatch.setattr(resources, "_physical_memory_bytes", lambda: None)
    assert config._default_analytics_duckdb_memory_limit() == "358MiB"


@pytest.mark.parametrize("membership", ["/tenant/worker", "/worker"])
def test_mount_root_and_cgroup_namespace_paths(linux_files, membership):
    unified(linux_files, membership=membership, root="/tenant")
    linux_files["/cg/worker/cpu.max"] = "50000 100000"
    linux_files["/cg/memory.max"] = str(256 * 1024**2)
    assert resources.available_cpu_count() == 1
    assert resources.available_memory_bytes() == 256 * 1024**2


def test_v1_separate_controller_mounts_parent_limits_and_unlimited(linux_files):
    linux_files["/proc/self/cgroup"] = (
        "2:cpu,cpuacct:/group/task\n3:memory:/group/task\n4:cpuset:/group/task"
    )
    linux_files["/proc/self/mountinfo"] = "\n".join(
        [
            "30 20 0:30 / /cpu rw - cgroup cgroup rw,cpu,cpuacct",
            "31 20 0:31 / /ram rw - cgroup cgroup rw,memory",
            "32 20 0:32 / /set rw - cgroup cgroup rw,cpuset",
        ]
    )
    linux_files.update(
        {
            "/cpu/group/task/cpu.cfs_quota_us": "-1",
            "/cpu/group/task/cpu.cfs_period_us": "100000",
            "/cpu/group/cpu.cfs_quota_us": "350000",
            "/cpu/group/cpu.cfs_period_us": "100000",
            "/ram/group/task/memory.limit_in_bytes": str(2**63 - 4096),
            "/ram/group/memory.limit_in_bytes": str(4 * 1024**3),
            "/set/group/task/cpuset.cpus": "1,3-4",
        }
    )
    assert resources.available_cpu_count() == 3
    assert resources.available_memory_bytes() == 4 * 1024**3


def test_unlimited_invalid_and_unreadable_sources_use_remaining_evidence(
    linux_files, monkeypatch
):
    unified(linux_files)
    linux_files.update(
        {
            "/cg/team/worker/cpu.max": "10000 0",
            "/cg/team/worker/memory.max": "max",
            "/cg/team/cpu.max": "bad data",
            "/cg/team/memory.max": "0",
        }
    )
    assert resources.available_cpu_count() == 64
    assert resources.available_memory_bytes() == 64 * 1024**3
    monkeypatch.setattr(resources, "_physical_memory_bytes", lambda: None)
    assert config._default_analytics_duckdb_memory_limit() == "1GB"
    monkeypatch.setattr(resources.os, "cpu_count", lambda: None)
    monkeypatch.setattr(
        resources.os, "sched_getaffinity", lambda _: set(), raising=False
    )
    assert resources.available_cpu_count() == 1
    linux_files["/cg/team/cpu.max"] = "200000 100000"
    assert resources.available_cpu_count() == 2


def test_mount_escaped_space_and_root_namespace(linux_files):
    linux_files["/proc/self/cgroup"] = "0::/"
    linux_files["/proc/self/mountinfo"] = (
        r"30 20 0:30 /tenant /cg\040mount rw - cgroup2 cgroup rw"
    )
    linux_files["/cg mount/cpu.max"] = "700000 100000"
    assert resources.available_cpu_count() == 7


def test_malformed_membership_cannot_escape_mount(linux_files):
    unified(linux_files, membership="/../../other")
    linux_files["/other/memory.max"] = "32"
    assert list(resources._cgroup_directories()) == []
    assert resources.available_memory_bytes() == 64 * 1024**3


def test_sibling_bind_mount_cannot_override_resolved_membership(linux_files):
    unified(linux_files, membership="/active/task")
    linux_files["/proc/self/mountinfo"] += (
        "\n31 20 0:30 /other /bound rw - cgroup2 cgroup rw"
    )
    linux_files.update(
        {
            "/cg/active/task/memory.max": str(512 * 1024**2),
            "/bound/memory.max": str(32 * 1024**2),
            # Even a coincidentally named leaf does not make a sibling relevant.
            "/bound/active/task/memory.max": str(16 * 1024**2),
        }
    )
    assert resources.available_memory_bytes() == 512 * 1024**2


def test_matching_bind_mount_retains_visible_ancestor_limits(linux_files):
    unified(linux_files, membership="/active/task")
    linux_files["/proc/self/mountinfo"] += (
        "\n31 20 0:30 /active/task /bound rw - cgroup2 cgroup rw"
    )
    linux_files.update(
        {
            "/cg/active/task/memory.max": str(512 * 1024**2),
            "/cg/active/memory.max": str(256 * 1024**2),
            "/bound/memory.max": str(512 * 1024**2),
        }
    )
    assert resources.available_memory_bytes() == 256 * 1024**2


def test_namespace_mapping_requires_existing_membership_leaf(linux_files):
    unified(linux_files, membership="/missing", root="/tenant")
    linux_files["/cg/memory.max"] = str(32 * 1024**2)
    assert list(resources._cgroup_directories()) == []
    assert resources.available_memory_bytes() == 64 * 1024**3


def test_cpu_process_limit_is_respected_without_affinity(linux_files, monkeypatch):
    monkeypatch.setattr(resources.os, "process_cpu_count", lambda: 12, raising=False)
    monkeypatch.delattr(resources.os, "sched_getaffinity", raising=False)
    assert resources.available_cpu_count() == 12


@pytest.mark.parametrize(
    "value,expected",
    [
        ("", None),
        ("0-3,6,8-9", 7),
        ("0-3,2-4", 5),
        ("-1", None),
        ("3-1", None),
        ("x", None),
    ],
)
def test_cpuset_parsing(value, expected):
    assert resources._cpuset_count(value) == expected


def test_proc_read_failure_is_optional(tmp_path):
    assert resources._read_text(tmp_path / "missing") == ""
    assert resources._read_text(tmp_path) == ""
