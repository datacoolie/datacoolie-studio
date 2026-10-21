from __future__ import annotations

import hashlib
import importlib.util
import sys
from types import ModuleType

from tests.support.paths import REPOSITORY_ROOT


_MISSING = object()


class ScriptModuleLoader:
    """Load repository scripts for one test and restore interpreter state on close."""

    def __init__(self) -> None:
        self._loaded: list[tuple[str, object]] = []
        self._counter = 0

    def __call__(self, relative_path: str, name: str) -> ModuleType:
        path = (REPOSITORY_ROOT / relative_path).resolve()
        try:
            path.relative_to(REPOSITORY_ROOT)
        except ValueError as exc:
            raise ValueError(f"Script path must stay inside the repository: {path}") from exc
        if not path.is_file():
            raise FileNotFoundError(path)

        safe_name = "".join(character if character.isalnum() else "_" for character in name)
        digest = hashlib.sha256(path.as_posix().encode("utf-8")).hexdigest()[:12]
        module_name = f"_studio_test_script_{safe_name}_{digest}_{self._counter}"
        self._counter += 1
        previous = sys.modules.get(module_name, _MISSING)

        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to load script module: {path}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            self._restore(module_name, previous)
            raise

        self._loaded.append((module_name, previous))
        return module

    def close(self) -> None:
        while self._loaded:
            module_name, previous = self._loaded.pop()
            self._restore(module_name, previous)

    @staticmethod
    def _restore(module_name: str, previous: object) -> None:
        if previous is _MISSING:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous  # type: ignore[assignment]
