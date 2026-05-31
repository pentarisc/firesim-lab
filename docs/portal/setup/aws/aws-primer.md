# AWS Primer

This page gets you from "no AWS account" to "an account that is safe to run fslab's FPGA path on." It is written for an engineer with a hardware background who has not operated AWS before. If you already run an AWS account day to day, skim the {ref}`service map <aws-service-map>` at the bottom and move on to {doc}`identity-center-sso`.

You do **not** need any of this for desktop metasimulation — only for `fslab build fpga` and `fslab sim fpga`, which launch real EC2 F2 instances.

## Create an AWS account

Go to [aws.amazon.com](https://aws.amazon.com/) and create an account. You will need an email address, a payment card, and a phone number for verification. The email and password you set here become the **root user** — the account's owner, with unrestricted access to everything including billing and account closure.

Account creation takes a few minutes; activation of all services can take up to a few hours.

## Secure the root user

The root user is too powerful for daily work, and a compromised root user means a compromised account. Lock it down immediately and then stop using it:

- **Enable MFA on the root user.** In the AWS console, open the account menu → *Security credentials* → *Multi-factor authentication* and register an authenticator app or hardware key.
- **Do not create access keys for the root user.** If any exist, delete them.
- **Do not log in as root for routine tasks.** You will create a separate everyday identity in {doc}`identity-center-sso`. Reserve root for the handful of operations that genuinely require it (closing the account, changing the support plan, some billing settings).

:::{warning}
Treat the root credentials like the keys to a safe. Anyone with them can run unlimited F2 instances on your card. MFA is not optional here.
:::

## Set a billing guardrail

F2 instances are powerful and priced accordingly — an `f2.6xlarge` runs on the order of a few dollars per hour, and a bitstream build can keep an instance busy for ~90 minutes. A forgotten running instance is the most common way to get a surprising bill. Put a guardrail in place before you launch anything:

- **Enable billing alerts.** *Billing and Cost Management* → *Billing preferences* → enable *Receive Billing Alerts*.
- **Create a budget with a notification.** *Billing and Cost Management* → *Budgets* → create a monthly cost budget (for example, \$100) with an email alert at 80% and 100% of the threshold. This does not cap spending — it warns you — so it is a backstop, not a wall.
- **Get in the habit of stopping or terminating instances** as soon as a build or run finishes. fslab's detached flows help here (`fslab abandon run` cleans up a remote), but the account-level budget is your safety net if something is left running.

## Regions and availability

AWS is divided into **regions** (for example, `us-west-2` in Oregon), each an independent geographic deployment, and each region into **availability zones**. Almost every resource you create — instances, key pairs, S3 buckets, FPGA images — lives in a specific region and is not visible from others.

This matters for fslab because **F2 instances exist only in a subset of regions.** As of this writing F2 is available in US East (N. Virginia, `us-east-1`), US West (Oregon, `us-west-2`), Canada Central, Europe (Frankfurt, London), and Asia Pacific (Sydney, Tokyo, Seoul) — but the smaller `f2.6xlarge` size is only in a few of those (`us-east-1`, `us-west-2`, London). Pick a region that offers the F2 size you want and use it consistently. {doc}`firesim-lab-aws-setup` covers region choice and the related FPGA-image (AGFI) region constraint in detail.

(aws-service-map)=
## What AWS services fslab touches

You do not need to be an AWS expert, but it helps to recognize the handful of services in play when fslab runs the FPGA path:

EC2 (Elastic Compute Cloud)
: Virtual machines. fslab launches an EC2 instance to build the bitstream and an F2 instance to run it. The **FPGA Developer AMI** — a prebuilt machine image with Xilinx Vivado, the AWS CLI, and the `aws-fpga` tooling — is what these instances boot from. New to EC2? Walk through AWS's [Get started with Amazon EC2](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/EC2_GetStarted.html) tutorial (launch, connect, terminate) on a free-tier instance first.

EC2 key pairs
: How fslab authenticates over SSH to the build and run instances. You create one in {doc}`firesim-lab-aws-setup` (AWS generates it and you download the private key) or import a key you generated locally. A framework-launched (`ec2_launch`) instance trusts the key at launch; for a host you manage yourself ({doc}`/setup/external-host`) you install the public key on the host directly.

EC2 FPGA images (AFI / AGFI)
: When a build finishes, the design is registered as an **Amazon FPGA Image (AFI)**, identified by an **AGFI** id. The run host loads an AGFI onto the FPGA with `fpga-load-local-image`. AFIs are region-scoped, like other resources. For background on the FPGA workflow, see the [AWS EC2 FPGA Development Kit](https://github.com/aws/aws-fpga) repository and the [F2 Developer Kit documentation](https://awsdocs-fpga-f2.readthedocs-hosted.com/). fslab automates these steps for you — you do not run the kit by hand.

S3 (Simple Storage Service)
: Object storage. The build host stages the design checkpoint (a "DCP" tarball) in an S3 bucket so the AFI-registration service can read it back. New to S3? AWS's [Getting started with Amazon S3](https://docs.aws.amazon.com/AmazonS3/latest/userguide/GetStartedWithS3.html) tutorial covers creating a bucket and uploading an object.

IAM (Identity and Access Management)
: Who can do what. fslab uses three IAM concepts: **roles** (a set of permissions an instance can assume), **instance profiles** (the wrapper that attaches a role to an EC2 instance at launch), and — through Identity Center — **permission sets** (the federated login identity you use). {doc}`firesim-lab-aws-setup` and {doc}`identity-center-sso` create the specific ones fslab needs.

Service Quotas
: Per-account limits. The "Running On-Demand F instances" quota starts at **0 vCPUs**, so you must request an increase before any F2 instance will launch.

VPC (Virtual Private Cloud)
: The network your instances live in. The account's default VPC is sufficient for fslab; you mainly need inbound SSH so the driver host is reachable.

## Where next

- **Solo developer**: you are your own admin. Continue to {doc}`identity-center-sso` to create a non-root login identity and CLI access, then {doc}`firesim-lab-aws-setup` for the F2 quota, key pair, and IAM roles.
- **Org / DevOps admin**: your team likely already has accounts, billing, and Identity Center. Skip ahead to {doc}`identity-center-sso` for the fslab permission-set grant and {doc}`firesim-lab-aws-setup` for the instance-profile roles.
