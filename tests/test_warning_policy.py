from __future__ import annotations

import warnings

import pytest


@pytest.mark.parametrize(
    "module",
    ["datacoolie_studio.test_warning_policy", "starlette.testclient"],
)
def test_owned_deprecation_warning_policy_is_active(module: str):
    with pytest.raises(DeprecationWarning):
        warnings.warn_explicit(
            "intentional warning-policy probe",
            DeprecationWarning,
            filename=f"{module.replace('.', '/')}.py",
            lineno=1,
            module=module,
        )
