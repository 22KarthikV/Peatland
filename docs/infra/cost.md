# AWS cost note (estimates)

Nothing in this project is applied to real AWS (SPEC D1). This note is for someone who decides to apply `infra/envs/dev.tfvars` later. All figures are rough estimates from memory of list prices in a low-cost region, 730 hours a month, USD. Check the AWS pricing pages and the AWS Pricing Calculator before relying on them.

| Item | Config | Est. per month |
| --- | --- | --- |
| Fargate api | 0.25 vCPU, 0.5 GiB, always on | ~$9 |
| Fargate worker | 1 vCPU, 4 GiB, always on | ~$50 |
| ALB | 1 load balancer, low traffic | ~$17 |
| RDS PostgreSQL | db.t4g.micro single-AZ, 20 GiB gp3 | ~$15 |
| NAT gateway (needed for private subnets, not created here) | 1 gateway | ~$33 + data |
| CloudFront, S3, ECR, logs, Secrets Manager (3 secrets), SNS | low use | ~$3 to $6 |

Rough total for an always-on dev stack: **about $120 to $140 a month**, dominated by the worker, NAT gateway and ALB. The default budget limit (`monthly_budget_usd`) is $60, so raise it or cut the stack before applying.

Ways to cut cost: set `worker_desired_count = 0` while idle (jobs wait in SQS), use `worker_cpu = 512` / `worker_memory = 2048` if runs fit, put the tasks in public subnets with a public IP instead of a NAT (changes the security posture), and destroy the stack between uses.

## Budget alerts

`budget.tf` creates a monthly cost budget with email alerts at 50, 80 and 100 percent of actual spend and 100 percent of forecast. Set `budget_email`. Budgets only notify; they do not stop resources. Cost data can lag by about a day.

## How to destroy

```
terraform -chdir=infra destroy -var-file=envs/dev.tfvars
```

- dev: ECR repos (`force_delete`) and the web bucket (`force_destroy`) are removed with their contents; the RDS instance is deleted without a final snapshot; the Secrets Manager secret has a 0-day recovery window.
- prod: deletion protection on the ALB and RDS, a final RDS snapshot, and a 7-day secret recovery window. Set `deletion_protection` off (change the variable-driven values, apply) before destroy; the final snapshot keeps costing storage until deleted by hand.
- The Terraform state bucket (if you configured remote state) and the manually created VPC/subnets/NAT are outside this configuration. Delete them by hand or they keep billing.
- Afterwards check the Billing console and Cost Explorer for leftovers (NAT gateway, Elastic IPs, snapshots, CloudWatch log groups).
