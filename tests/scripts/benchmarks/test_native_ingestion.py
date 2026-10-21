from copy import deepcopy


def test_native_oracle_rejects_type_null_provenance_order_and_multiplicity_changes(script_module_loader):
    module = script_module_loader("scripts/benchmarks/native_ingestion.py", "native_ingestion_oracle")
    original = {"id": "123", "missing_or_null": None, "_file_uri": "canonical://a", "_record_line": 2, "values": [1,2]}
    for field, value in (("id",123),("_file_uri","canonical://b"),("_record_line",3),("values",[2,1])):
        changed = deepcopy(original)
        changed[field] = value
        assert module.native_digest(changed) != module.native_digest(original)
    missing = deepcopy(original)
    missing.pop("missing_or_null")
    assert module.native_digest(missing) != module.native_digest(original)
    assert module.native_digest([original]) != module.native_digest([original,original])
