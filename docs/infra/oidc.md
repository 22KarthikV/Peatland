# GitHub OIDC role for a future AWS deploy

Status: documented only. Nothing in this document has been created, and nothing in this project uses it.

## No real AWS credentials exist in this repo

No real AWS credentials exist anywhere in this repository, in its CI configuration, or in any GitHub secret for it. The only AWS-shaped values are the LocalStack dummies (`test`/`test`, `dev-only-key`), which are rejected by real AWS. CI runs `terraform fmt`, `init -backend=false`, `validate` and `terraform test` (mock providers), none of which contact AWS. The LocalStack smoke (`scripts/smoke_infra.sh`) runs locally only. Per SPEC, the AWS-only resources (`enable_aws_only`) are validated and mock-planned, never applied.

## Approach if a real deploy is ever wanted

Use GitHub's OpenID Connect so the workflow gets short-lived credentials and no long-lived access key is ever stored.

1. In the AWS account (created by a human, outside this repo), add an IAM OIDC identity provider:
   - URL: `https://token.actions.githubusercontent.com`
   - Audience: `sts.amazonaws.com`
2. Create a role per environment (`peatwatch-plan-dev`, `peatwatch-deploy-prod`) with this trust policy. The `sub` condition pins the repository and, for deploys, the branch or GitHub Environment, so forks and other branches cannot assume it.

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Principal": {"Federated": "arn:aws:iam::<ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com"},
       "Action": "sts:AssumeRoleWithWebIdentity",
       "Condition": {
         "StringEquals": {"token.actions.githubusercontent.com:aud": "sts.amazonaws.com"},
         "StringLike": {"token.actions.githubusercontent.com:sub": "repo:<OWNER>/<REPO>:environment:prod"}
       }
     }]
   }
   ```

   Use `repo:<OWNER>/<REPO>:ref:refs/heads/main` for a branch-pinned role, or `repo:<OWNER>/<REPO>:pull_request` for a read-only plan role.
3. Permissions: a plan role gets read-only access plus read/write to the state bucket (`s3:GetObject`, `PutObject`, `DeleteObject` on the lock file). A deploy role gets only what `infra/` manages (S3, SQS, Secrets Manager, IAM roles, ECS, ECR, RDS, ELB, CloudFront, Budgets, CloudWatch), scoped by resource name prefix `peatwatch-*` where the service allows it, with a permissions boundary on any role it creates.
4. The workflow requests the token and assumes the role. The role ARN is not a secret, so it is stored as a repository variable, not a secret:

   ```yaml
   permissions:
     id-token: write   # needed to request the OIDC token
     contents: read
   steps:
     - uses: aws-actions/configure-aws-credentials@v4
       with:
         role-to-assume: ${{ vars.AWS_PLAN_ROLE_ARN }}
         aws-region: eu-west-2
     - run: terraform -chdir=infra init -backend-config=... && terraform -chdir=infra plan -var-file=envs/dev.tfvars
   ```
5. Use a GitHub Environment with required reviewers for the deploy role, so an apply always waits for a human approval.
6. Remote state: the S3 backend recipe is in the comment in `infra/providers.tf` (versioned, encrypted bucket, `use_lockfile`).

## Guardrails to keep

- `use_localstack = false` is only ever set in `dev.tfvars`/`prod.tfvars` and only by a job that has assumed a role. Local runs keep the loopback-endpoint guard from `variables.tf`.
- Do not add `AWS_ACCESS_KEY_ID` or `AWS_SECRET_ACCESS_KEY` as repository secrets. If a step seems to need them, fix the role trust policy instead.
- Cost controls from `docs/infra/cost.md` (budget, alarms) are applied together with the first real deploy, not after.
