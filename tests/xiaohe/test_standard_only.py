import shutil
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2] / "resources/xiaohe"


def standard_root(tmp_path):
    root = tmp_path / "resources"
    root.mkdir()
    manifest = yaml.safe_load((ROOT / "deep-runtime.yaml").read_text("utf-8"))
    for spec in manifest["skills"].values():
        spec.pop("legacy_id", None)
        shutil.copytree(ROOT / spec["path"], root / spec["path"])
    shutil.copytree(ROOT / "deep-runtime", root / "deep-runtime")
    (root / "deep-runtime.yaml").write_text(yaml.safe_dump(manifest, allow_unicode=True), "utf-8")
    return root


def test_standard_packages_load_without_catalog_or_legacy_aliases(tmp_path):
    from xiaohe.deep_runtime.bundles import load_runtime_bundle

    bundle = load_runtime_bundle(standard_root(tmp_path))
    assert len(bundle.skills) == 21
    assert bundle.bindings["xiaohe-buyer"].flow_skill == "buyer-lead-capture"
    assert not any("legacy_id" in spec.model_dump() for spec in bundle.skills.values())


@pytest.mark.parametrize("direction", ["buyer", "organization", "supplier"])
def test_independent_form_uses_standard_field_rules(tmp_path, monkeypatch, direction):
    from xiaohe.deep_runtime.bundles import load_runtime_bundle
    from xiaohe.reception import forms

    root = standard_root(tmp_path)
    monkeypatch.setattr(forms, "RESOURCE_ROOT", root)
    bundle = load_runtime_bundle(root)
    bundle_id, policy = forms.form_definition(direction)
    assert bundle_id == bundle.bundle_id
    assert policy.required_fields == bundle.field_policies[direction].required_fields
    assert forms.form_field_policy(direction, expected_bundle_id=bundle_id) == policy
