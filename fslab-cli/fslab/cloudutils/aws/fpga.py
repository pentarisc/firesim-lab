"""Thin boto3 helpers used by AWS-related fslab subsystems
(publisher, EC2-backed host providers).

All public helpers take a `boto3.Session` as the first argument so callers
own the credential context (profile / region). Constructing the session
in one place per pipeline keeps the named-profile + SSO story consistent
across publish, build-host, and run-host axes — see `make_session` /
`check_credentials` for the entry-point pattern.

Region selection follows the standard boto3 credential chain: explicit
`region_name` on the session, then `AWS_DEFAULT_REGION`, then the named
profile's config, then instance metadata. AGFI/AFI creation is
region-bound; replication to other regions is a Tier-2 feature handled
elsewhere.

No fslab schema imports — schema → helper translation happens in the
publisher / provider layer.
"""

from __future__ import annotations

import random
import socket
import string
import time
from pathlib import Path
from typing import Optional

import boto3
from botocore.exceptions import (
    ClientError,
    NoCredentialsError,
    NoRegionError,
    TokenRetrievalError,
    UnauthorizedSSOTokenError,
)

from fslab.utils.display import info, warning


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AwsCredsExpired(Exception):
    """Raised when boto3 cannot obtain credentials.

    Two flavours land here: SSO-token expiry (the common case in container
    workflows) and a missing/empty credential chain. The message includes
    the active profile name and a concrete remediation command.
    """


# ---------------------------------------------------------------------------
# Session construction + credential probe
# ---------------------------------------------------------------------------


def make_session(
    region: Optional[str] = None,
    profile: Optional[str] = None,
) -> boto3.Session:
    """Build a boto3 session pinned to the given region/profile.

    Either argument may be None — boto3 falls through to its standard
    credential chain (env vars, named profile config, instance metadata)
    when an argument is omitted.
    """
    kwargs: dict = {}
    if region:
        kwargs["region_name"] = region
    if profile:
        kwargs["profile_name"] = profile
    return boto3.Session(**kwargs)


def check_credentials(
    session: boto3.Session, profile: Optional[str] = None
) -> None:
    """Probe with sts:GetCallerIdentity to surface credential issues early.

    Translates the two common boto3 credential failures (SSO token expiry,
    no credentials in the chain) into `AwsCredsExpired` with an actionable
    message. Other errors propagate unchanged.

    Call once at the start of each pipeline phase (provider.request,
    publisher.publish) so credentials problems fail fast with a clean
    message instead of bubbling up mid-build from an unrelated API call.
    """
    try:
        session.client("sts").get_caller_identity()
    except (TokenRetrievalError, UnauthorizedSSOTokenError) as e:
        prof = profile or "(default)"
        flag = f" --profile {profile}" if profile else ""
        raise AwsCredsExpired(
            f"AWS SSO session expired for profile {prof}.\n"
            f"Run inside the container:  aws sso login{flag}\n"
            f"Then retry the operation."
        ) from e
    except NoCredentialsError as e:
        raise AwsCredsExpired(
            "No AWS credentials found in the boto3 chain. Set AWS_PROFILE, "
            "configure ~/.aws (e.g. `aws configure` or `aws sso login`), or "
            "set aws_profile in fslab.yaml."
        ) from e
    except NoRegionError as e:
        raise AwsCredsExpired(
            "No AWS region configured. Set AWS_DEFAULT_REGION, configure a "
            "default profile in ~/.aws/config, or supply host.region / "
            "publish.aws_profile in fslab.yaml."
        ) from e


# ---------------------------------------------------------------------------
# Identity / region helpers
# ---------------------------------------------------------------------------


def aws_userid(session: boto3.Session) -> str:
    """Return the numeric AWS account ID for the active credentials.

    Used to build firesim's bucket-name suffix `-<userid>-<region>` when
    the publisher's `append_userid_region` is true.
    """
    return session.client("sts").get_caller_identity()["Account"]


def aws_region(session: boto3.Session) -> str:
    """Return the active region from the session.

    Raises ValueError if no region is configured anywhere on the chain — a
    region is required for `create_fpga_image` and for region-suffixed
    bucket names, so failing fast is preferable to letting a downstream
    boto3 call fail with a less obvious error.
    """
    region = session.region_name
    if not region:
        raise ValueError(
            "No AWS region configured. Set AWS_DEFAULT_REGION, configure a "
            "default profile in ~/.aws/config, or use AWS_PROFILE."
        )
    return region


# ---------------------------------------------------------------------------
# S3
# ---------------------------------------------------------------------------


def ensure_bucket(
    session: boto3.Session, bucket: str, region: Optional[str] = None
) -> None:
    """Create `bucket` if it does not already exist; otherwise no-op.

    `head_bucket` is the cheap probe. A 404 means we own the namespace and
    just haven't created it yet — proceed to create. A 403 means somebody
    else owns it (S3 bucket names are global) — surface that as-is rather
    than masking it.

    `us-east-1` is the only region that rejects a `LocationConstraint`
    field on `create_bucket`; every other region requires it.
    """
    s3 = session.client("s3")
    region = region or aws_region(session)

    try:
        s3.head_bucket(Bucket=bucket)
        info(f"S3 bucket already exists: s3://{bucket}")
        return
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code not in ("404", "NoSuchBucket"):
            raise

    info(f"Creating S3 bucket: s3://{bucket} (region={region})")
    if region == "us-east-1":
        s3.create_bucket(Bucket=bucket)
    else:
        s3.create_bucket(
            Bucket=bucket,
            CreateBucketConfiguration={"LocationConstraint": region},
        )


def upload_dcp_to_s3(
    session: boto3.Session, local_path: Path, bucket: str, key: str
) -> None:
    """Upload a single DCP tarball to s3://<bucket>/<key>.

    Uses boto3's TransferManager under the hood (default thresholds), which
    transparently switches to multipart for large objects — DCP tarballs
    can run 100s of MB.
    """
    s3 = session.client("s3")
    info(f"Uploading {local_path.name} -> s3://{bucket}/{key}")
    s3.upload_file(str(local_path), bucket, key)


# ---------------------------------------------------------------------------
# EC2 — FPGA images (publisher side)
# ---------------------------------------------------------------------------


def create_fpga_image(
    session: boto3.Session,
    bucket: str,
    dcp_key: str,
    logs_prefix: str,
    name: str,
    description: str,
) -> dict[str, str]:
    """Submit `create-fpga-image` and return the AFI/AGFI ids.

    Returns a dict with `FpgaImageId` (AFI) and `FpgaImageGlobalId` (AGFI).
    Both are needed downstream: AFI for the describe-poll, AGFI for the
    hwdb entry that the runtime resolves.
    """
    ec2 = session.client("ec2")
    resp = ec2.create_fpga_image(
        InputStorageLocation={"Bucket": bucket, "Key": dcp_key},
        LogsStorageLocation={"Bucket": bucket, "Key": logs_prefix},
        Name=name,
        Description=description,
    )
    return {
        "FpgaImageId": resp["FpgaImageId"],
        "FpgaImageGlobalId": resp["FpgaImageGlobalId"],
    }


def wait_for_fpga_image(
    session: boto3.Session, afi_id: str, poll_interval: int = 10
) -> str:
    """Poll `describe-fpga-images` until state leaves `pending`.

    No timeout — the user can Ctrl+C. AFI creation typically runs 30–60
    minutes; configurable timeout/interval are deferred to a later round.

    Returns the final state code on `available`. Raises RuntimeError on
    `failed` or `unavailable`, surfacing any state-reason message AWS
    provides so the user has something actionable.
    """
    ec2 = session.client("ec2")
    info(f"Waiting for AFI {afi_id} to leave 'pending' state...")
    last_logged = None
    while True:
        resp = ec2.describe_fpga_images(FpgaImageIds=[afi_id])
        images = resp.get("FpgaImages", [])
        if not images:
            raise RuntimeError(
                f"describe-fpga-images returned no entry for {afi_id}"
            )
        state = images[0]["State"]["Code"]
        if state != last_logged:
            info(f"AFI {afi_id} state: {state}")
            last_logged = state
        if state == "available":
            return state
        if state in ("failed", "unavailable"):
            reason = images[0]["State"].get("Message", "(no message)")
            raise RuntimeError(
                f"AFI {afi_id} entered terminal state '{state}': {reason}"
            )
        time.sleep(poll_interval)


# ---------------------------------------------------------------------------
# EC2 — instance lifecycle (ec2_launch provider side)
# ---------------------------------------------------------------------------


class InstanceNotFound(Exception):
    """Raised when describe_instances cannot resolve a requested id."""


class InstanceUnusable(Exception):
    """Raised when an instance exists but cannot be used for a build (e.g.
    terminated, shutting-down)."""


# AWS error codes that mean "the instance is already in (or past) the
# cleanup-target state". Cleanup paths swallow these and continue — the
# goal of stop/terminate during cleanup is "the resource is no longer
# running on our account", which is satisfied whether we did it or AWS
# (or the user) already did.
_ALREADY_GONE_CODES = frozenset({
    "InvalidInstanceID.NotFound",
    "InvalidInstanceID.Malformed",
})


def describe_instance(session: boto3.Session, instance_id: str) -> dict:
    """Return the first matching reservation's first instance.

    Raises `InstanceNotFound` if the id is unknown, and
    `InstanceUnusable` if the state is terminal (terminated /
    shutting-down). Other states (running, pending, stopping, stopped) are
    handed back for the caller to decide on.
    """
    ec2 = session.client("ec2")
    try:
        resp = ec2.describe_instances(InstanceIds=[instance_id])
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in _ALREADY_GONE_CODES:
            raise InstanceNotFound(
                f"EC2 instance '{instance_id}' not found in this region."
            ) from e
        raise

    reservations = resp.get("Reservations", [])
    if not reservations or not reservations[0].get("Instances"):
        raise InstanceNotFound(
            f"EC2 instance '{instance_id}' not found in this region."
        )
    inst = reservations[0]["Instances"][0]
    state = inst["State"]["Name"]
    if state in ("terminated", "shutting-down"):
        raise InstanceUnusable(
            f"EC2 instance '{instance_id}' is in terminal state '{state}'."
        )
    return inst


def start_instance(session: boto3.Session, instance_id: str) -> None:
    """Start a stopped instance. No-op if already running."""
    ec2 = session.client("ec2")
    ec2.start_instances(InstanceIds=[instance_id])


def stop_instance(session: boto3.Session, instance_id: str) -> None:
    """Stop a running instance, idempotently from a cleanup caller's view.

    Used on the managed-reuse cleanup path (lifecycle 'started'): we want
    the instance to end up not-running. Cases swallowed:

      * `InvalidInstanceID.NotFound` / `.Malformed` — AWS has already
        purged the instance record; nothing to stop.
      * `IncorrectInstanceState` — most commonly fires when the instance
        is already terminated or shutting-down (a state past 'stopped'
        from the cleanup perspective), or stopping/stopped (already at
        target). Either way, the desired post-condition is satisfied.

    Any other ClientError propagates — those are real problems (perms,
    throttling, region mismatch) the user needs to see.
    """
    ec2 = session.client("ec2")
    try:
        ec2.stop_instances(InstanceIds=[instance_id])
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in _ALREADY_GONE_CODES:
            info(
                f"Instance {instance_id} not found in AWS — already gone, "
                f"treating as cleaned."
            )
            return
        if code == "IncorrectInstanceState":
            info(
                f"Instance {instance_id} cannot be stopped from its current "
                f"state ({e}) — treating as already cleaned."
            )
            return
        raise


def terminate_instance(session: boto3.Session, instance_id: str) -> None:
    """Terminate an instance, idempotently from a cleanup caller's view.

    Used on the ephemeral-launch cleanup path (lifecycle 'launched').
    AWS treats `terminate_instances` on an already-terminated instance as
    a no-op as long as the instance record still exists, but once AWS
    purges the record the same call raises `InvalidInstanceID.NotFound`.
    Swallow that here so cleanup is properly idempotent across the purge
    boundary.

    Any other ClientError propagates — those are real problems (perms,
    throttling, region mismatch) the user needs to see.
    """
    ec2 = session.client("ec2")
    try:
        ec2.terminate_instances(InstanceIds=[instance_id])
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in _ALREADY_GONE_CODES:
            info(
                f"Instance {instance_id} not found in AWS — already gone, "
                f"treating as cleaned."
            )
            return
        raise


def wait_until_running(
    session: boto3.Session, instance_id: str, timeout: int = 600
) -> dict:
    """Block until the instance reports `running`, then return the latest
    describe-instances payload (the caller usually wants `PublicIpAddress`
    or `PublicDnsName`)."""
    ec2 = session.client("ec2")
    waiter = ec2.get_waiter("instance_running")
    waiter.wait(
        InstanceIds=[instance_id],
        WaiterConfig={"Delay": 10, "MaxAttempts": max(1, timeout // 10)},
    )
    return describe_instance(session, instance_id)


def wait_until_stopped(
    session: boto3.Session, instance_id: str, timeout: int = 600
) -> None:
    """Block until the instance reports `stopped`."""
    ec2 = session.client("ec2")
    waiter = ec2.get_waiter("instance_stopped")
    waiter.wait(
        InstanceIds=[instance_id],
        WaiterConfig={"Delay": 10, "MaxAttempts": max(1, timeout // 10)},
    )


def wait_for_ssh(
    host: str, port: int = 22, timeout: int = 300, poll_interval: int = 5
) -> None:
    """TCP-probe `host:port` until it accepts a connection.

    `wait_until_running` returns as soon as EC2 marks the instance running,
    but cloud-init / sshd takes another ~30–60s to be reachable. This is
    the cheap port-open check that bridges the gap.
    """
    deadline = time.monotonic() + timeout
    last_err: Optional[OSError] = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=poll_interval):
                return
        except OSError as e:
            last_err = e
            time.sleep(poll_interval)
    raise TimeoutError(
        f"Timed out after {timeout}s waiting for {host}:{port} (last: {last_err})"
    )


# Cloud-init UserData injected when data_volume_gb resizes the AMI's secondary
# volume. Growing the EBS volume only enlarges the block device; the filesystem
# on the pre-baked data volume must be grown to make the space usable. This runs
# as root at first boot and self-targets: it grows any *whole-disk* data
# filesystem (device holds the FS directly, no partition table) that is smaller
# than its device. The root disk is partitioned, so it is skipped here and left
# to the AMI's own cloud-init growpart. Output is logged for post-hoc inspection.
_GROWFS_USERDATA = r"""#!/bin/bash
# firesim-lab: grow resized whole-disk data volume(s) to fill the enlarged EBS
# block device. Root is partitioned and handled by the AMI's own growpart.
exec >>/var/log/firesim-lab-growfs.log 2>&1
set -x
for dev in /dev/nvme*n1 /dev/xvd[b-z]; do
  [ -b "$dev" ] || continue
  # Skip devices carrying a partition table (e.g. the root disk).
  [ -n "$(lsblk -rno NAME "$dev" | tail -n +2)" ] && continue
  mnt=$(findmnt -rno TARGET --source "$dev" 2>/dev/null) || continue
  [ -n "$mnt" ] || continue
  case "$(findmnt -rno FSTYPE --source "$dev" 2>/dev/null)" in
    ext2|ext3|ext4) resize2fs "$dev" ;;
    xfs) xfs_growfs "$mnt" ;;
  esac
done
"""


def _resolve_block_device_mappings(
    ec2,
    ami_id: str,
    *,
    root_volume_gb: Optional[int],
    data_volume_gb: Optional[int],
    volume_type: Optional[str],
) -> Optional[list]:
    """Build a `BlockDeviceMappings` override that resizes/retypes the AMI's
    volumes, or `None` when the caller requested no override.

    Approach A (AMI introspection): read the AMI's own block-device mappings
    via `describe_images`, then emit override entries only for the volumes the
    user asked to change — every unmentioned device keeps its AMI default, so
    the no-fields case reproduces today's behaviour exactly.

    Volumes are identified by role, not by device name, so the user never has
    to know the AMI's internal `/dev/...` layout:
      * root — the mapping whose DeviceName equals the AMI's RootDeviceName.
      * data — the single non-root EBS-backed mapping. Zero or >1 is ambiguous
        and raises rather than guessing which volume the user meant.

    `volume_type`, when given, applies to each volume being overridden. EBS
    volumes can only grow, so a request smaller than the AMI's baked size is
    refused up front with a clear message instead of a late boto3 error.
    """
    if root_volume_gb is None and data_volume_gb is None:
        return None

    images = ec2.describe_images(ImageIds=[ami_id]).get("Images", [])
    if not images:
        raise ValueError(
            f"Cannot resize volumes: AMI {ami_id} not found or not visible "
            f"to this account/region."
        )
    image = images[0]
    root_device_name = image.get("RootDeviceName")
    ebs_mappings = [m for m in image.get("BlockDeviceMappings", []) if "Ebs" in m]

    def _override(mapping: dict, requested_gb: int, role: str) -> dict:
        existing_gb = mapping["Ebs"].get("VolumeSize")
        if existing_gb is not None and requested_gb < existing_gb:
            raise ValueError(
                f"{role}_volume_gb={requested_gb} is smaller than the AMI's "
                f"baked {role} volume ({existing_gb} GiB). EBS volumes can "
                f"only grow, not shrink."
            )
        # Copy the AMI's Ebs block and mutate only what we intend to change,
        # preserving SnapshotId / DeleteOnTermination.
        new_ebs = dict(mapping["Ebs"])
        new_ebs["VolumeSize"] = requested_gb
        # Encrypted / KmsKeyId conflict with an inherited SnapshotId in a
        # RunInstances override; drop them so the volume inherits the
        # snapshot's encryption unchanged.
        if "SnapshotId" in new_ebs:
            new_ebs.pop("Encrypted", None)
            new_ebs.pop("KmsKeyId", None)
        if volume_type is not None:
            new_ebs["VolumeType"] = volume_type
            # Iops/Throughput are tied to the old type; let AWS apply the new
            # type's defaults rather than risk an incompatible carry-over.
            new_ebs.pop("Iops", None)
            new_ebs.pop("Throughput", None)
        return {"DeviceName": mapping["DeviceName"], "Ebs": new_ebs}

    overrides: list = []

    if root_volume_gb is not None:
        root_mapping = next(
            (m for m in ebs_mappings if m["DeviceName"] == root_device_name), None
        )
        if root_mapping is None:
            raise ValueError(
                f"Cannot resize root volume: AMI {ami_id} exposes no EBS root "
                f"device (RootDeviceName={root_device_name!r})."
            )
        overrides.append(_override(root_mapping, root_volume_gb, "root"))

    if data_volume_gb is not None:
        data_mappings = [
            m for m in ebs_mappings if m["DeviceName"] != root_device_name
        ]
        if not data_mappings:
            raise ValueError(
                f"Cannot resize data volume: AMI {ami_id} has no secondary EBS "
                f"volume (only the root device). Use root_volume_gb instead."
            )
        if len(data_mappings) > 1:
            names = ", ".join(m["DeviceName"] for m in data_mappings)
            raise ValueError(
                f"Cannot resize data volume: AMI {ami_id} has {len(data_mappings)} "
                f"non-root EBS volumes ({names}); which to grow is ambiguous. "
                f"Per-device selection is not yet supported."
            )
        overrides.append(_override(data_mappings[0], data_volume_gb, "data"))

    return overrides


def launch_instance(
    session: boto3.Session,
    *,
    ami_id: str,
    instance_type: str,
    key_name: Optional[str] = None,
    subnet_id: Optional[str] = None,
    iam_instance_profile: Optional[str] = None,
    lifecycle: str = "spot_one_time",
    tags: Optional[dict[str, str]] = None,
    root_volume_gb: Optional[int] = None,
    data_volume_gb: Optional[int] = None,
    volume_type: Optional[str] = None,
) -> str:
    """Run a single instance and return its id.

    `lifecycle` controls market behaviour:
      * "spot_one_time"  → spot, terminate-on-interrupt (cheapest)
      * "on_demand"      → regular on-demand instance

    Spot-persistent is deliberately not offered — managed reuse is the
    `instance_id` opt-in path, not a per-launch flag.
    """
    ec2 = session.client("ec2")

    sg_name = "fslab-ssh-access-sg"
    vpc_id = None
    security_group_id = None

    # If a subnet is provided, we must find its VPC to create the SG in the right place
    if subnet_id:
        subnet_info = ec2.describe_subnets(SubnetIds=[subnet_id])
        vpc_id = subnet_info["Subnets"][0]["VpcId"]

    # 1. Check if our SSH Security Group already exists
    filters = [{"Name": "group-name", "Values": [sg_name]}]
    if vpc_id:
        filters.append({"Name": "vpc-id", "Values": [vpc_id]})

    existing_sgs = ec2.describe_security_groups(Filters=filters).get("SecurityGroups", [])

    if existing_sgs:
        # Reuse existing security group to prevent clutter
        security_group_id = existing_sgs[0]["GroupId"]
        info(f"Using existing Security Group: {security_group_id}")
    else:
        # 2. Create the Security Group
        sg_params = {
            "GroupName": sg_name,
            "Description": "Allow SSH access for boto3 spot instances"
        }
        if vpc_id:
            sg_params["VpcId"] = vpc_id

        sg_resp = ec2.create_security_group(**sg_params)
        security_group_id = sg_resp["GroupId"]
        info(f"Created new Security Group: {security_group_id}")

        # 3. Authorize Ingress for Port 22 (SSH)
        ec2.authorize_security_group_ingress(
            GroupId=security_group_id,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": 22,
                    "ToPort": 22,
                    "IpRanges": [{"CidrIp": "0.0.0.0/0"}], # 0.0.0.0/0 allows access from anywhere
                }
            ],
        )
        info("Authorized Port 22 (SSH) for the Security Group.")
    # -------------------------------------------------------------

    # Proceed with launching the instance
    params: dict = {
        "ImageId": ami_id,
        "InstanceType": instance_type,
        "MinCount": 1,
        "MaxCount": 1,
    }

    if key_name:
        params["KeyName"] = key_name
    if iam_instance_profile:
        params["IamInstanceProfile"] = {"Name": iam_instance_profile}

    # 4. Attach Subnet, Security Group, and FORCE a Public IP Address
    if subnet_id:
        params["NetworkInterfaces"] = [{
            "DeviceIndex": 0,
            "SubnetId": subnet_id,
            "Groups": [security_group_id],
            "AssociatePublicIpAddress": True, # Crucial for SSH access
        }]
    else:
        # If no subnet is provided, use default VPC and assign the SG directly
        params["SecurityGroupIds"] = [security_group_id]

    if lifecycle == "spot_one_time":
        params["InstanceMarketOptions"] = {
            "MarketType": "spot",
            "SpotOptions": {
                "SpotInstanceType": "one-time",
                "InstanceInterruptionBehavior": "terminate",
            },
        }
    elif lifecycle != "on_demand":
        raise ValueError(
            f"launch_instance: unsupported lifecycle {lifecycle!r}; "
            f"expected one of 'spot_one_time' or 'on_demand'."
        )

    if tags:
        params["TagSpecifications"] = [
            {
                "ResourceType": "instance",
                "Tags": [{"Key": k, "Value": v} for k, v in tags.items()],
            }
        ]

    block_device_mappings = _resolve_block_device_mappings(
        ec2,
        ami_id,
        root_volume_gb=root_volume_gb,
        data_volume_gb=data_volume_gb,
        volume_type=volume_type,
    )
    if block_device_mappings:
        params["BlockDeviceMappings"] = block_device_mappings
        sizes = ", ".join(
            f"{m['DeviceName']}={m['Ebs']['VolumeSize']}GiB"
            f"{'/' + m['Ebs']['VolumeType'] if 'VolumeType' in m['Ebs'] and volume_type else ''}"
            for m in block_device_mappings
        )
        info(f"Overriding AMI volumes: {sizes}")

    # A bigger data volume is only usable once its filesystem is grown to fill
    # the enlarged block device. Inject the boot-time grow (boto3 base64-encodes
    # UserData for run_instances). Only needed for the whole-disk data volume;
    # the root filesystem is grown by the AMI's own growpart.
    if data_volume_gb is not None:
        params["UserData"] = _GROWFS_USERDATA

    resp = ec2.run_instances(**params)
    instance_id = resp["Instances"][0]["InstanceId"]
    info(f"Launched EC2 instance {instance_id} ({lifecycle}, {instance_type})")

    return instance_id


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def random_suffix(n: int = 4) -> str:
    """Short alphanumeric suffix for de-duping S3 keys across retries.

    Uppercase + digits to keep the key visually parseable. SystemRandom
    isn't cryptographically required here — collisions are improbable at
    n=4 within a single project's build cadence.
    """
    alphabet = string.ascii_uppercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(n))
