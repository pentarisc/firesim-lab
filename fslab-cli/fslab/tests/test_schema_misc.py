"""
tests/test_schema_misc.py
=========================
Phase 1 — validation tests for the leaf schema modules composed by the
project/registry models:

  * host_model.py        — ExternalHostConfig, Ec2LaunchHostConfig, FpgaSlotConfig
  * publish.py           — AwsAfiPublishConfig, NonePublishConfig
  * artifact_source.py   — AwsAfiArtifactSourceConfig
  * resolvers.py         — BridgeParam value/ref handling
  * runner_args.py       — F2RunnerArgs payload axis + resolve helpers
  * bitbuilder_args.py   — resolve_args_schema / resolve_params_schema

Assertions pin to the documented code tags. Where a constraint is enforced by
a plain pydantic Field rule (e.g. ``min_length``) rather than a tagged raise,
the test asserts behaviorally and is flagged inline.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from fslab.schemas.host_model import (
    ExternalHostConfig,
    Ec2LaunchHostConfig,
    FpgaSlotConfig,
)
from fslab.schemas.publish import AwsAfiPublishConfig, NonePublishConfig
from fslab.schemas.artifact_source import AwsAfiArtifactSourceConfig
from fslab.schemas.resolvers import BridgeParam
from fslab.schemas import runner_args as ra
from fslab.schemas import bitbuilder_args as ba


# ===========================================================================
# host_model — ExternalHostConfig
# ===========================================================================


def external_base(**over) -> dict:
    d = {
        "type": "external",
        "host": "build.example.com",
        "user": "centos",
        "remote_platform_path": "/home/centos/hdk",
    }
    d.update(over)
    return d


class TestExternalHost:
    def test_valid(self):
        h = ExternalHostConfig.model_validate(external_base())
        assert h.host == "build.example.com"

    def test_hmod03_at_in_host(self):
        with pytest.raises(ValidationError) as ei:
            ExternalHostConfig.model_validate(external_base(host="centos@build"))
        assert "HMOD-03" in str(ei.value)

    def test_hmod03_url_in_host(self):
        with pytest.raises(ValidationError) as ei:
            ExternalHostConfig.model_validate(external_base(host="ssh://build"))
        assert "HMOD-03" in str(ei.value)

    def test_hmod04_relative_remote_path(self):
        with pytest.raises(ValidationError) as ei:
            ExternalHostConfig.model_validate(external_base(remote_platform_path="rel/path"))
        assert "HMOD-04" in str(ei.value)

    def test_hmod02_blank_ssh_key_becomes_none(self):
        h = ExternalHostConfig.model_validate(external_base(ssh_key="   "))
        assert h.ssh_key is None


# ===========================================================================
# host_model — Ec2LaunchHostConfig
# ===========================================================================


def ec2_base(**over) -> dict:
    d = {
        "type": "ec2_launch",
        "region": "us-west-2",
        "iam_instance_profile": "fslab-build-role",
    }
    d.update(over)
    return d


class TestEc2LaunchHost:
    def test_valid(self):
        h = Ec2LaunchHostConfig.model_validate(ec2_base())
        assert h.region == "us-west-2"

    def test_aws02_bad_region(self):
        with pytest.raises(ValidationError) as ei:
            Ec2LaunchHostConfig.model_validate(ec2_base(region="not_a_region"))
        assert "AWS-02" in str(ei.value)

    def test_aws03_bad_instance_type(self):
        with pytest.raises(ValidationError) as ei:
            Ec2LaunchHostConfig.model_validate(ec2_base(instance_type="F2.2xlarge"))
        assert "AWS-03" in str(ei.value)

    def test_aws01_bad_ami_id(self):
        with pytest.raises(ValidationError) as ei:
            Ec2LaunchHostConfig.model_validate(ec2_base(ami_id="ami-nothex"))
        assert "AWS-01" in str(ei.value)

    def test_aws06_bad_profile(self):
        with pytest.raises(ValidationError) as ei:
            Ec2LaunchHostConfig.model_validate(ec2_base(aws_profile="bad profile"))
        assert "AWS-06" in str(ei.value)

    def test_aws07_bad_instance_id(self):
        with pytest.raises(ValidationError) as ei:
            Ec2LaunchHostConfig.model_validate(ec2_base(instance_id="i-nothex"))
        assert "AWS-07" in str(ei.value)

    def test_hmod04_relative_remote_path(self):
        with pytest.raises(ValidationError) as ei:
            Ec2LaunchHostConfig.model_validate(ec2_base(remote_platform_path="rel"))
        assert "HMOD-04" in str(ei.value)

    def test_iam_instance_profile_required_behavioral(self):
        # HMOD-07 enforced via min_length=1 / required — no tag in the message.
        d = ec2_base()
        del d["iam_instance_profile"]
        with pytest.raises(ValidationError):
            Ec2LaunchHostConfig.model_validate(d)

    # --- volume overrides -------------------------------------------------

    def test_volumes_default_none(self):
        h = Ec2LaunchHostConfig.model_validate(ec2_base())
        assert h.root_volume_gb is None
        assert h.data_volume_gb is None
        assert h.volume_type is None

    def test_volumes_valid(self):
        h = Ec2LaunchHostConfig.model_validate(
            ec2_base(data_volume_gb=100, root_volume_gb=60, volume_type="gp3")
        )
        assert h.data_volume_gb == 100
        assert h.root_volume_gb == 60
        assert h.volume_type == "gp3"

    def test_aws04_data_volume_zero(self):
        with pytest.raises(ValidationError) as ei:
            Ec2LaunchHostConfig.model_validate(ec2_base(data_volume_gb=0))
        assert "AWS-04" in str(ei.value)

    def test_aws04_root_volume_too_large(self):
        with pytest.raises(ValidationError) as ei:
            Ec2LaunchHostConfig.model_validate(ec2_base(root_volume_gb=99999))
        assert "AWS-04" in str(ei.value)

    def test_bad_volume_type_rejected(self):
        with pytest.raises(ValidationError):
            Ec2LaunchHostConfig.model_validate(
                ec2_base(data_volume_gb=100, volume_type="gp9")
            )

    def test_aws04_volume_type_requires_size(self):
        with pytest.raises(ValidationError) as ei:
            Ec2LaunchHostConfig.model_validate(ec2_base(volume_type="gp3"))
        assert "AWS-04" in str(ei.value)


# ===========================================================================
# host_model — FpgaSlotConfig
# ===========================================================================


class TestFpgaSlot:
    def test_valid_slot_zero(self):
        s = FpgaSlotConfig.model_validate({"id": 0})
        assert s.id == 0

    def test_fslot01_negative_id(self):
        with pytest.raises(ValidationError) as ei:
            FpgaSlotConfig.model_validate({"id": -1})
        assert "FSLOT-01" in str(ei.value)

    def test_fslot01_nonzero_id(self):
        with pytest.raises(ValidationError) as ei:
            FpgaSlotConfig.model_validate({"id": 1})
        assert "FSLOT-01" in str(ei.value)


# ===========================================================================
# publish
# ===========================================================================


def aws_afi_publish_base(**over) -> dict:
    d = {"type": "aws_afi", "s3_bucket_name": "my-fslab-bucket"}
    d.update(over)
    return d


class TestPublish:
    def test_none_publish_valid(self):
        p = NonePublishConfig.model_validate({"type": "none"})
        assert p.type == "none"

    def test_aws_afi_valid(self):
        p = AwsAfiPublishConfig.model_validate(aws_afi_publish_base())
        assert p.s3_bucket_name == "my-fslab-bucket"

    def test_aws04_bad_bucket(self):
        with pytest.raises(ValidationError) as ei:
            AwsAfiPublishConfig.model_validate(aws_afi_publish_base(s3_bucket_name="Bad_Bucket"))
        assert "AWS-04" in str(ei.value)

    def test_aws02_bad_copy_region(self):
        with pytest.raises(ValidationError) as ei:
            AwsAfiPublishConfig.model_validate(aws_afi_publish_base(copy_to_regions=["nope"]))
        assert "AWS-02" in str(ei.value)

    def test_aws05_bad_sns_arn(self):
        with pytest.raises(ValidationError) as ei:
            AwsAfiPublishConfig.model_validate(aws_afi_publish_base(sns_topic_arn="not-an-arn"))
        assert "AWS-05" in str(ei.value)

    def test_aws06_bad_profile(self):
        with pytest.raises(ValidationError) as ei:
            AwsAfiPublishConfig.model_validate(aws_afi_publish_base(aws_profile="bad profile"))
        assert "AWS-06" in str(ei.value)


# ===========================================================================
# artifact_source
# ===========================================================================


class TestArtifactSource:
    def test_valid(self):
        a = AwsAfiArtifactSourceConfig.model_validate(
            {"type": "aws_afi", "agfi": "agfi-0123456789abcdef0"}
        )
        assert a.agfi.startswith("agfi-")

    def test_aws08_bad_agfi(self):
        with pytest.raises(ValidationError) as ei:
            AwsAfiArtifactSourceConfig.model_validate({"type": "aws_afi", "agfi": "agfi-short"})
        assert "AWS-08" in str(ei.value)


# ===========================================================================
# resolvers — BridgeParam
# ===========================================================================


class TestBridgeParam:
    def test_literal_value(self):
        p = BridgeParam.model_validate(115200)
        assert p.value == 115200 and p.ref is None

    def test_ref_form(self):
        p = BridgeParam.model_validate({"ref": "BAUD"})
        assert p.ref == "BAUD" and p.value is None

    def test_invalid_dict(self):
        with pytest.raises(ValidationError):
            BridgeParam.model_validate({"unexpected": "x"})


# ===========================================================================
# runner_args — F2RunnerArgs payload axis
# ===========================================================================


class TestF2RunnerArgs:
    def test_valid_default(self):
        a = ra.F2RunnerArgs.model_validate({})
        assert a.verify_hash == ra.VerifyHash.IF_PRESENT

    def test_max_cycles_must_be_positive(self):
        with pytest.raises(ValidationError):
            ra.F2RunnerArgs.model_validate({"max_cycles": 0})

    def test_payload_remote_name_defaults_to_basename(self):
        a = ra.F2RunnerArgs.model_validate({"payloads": [{"path": "bins/dhrystone.bin"}]})
        assert a.payloads[0].remote_name == "dhrystone.bin"

    def test_pay02_duplicate_remote_name(self):
        with pytest.raises(ValidationError) as ei:
            ra.F2RunnerArgs.model_validate(
                {
                    "payloads": [
                        {"path": "a/foo.bin", "remote_name": "x.bin"},
                        {"path": "b/bar.bin", "remote_name": "x.bin"},
                    ]
                }
            )
        assert "PAY-02" in str(ei.value)

    def test_pay03_reserved_remote_name(self):
        with pytest.raises(ValidationError) as ei:
            ra.F2RunnerArgs.model_validate(
                {"payloads": [{"path": "x", "remote_name": "driver.log"}]}
            )
        assert "PAY-03" in str(ei.value)

    def test_pay06_reserved_result_remote_path(self):
        with pytest.raises(ValidationError) as ei:
            ra.F2RunnerArgs.model_validate(
                {"result_files": [{"remote_path": "result.yaml"}]}
            )
        assert "PAY-06" in str(ei.value)


# ===========================================================================
# resolve helpers — args/params schema lookup
# ===========================================================================


class TestResolveHelpers:
    def test_runner_args_known(self):
        assert ra.resolve_args_schema("F2RunnerArgs") is ra.F2RunnerArgs

    def test_runa01_runner_args_unknown(self):
        with pytest.raises(ValueError) as ei:
            ra.resolve_args_schema("NoSuchArgs")
        assert "RUNA-01" in str(ei.value)

    def test_runa03_runner_params_unknown(self):
        with pytest.raises(ValueError) as ei:
            ra.resolve_params_schema("NoSuchParams")
        assert "RUNA-03" in str(ei.value)

    def test_bitbuilder_args_known(self):
        assert ba.resolve_args_schema("F2BitbuilderArgs") is ba.F2BitbuilderArgs

    def test_bba01_bitbuilder_args_unknown(self):
        with pytest.raises(ValueError) as ei:
            ba.resolve_args_schema("NoSuchArgs")
        assert "BBA-01" in str(ei.value)

    def test_bba03_bitbuilder_params_unknown(self):
        with pytest.raises(ValueError) as ei:
            ba.resolve_params_schema("NoSuchParams")
        assert "BBA-03" in str(ei.value)
