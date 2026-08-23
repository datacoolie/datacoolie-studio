from pathlib import Path
import tomllib


def test_all_extra_covers_every_optional_dependency() -> None:
    project = tomllib.loads(
        (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]
    optional_dependencies = project["optional-dependencies"]

    expected = set().union(
        *(
            set(dependencies)
            for name, dependencies in optional_dependencies.items()
            if name != "all"
        )
    )

    assert set(optional_dependencies["all"]) == expected
