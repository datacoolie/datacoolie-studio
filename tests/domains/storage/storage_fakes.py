from __future__ import annotations

import io
import threading
from datetime import datetime, timezone
from types import SimpleNamespace
class _FakeFilesystem:
    payload = b'{"ok":true}'

    @staticmethod
    def _strip_protocol(uri: str) -> str:
        return uri.split("://", 1)[-1]

    def ls(self, path: str, detail: bool = True):
        assert detail
        return [self.info(f"{path.rstrip('/')}/data.json")]

    def find(self, path: str, detail: bool = True, withdirs: bool = True):
        assert detail and withdirs
        child = f"{path.rstrip('/')}/data.json"
        return {child: self.info(child)}

    def info(self, path: str):
        return {
            "name": path,
            "type": "file",
            "size": len(self.payload),
            "LastModified": datetime(2026, 7, 23, tzinfo=timezone.utc),
            "VersionId": "version-7",
            "ETag": '"etag-fallback"',
        }

    def open(self, path: str, mode: str):
        assert path == "bucket/prefix/data.json"
        assert mode == "rb"
        return io.BytesIO(self.payload)


class _FakeAdlsFilesystem:
    @staticmethod
    def _strip_protocol(uri: str) -> str:
        return uri.split("://", 1)[-1]

    def ls(self, path: str, detail: bool = True):
        return [self.info("test/functions/sources.py")]

    def find(self, path: str, detail: bool = True, withdirs: bool = True):
        return {"test/functions/sources.py": self.info("test/functions/sources.py")}

    @staticmethod
    def info(path: str):
        return {
            "name": path,
            "type": "file",
            "size": 32,
            "last_modified": datetime(2026, 7, 27, tzinfo=timezone.utc),
            "etag": "adls-etag",
        }


class _FakeS3Client:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def put_object(self, **kwargs):
        self.calls.append(kwargs)
        return {"VersionId": "version-new", "ETag": '"etag-new"'}


class _FakeGcsBlob:
    generation = 18
    metageneration = 3

    def __init__(self) -> None:
        self.last_precondition = None

    def upload_from_string(self, _content: bytes, *, if_generation_match: int):
        self.last_precondition = if_generation_match


class _FakeGcsBucket:
    def __init__(self) -> None:
        self.last_blob = _FakeGcsBlob()
        self.last_key: str | None = None

    def blob(self, key: str):
        self.last_key = key
        self.last_blob = _FakeGcsBlob()
        return self.last_blob


class _FakeDatabricksFiles:
    def __init__(self) -> None:
        self.listed: list[str] = []

    def list_directory_contents(self, path: str):
        self.listed.append(path)
        if path.endswith("/project"):
            return [
                {
                    "path": f"{path}/metadata",
                    "is_directory": True,
                    "last_modified": 1_753_248_000_000,
                }
            ]
        return [
            {
                "path": f"{path}/metadata.json",
                "is_directory": False,
                "file_size": 11,
                "last_modified": 1_753_248_000_000,
                "etag": "volume-etag",
            }
        ]

    def download(self, _path: str):
        return type(
            "Download",
            (),
            {
                "contents": io.BytesIO(b'{"ok":true}'),
                "content_length": 11,
                "last_modified": "Wed, 23 Jul 2025 05:20:00 GMT",
            },
        )()


class _FakeDatabricksDbfs:
    payload = b"def transform():\n    return 1\n"

    def __init__(self) -> None:
        self.status_paths: list[str] = []

    def get_status(self, path: str):
        self.status_paths.append(path)
        return {
            "path": path,
            "is_dir": False,
            "file_size": len(self.payload),
            "modification_time": 1_753_248_000_000,
        }

    def list(self, _path: str):
        return []

    def download(self, _path: str):
        return io.BytesIO(self.payload)


class _FakeDatabricksClient:
    def __init__(self) -> None:
        self.files = _FakeDatabricksFiles()
        self.dbfs = _FakeDatabricksDbfs()


class _FakeOneLakePager:
    def __init__(self, pages):
        self._pages = pages

    def by_page(self):
        return iter(self._pages)


class _FakeOneLakeDownloader:
    def __init__(self, payload: bytes, properties) -> None:
        self._payload = payload
        self.properties = properties

    def chunks(self):
        yield self._payload[:5]
        yield self._payload[5:]


class _FakeOneLakeFileClient:
    def __init__(self, filesystem, path: str) -> None:
        self._filesystem = filesystem
        self._path = path

    def get_file_properties(self):
        return self._filesystem.properties(self._path)

    def download_file(self, *, max_concurrency: int):
        self._filesystem.download_concurrency.append(max_concurrency)
        return _FakeOneLakeDownloader(
            self._filesystem.payload,
            self._filesystem.properties(self._path),
        )


class _FakeOneLakeFilesystem:
    payload = b'{"onelake":true}'

    def __init__(self) -> None:
        self.listed: list[str] = []
        self.page_limits: list[int | None] = []
        self.download_concurrency: list[int] = []

    def get_paths(
        self, *, path: str, recursive: bool, max_results: int | None
    ):
        assert recursive is False
        self.listed.append(path)
        self.page_limits.append(max_results)
        if path.endswith("/project"):
            return _FakeOneLakePager(
                [
                    [
                        SimpleNamespace(
                            name=f"{path}/ignored",
                            is_directory=True,
                            content_length=None,
                            last_modified=None,
                            etag=None,
                        ),
                        SimpleNamespace(
                            name=f"{path}/metadata",
                            is_directory=True,
                            content_length=None,
                            last_modified=None,
                            etag=None,
                        ),
                    ]
                ]
            )
        if path.endswith("/metadata"):
            return _FakeOneLakePager(
                [
                    [
                        SimpleNamespace(
                            name=f"{path}/assets.json",
                            is_directory=False,
                            content_length=len(self.payload),
                            last_modified=datetime(
                                2026, 7, 28, tzinfo=timezone.utc
                            ),
                            etag='"etag-7"',
                        )
                    ]
                ]
            )
        raise AssertionError(path)

    def get_file_client(self, path: str):
        return _FakeOneLakeFileClient(self, path)

    def properties(self, path: str):
        return SimpleNamespace(
            name=path,
            size=len(self.payload),
            content_length=len(self.payload),
            last_modified=datetime(2026, 7, 28, tzinfo=timezone.utc),
            etag='"etag-7"',
        )


class _FakeOneLakeService:
    def __init__(self) -> None:
        self.filesystem = _FakeOneLakeFilesystem()
        self.workspaces: list[str] = []

    def get_file_system_client(self, workspace: str):
        self.workspaces.append(workspace)
        return self.filesystem


class _ConcurrentOneLakeFilesystem:
    def __init__(self, *, barrier: threading.Barrier | None = None) -> None:
        self.listed: list[str] = []
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()
        self._barrier = barrier

    def get_paths(
        self, *, path: str, recursive: bool, max_results: int | None
    ):
        assert recursive is False
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.listed.append(path)
        try:
            if self._barrier is not None and "/directory-" in path:
                self._barrier.wait(timeout=2)
            if path.endswith("/project"):
                entries = [
                    SimpleNamespace(
                        name=f"{path}/directory-{index:02d}",
                        is_directory=True,
                        content_length=None,
                        last_modified=None,
                        etag=None,
                    )
                    for index in range(12)
                ]
                entries.append(
                    SimpleNamespace(
                        name=f"{path}/ignored",
                        is_directory=True,
                        content_length=None,
                        last_modified=None,
                        etag=None,
                    )
                )
            elif "/directory-" in path:
                entries = [
                    SimpleNamespace(
                        name=f"{path}/module.py",
                        is_directory=False,
                        content_length=1,
                        last_modified=datetime(
                            2026, 7, 28, tzinfo=timezone.utc
                        ),
                        etag=f'"{path}"',
                    )
                ]
            else:
                raise AssertionError(path)
            return _FakeOneLakePager([entries])
        finally:
            with self._lock:
                self.active -= 1
