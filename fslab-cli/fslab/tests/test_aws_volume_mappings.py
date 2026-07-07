"""Unit tests for the EC2 volume-override resolver.

Covers `fslab.cloudutils.aws.fpga._resolve_block_device_mappings`, which turns
the user's `root_volume_gb` / `data_volume_gb` / `volume_type` requests into a
`BlockDeviceMappings` override by introspecting the AMI. No AWS calls: a tiny
fake `ec2` stub serves a canned `describe_images` response.
"""

from __future__ import annotations

import pytest

from fslab.cloudutils.aws.fpga import (
    _resolve_block_device_mappings,
    _GROWFS_USERDATA,
    launch_instance,
)


def _ami_response(mappings, root="/dev/sda1"):
    return {"Images": [{"RootDeviceName": root, "BlockDeviceMappings": mappings}]}


class FakeEc2:
    """Minimal stand-in exposing only describe_images."""

    def __init__(self, response):
        self._response = response
        self.calls = []

    def describe_images(self, ImageIds):  # noqa: N803 (boto3 kwarg name)
        self.calls.append(ImageIds)
        return self._response


# A root + one data volume, plus an ephemeral device that must be ignored.
ROOT_AND_DATA = [
    {
        "DeviceName": "/dev/sda1",
        "Ebs": {
            "SnapshotId": "snap-root",
            "VolumeSize": 40,
            "VolumeType": "gp2",
            "DeleteOnTermination": True,
            "Encrypted": False,
        },
    },
    {
        "DeviceName": "/dev/sdb",
        "Ebs": {
            "SnapshotId": "snap-data",
            "VolumeSize": 10,
            "VolumeType": "gp2",
            "DeleteOnTermination": True,
        },
    },
    {"DeviceName": "/dev/sdc", "VirtualName": "ephemeral0"},  # skipped
]


def _resolve(mappings, **kw):
    ec2 = FakeEc2(_ami_response(mappings))
    kw.setdefault("root_volume_gb", None)
    kw.setdefault("data_volume_gb", None)
    kw.setdefault("volume_type", None)
    return _resolve_block_device_mappings(ec2, "ami-123", **kw)


def test_no_fields_returns_none():
    assert _resolve(ROOT_AND_DATA) is None


def test_data_volume_resize_only_touches_data():
    out = _resolve(ROOT_AND_DATA, data_volume_gb=100)
    assert len(out) == 1
    m = out[0]
    assert m["DeviceName"] == "/dev/sdb"
    assert m["Ebs"]["VolumeSize"] == 100
    assert m["Ebs"]["SnapshotId"] == "snap-data"  # preserved


def test_root_volume_resize():
    out = _resolve(ROOT_AND_DATA, root_volume_gb=80)
    assert [m["DeviceName"] for m in out] == ["/dev/sda1"]
    assert out[0]["Ebs"]["VolumeSize"] == 80


def test_both_volumes_resized():
    out = _resolve(ROOT_AND_DATA, root_volume_gb=80, data_volume_gb=100)
    by_dev = {m["DeviceName"]: m["Ebs"]["VolumeSize"] for m in out}
    assert by_dev == {"/dev/sda1": 80, "/dev/sdb": 100}


def test_encrypted_dropped_when_snapshot_present():
    # Encrypted/KmsKeyId conflict with an inherited SnapshotId in the override.
    out = _resolve(ROOT_AND_DATA, root_volume_gb=80)
    assert "Encrypted" not in out[0]["Ebs"]


def test_volume_type_applied_and_iops_throughput_dropped():
    mappings = [
        {"DeviceName": "/dev/sda1", "Ebs": {"VolumeSize": 40, "VolumeType": "gp2"}},
        {
            "DeviceName": "/dev/sdb",
            "Ebs": {
                "VolumeSize": 10,
                "VolumeType": "io1",
                "Iops": 3000,
                "Throughput": 250,
            },
        },
    ]
    out = _resolve(mappings, data_volume_gb=100, volume_type="gp3")
    ebs = out[0]["Ebs"]
    assert ebs["VolumeType"] == "gp3"
    assert "Iops" not in ebs and "Throughput" not in ebs


def test_shrink_rejected():
    with pytest.raises(ValueError, match="only grow"):
        _resolve(ROOT_AND_DATA, data_volume_gb=5)


def test_no_data_volume_rejected():
    root_only = [ROOT_AND_DATA[0]]
    with pytest.raises(ValueError, match="no secondary EBS"):
        _resolve(root_only, data_volume_gb=100)


def test_multiple_data_volumes_ambiguous():
    two_data = ROOT_AND_DATA[:2] + [
        {"DeviceName": "/dev/sdd", "Ebs": {"VolumeSize": 10, "VolumeType": "gp2"}}
    ]
    with pytest.raises(ValueError, match="ambiguous"):
        _resolve(two_data, data_volume_gb=100)


def test_ami_not_found_rejected():
    ec2 = FakeEc2({"Images": []})
    with pytest.raises(ValueError, match="not found"):
        _resolve_block_device_mappings(
            ec2, "ami-missing", root_volume_gb=None,
            data_volume_gb=100, volume_type=None,
        )


# ---------------------------------------------------------------------------
# launch_instance — UserData (boot-time filesystem grow) injection
# ---------------------------------------------------------------------------


class FakeLaunchEc2:
    """Records run_instances kwargs; serves the minimal describe_* calls
    launch_instance makes (no subnet path exercised)."""

    def __init__(self):
        self.run_kwargs = None

    def describe_images(self, ImageIds):  # noqa: N803
        return _ami_response(ROOT_AND_DATA)

    def describe_security_groups(self, Filters):  # noqa: N803
        # Pretend the SSH SG already exists so no create path runs.
        return {"SecurityGroups": [{"GroupId": "sg-existing"}]}

    def run_instances(self, **kwargs):
        self.run_kwargs = kwargs
        return {"Instances": [{"InstanceId": "i-abc123"}]}


class FakeSession:
    def __init__(self, ec2):
        self._ec2 = ec2

    def client(self, name):
        assert name == "ec2"
        return self._ec2


def _launch(**vol):
    ec2 = FakeLaunchEc2()
    iid = launch_instance(
        FakeSession(ec2), ami_id="ami-123", instance_type="t3.medium",
        lifecycle="on_demand", **vol,
    )
    assert iid == "i-abc123"
    return ec2.run_kwargs


def test_userdata_injected_when_data_volume_set():
    kw = _launch(data_volume_gb=30)
    assert kw.get("UserData") == _GROWFS_USERDATA
    assert "BlockDeviceMappings" in kw


def test_no_userdata_without_any_volume_override():
    kw = _launch()
    assert "UserData" not in kw
    assert "BlockDeviceMappings" not in kw


def test_no_userdata_for_root_only_resize():
    # Root is partitioned and grown by the AMI's growpart — no UserData needed.
    kw = _launch(root_volume_gb=200)
    assert "UserData" not in kw
    assert "BlockDeviceMappings" in kw
