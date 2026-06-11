# AWS setup & teardown

> **Status (2026-06-10): Phase 0 account setup COMPLETE.** Account created; root user
> MFA-protected and parked; `andy-admin` IAM user (AdministratorAccess + MFA) is the daily
> login; $1 `EstimatedCharges` billing alarm wired to email (SNS confirmed); region `us-east-1`.
> Per-component least-privilege IAM users are still to be created with their resources (Phase 1–2).
>
> **Status (2026-06-11): Phase 1 Amazon Connect source COMPLETE.** Upgraded account from the new
> AWS *free plan* (which blocks Connect) to the **paid plan** — credits + the $1 alarm + same-session
> number release keep exposure to pennies. Connect instance `acx-practice` (`us-east-1`); recording
> enabled in the Sample inbound flow and published; synthetic scripted calls land `.wav` in
> `amazon-connect-8ad40e47e8f8/connect/acx-practice/CallRecordings/`. CTR metadata streams via
> Kinesis Firehose `acx-ctr-stream` (Direct PUT → S3, 60s buffer) into the `connect/acx-practice/CTR/`
> prefix. **DID phone number released** after capturing calls (the only daily-billing item).

Free-tier-friendly. Do the account + identity hardening now (Phase 0); per-component
**least-privilege** IAM policies are added as their resources are created (Phases 1–2),
because you can't scope a policy to a bucket/queue that doesn't exist yet.

## Phase 0 — account + identity hardening (do once)

1. **Create the AWS account** at https://aws.amazon.com → "Create an AWS Account".
   Requires email, a payment card (free tier won't charge for what we use), phone verify.
2. **Secure the root user.** The root account can do *anything and bill anything* — treat it
   like the master key. Enable **MFA** on it (Authenticator app), then stop using it for
   daily work.
3. **Create an admin IAM user for yourself** (not for the pipeline) — IAM → Users → Add user,
   attach `AdministratorAccess`, enable MFA. You'll do console work as this user, not root.
4. **Set a billing alarm** (this is "mind the AWS bill" made real):
   - Billing console → Billing preferences → enable "Receive Free Tier Usage Alerts".
   - CloudWatch → Alarms → create alarm on the `EstimatedCharges` metric, threshold e.g. **$1**,
     notify your email via SNS. You'll get an email before anything meaningful accrues.
5. **Pick a region and stick to it** — `us-east-1` (cheapest, most services). Record it in `.env`.

## Per-component IAM (added later, scoped tight)

Each pipeline component gets its OWN IAM user/role with a policy granting **only** the actions
on **only** the ARNs it needs. Examples to come:

| Component | Needs | Will get (least privilege) |
|---|---|---|
| Connector (Phase 2) | read recordings, send to queue | `s3:GetObject` on the recordings bucket; `sqs:SendMessage` on the queue |
| Consumer (Phase 2/3) | pull from queue | `sqs:ReceiveMessage`/`DeleteMessage` on the queue + DLQ |

Each policy gets explained inline when created (IAM is an interview topic).

## Teardown (avoid surprise bills)

When done, delete in reverse order of creation:

1. **Release the Connect DID phone number** (Channels → Phone numbers → Release) — the only
   daily-billing item. *(Done at the end of Phase 1; re-claim only when actively testing.)*
2. Disable Connect **Data streaming** (CTR), then delete the **Kinesis Firehose** `acx-ctr-stream`
   and its auto-created delivery IAM role.
3. Empty + delete the S3 recordings bucket (and the `-access-logs` bucket).
4. Delete the SQS queue and DLQ (Phase 2+).
5. Delete the Amazon Connect instance (stops any per-minute/usage charges).
6. Delete per-component IAM users/policies and their access keys.
7. Delete the CloudWatch billing alarm + SNS topic.
8. Confirm $0 in the Billing console after the next cycle.
