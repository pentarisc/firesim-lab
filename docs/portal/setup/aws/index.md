# AWS Setup

You only need this section if you intend to run on real hardware — `fslab sim fpga` and `fslab build fpga` target AWS EC2 **F2** FPGA instances. Desktop metasimulation ({doc}`/concepts/metasim-vs-fpga`) needs none of it; you can defer everything here until your design works in metasim.

The FPGA path expects a small set of AWS resources to exist before it runs: an account with an F2 service-quota increase, an SSH key pair, and two narrow IAM roles (one for the build host, one for the run host) that fslab attaches to the EC2 instances it launches. This section walks through standing those up from an empty account.

Work through the three pages in order:

1. {doc}`aws-primer` — create and secure an AWS account, set a billing guardrail (F2 instances are not cheap), and get a conceptual map of the AWS services fslab touches. Skip if you already operate an AWS account.
2. {doc}`identity-center-sso` — enable AWS IAM Identity Center, create a login identity and a `FireSim-Developer` permission set, configure `aws sso login`, and grant that permission set the one fslab-specific permission it needs (`iam:PassRole`).
3. {doc}`firesim-lab-aws-setup` — request the F2 quota, pick a region, create an SSH key pair, and create the two IAM instance-profile roles fslab references from `fslab.yaml`.

:::{note}
Two reader profiles run through these pages, and they are flagged inline where the paths diverge:

- **Solo developer** on a personal AWS account — you are your own admin and can take the shorter path.
- **Org / DevOps admin** provisioning fslab for a team that logs in through Identity Center with a pre-provisioned `FireSim-Developer` identity.
:::

```{toctree}
:maxdepth: 2

aws-primer
identity-center-sso
firesim-lab-aws-setup
```
