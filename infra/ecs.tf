# AWS-only compute (I2): ECR, ECS Fargate (api + worker), ALB. Validate/plan only (SPEC D1).
#
# Everything here is gated on var.enable_aws_only (default false; false in envs/local.tfvars so
# `tflocal apply` still creates only the I1 resources, true in dev/prod). Nothing here is applied
# anywhere in this project.
#
# Networking is an input, not a resource: a VPC with public subnets (ALB) and private subnets
# (tasks, RDS) is expected to exist. No network.tf and no data-source lookups, so `validate` and
# `plan` make no AWS calls for it. Private subnets need outbound internet (NAT gateway or VPC
# endpoints) for ECR, CloudWatch Logs, Secrets Manager, SQS, S3 and the public Earth Search COGs.

variable "enable_aws_only" {
  description = "Create the AWS-only resources (ECR, ECS, ALB, RDS, CloudFront, Budgets, alarms). Must stay false for LocalStack."
  type        = bool
  default     = false
}

variable "vpc_id" {
  description = "Existing VPC for the ALB, tasks and database. Required when enable_aws_only is true."
  type        = string
  default     = null
}

variable "public_subnet_ids" {
  description = "Public subnets for the ALB (at least two AZs). Required when enable_aws_only is true."
  type        = list(string)
  default     = []
}

variable "private_subnet_ids" {
  description = "Private subnets for the Fargate tasks and RDS (at least two AZs). Tasks get no public IP."
  type        = list(string)
  default     = []
}

variable "alb_ingress_cidrs" {
  description = "CIDR blocks allowed to reach the ALB."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "alb_certificate_arn" {
  description = <<-EOT
    ACM certificate ARN (same region as the ALB) for the HTTPS listener. Null (default) creates
    only an HTTP:80 listener that forwards to the API. That variant sends the X-API-Key header in
    clear text, so set a certificate for anything other than a throwaway dev stack: with it the
    HTTP listener redirects to HTTPS:443.
  EOT
  type        = string
  default     = null
}

variable "container_user" {
  description = "Non-root user the containers run as. The api and worker Dockerfiles create and switch to 'app'; ECS enforces it even if an image forgets."
  type        = string
  default     = "app"
}

variable "api_image_tag" {
  description = "Image tag deployed for the api and worker (immutable tags; use a git SHA)."
  type        = string
  default     = "latest"
}

variable "api_cpu" {
  type    = number
  default = 256
}

variable "api_memory" {
  type    = number
  default = 512
}

variable "worker_cpu" {
  description = "The worker runs rasterio/GDAL over Sentinel-2 tiles; keep 1 vCPU / 4 GiB unless runs prove smaller."
  type        = number
  default     = 1024
}

variable "worker_memory" {
  type    = number
  default = 4096
}

variable "api_desired_count" {
  type    = number
  default = 1
}

variable "worker_desired_count" {
  type    = number
  default = 1
}

variable "titiler_base_url" {
  description = "Public base URL of the tile server (TITILER_BASE_URL). TiTiler is not part of I2; empty leaves tile links unusable."
  type        = string
  default     = ""
}

variable "log_retention_days" {
  type    = number
  default = 30
}

locals {
  aws_only = var.enable_aws_only ? 1 : 0
  services = toset(["api", "worker"])
  # Same variables as the docker-compose `api-env` anchor, minus the LocalStack-only
  # AWS_ENDPOINT_URL and static AWS keys (the task role supplies credentials on AWS).
  # DATABASE_URL is assembled at start-up from the RDS-managed secret (see db_url_prefix).
  container_env = [
    { name = "AWS_REGION", value = var.aws_region },
    { name = "DATA_BUCKET", value = aws_s3_bucket.this["data"].bucket },
    { name = "ARTIFACTS_BUCKET", value = aws_s3_bucket.this["artifacts"].bucket },
    { name = "QUEUE_NAME", value = aws_sqs_queue.jobs.name },
    { name = "API_KEY_SECRET_ID", value = aws_secretsmanager_secret.api_key.name },
    { name = "TITILER_BASE_URL", value = var.titiler_base_url },
    { name = "CORS_ORIGINS", value = local.web_origin },
    { name = "DB_HOST", value = local.db_host },
    { name = "DB_PORT", value = "5432" },
    { name = "DB_NAME", value = var.db_name },
  ]

  # ECS cannot interpolate a secret into another variable, and the app reads one DATABASE_URL.
  # The RDS-managed secret holds {"username","password"} and ECS injects them as DB_USER and
  # DB_PASSWORD; this prefix percent-encodes them into the URL before exec'ing the real command.
  db_url_prefix = <<-EOT
    export DATABASE_URL="postgresql+psycopg://$(python -c 'import os,urllib.parse as u;q=lambda k:u.quote(os.environ[k],safe="");print(q("DB_USER")+":"+q("DB_PASSWORD"),end="")')@$DB_HOST:$DB_PORT/$DB_NAME?sslmode=require" &&
  EOT

  container_commands = {
    api    = "alembic -c api/alembic.ini upgrade head && exec uvicorn api.main:app --host 0.0.0.0 --port 8000"
    worker = "exec python -m api.worker"
  }

  # Only the api listens and is health-checked (the worker polls SQS).
  service_extra = {
    api = {
      portMappings = [{ containerPort = 8000, protocol = "tcp" }]
      healthCheck = {
        command     = ["CMD-SHELL", "python -c \"import urllib.request as r;r.urlopen('http://localhost:8000/health',timeout=3)\""]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      }
    }
    worker = {}
  }

  container_secrets = [
    { name = "DB_USER", valueFrom = "${coalesce(local.db_secret_arn, "unset")}:username::" },
    { name = "DB_PASSWORD", valueFrom = "${coalesce(local.db_secret_arn, "unset")}:password::" },
  ]
}

# ---------------------------------------------------------------------------------------------
# ECR
# ---------------------------------------------------------------------------------------------

resource "aws_ecr_repository" "this" {
  for_each             = var.enable_aws_only ? toset(["api", "worker", "web"]) : toset([])
  name                 = "peatwatch/${each.key}${var.name_suffix}"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = var.environment != "prod"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "this" {
  for_each   = aws_ecr_repository.this
  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the 10 most recent images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}

# ---------------------------------------------------------------------------------------------
# Cluster, logs, roles
# ---------------------------------------------------------------------------------------------

resource "aws_ecs_cluster" "this" {
  count = local.aws_only
  name  = "peatwatch${var.name_suffix}"

  setting {
    name  = "containerInsights"
    value = "disabled" # cost; turn on when debugging
  }

  lifecycle {
    precondition {
      condition     = var.vpc_id != null && length(var.public_subnet_ids) >= 2 && length(var.private_subnet_ids) >= 2
      error_message = "enable_aws_only requires vpc_id and at least two public_subnet_ids and two private_subnet_ids."
    }
    precondition {
      condition     = var.enable_iam
      error_message = "enable_aws_only requires enable_iam = true: the task definitions use aws_iam_role.api and aws_iam_role.worker."
    }
  }
}

resource "aws_cloudwatch_log_group" "service" {
  for_each          = var.enable_aws_only ? local.services : toset([])
  name              = "/peatwatch${var.name_suffix}/${each.key}"
  retention_in_days = var.log_retention_days
}

# Execution role: pulls images, writes logs, and reads the two secrets injected into tasks. The
# task roles (aws_iam_role.api / worker, iam.tf) carry the application permissions.
resource "aws_iam_role" "task_execution" {
  count              = local.aws_only
  name               = "peatwatch-task-execution${var.name_suffix}"
  assume_role_policy = local.assume_role
}

resource "aws_iam_role_policy_attachment" "task_execution" {
  count      = local.aws_only
  role       = aws_iam_role.task_execution[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "task_execution_secrets" {
  count = local.aws_only
  name  = "peatwatch-read-injected-secrets"
  role  = aws_iam_role.task_execution[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "ReadDbSecret"
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = local.db_secret_arn
    }]
  })
}

# ---------------------------------------------------------------------------------------------
# Security groups
# ---------------------------------------------------------------------------------------------

resource "aws_security_group" "alb" {
  count       = local.aws_only
  name        = "peatwatch-alb${var.name_suffix}"
  description = "ALB: HTTP/HTTPS in, API tasks out"
  vpc_id      = var.vpc_id
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  for_each          = var.enable_aws_only ? toset(var.alb_ingress_cidrs) : toset([])
  security_group_id = aws_security_group.alb[0].id
  cidr_ipv4         = each.value
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
  description       = "HTTP"
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  for_each          = var.enable_aws_only && var.alb_certificate_arn != null ? toset(var.alb_ingress_cidrs) : toset([])
  security_group_id = aws_security_group.alb[0].id
  cidr_ipv4         = each.value
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  description       = "HTTPS"
}

resource "aws_vpc_security_group_egress_rule" "alb_to_api" {
  count                        = local.aws_only
  security_group_id            = aws_security_group.alb[0].id
  referenced_security_group_id = aws_security_group.tasks[0].id
  from_port                    = 8000
  to_port                      = 8000
  ip_protocol                  = "tcp"
  description                  = "To API tasks"
}

resource "aws_security_group" "tasks" {
  count       = local.aws_only
  name        = "peatwatch-tasks${var.name_suffix}"
  description = "Fargate tasks: API port from the ALB only; all egress"
  vpc_id      = var.vpc_id
}

resource "aws_vpc_security_group_ingress_rule" "tasks_from_alb" {
  count                        = local.aws_only
  security_group_id            = aws_security_group.tasks[0].id
  referenced_security_group_id = aws_security_group.alb[0].id
  from_port                    = 8000
  to_port                      = 8000
  ip_protocol                  = "tcp"
  description                  = "API from ALB"
}

resource "aws_vpc_security_group_egress_rule" "tasks_all" {
  count             = local.aws_only
  security_group_id = aws_security_group.tasks[0].id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
  description       = "AWS APIs, ECR, RDS, public Sentinel-2 COGs"
}

# ---------------------------------------------------------------------------------------------
# Task definitions and services
# ---------------------------------------------------------------------------------------------

resource "aws_ecs_task_definition" "service" {
  for_each                 = var.enable_aws_only ? local.services : toset([])
  family                   = "peatwatch-${each.key}${var.name_suffix}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = each.key == "api" ? var.api_cpu : var.worker_cpu
  memory                   = each.key == "api" ? var.api_memory : var.worker_memory
  execution_role_arn       = aws_iam_role.task_execution[0].arn
  task_role_arn            = each.key == "api" ? aws_iam_role.api[0].arn : aws_iam_role.worker[0].arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([merge(
    {
      name        = each.key
      image       = "${aws_ecr_repository.this[each.key].repository_url}:${var.api_image_tag}"
      essential   = true
      user        = var.container_user
      privileged  = false
      command     = ["sh", "-c", "${trimspace(local.db_url_prefix)} ${local.container_commands[each.key]}"]
      environment = local.container_env
      secrets     = local.container_secrets
      linuxParameters = {
        initProcessEnabled = true
      }
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.service[each.key].name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = each.key
        }
      }
    },
    local.service_extra[each.key],
  )])
}

resource "aws_ecs_service" "api" {
  count                             = local.aws_only
  name                              = "api"
  cluster                           = aws_ecs_cluster.this[0].id
  task_definition                   = aws_ecs_task_definition.service["api"].arn
  desired_count                     = var.api_desired_count
  launch_type                       = "FARGATE"
  health_check_grace_period_seconds = 90

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.tasks[0].id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api[0].arn
    container_name   = "api"
    container_port   = 8000
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  depends_on = [aws_lb_listener.http]
}

resource "aws_ecs_service" "worker" {
  count           = local.aws_only
  name            = "worker"
  cluster         = aws_ecs_cluster.this[0].id
  task_definition = aws_ecs_task_definition.service["worker"].arn
  desired_count   = var.worker_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.tasks[0].id]
    assign_public_ip = false
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
}

# ---------------------------------------------------------------------------------------------
# ALB for the API
# ---------------------------------------------------------------------------------------------

resource "aws_lb" "api" {
  count                      = local.aws_only
  name                       = "peatwatch-api${var.name_suffix}"
  load_balancer_type         = "application"
  internal                   = false
  subnets                    = var.public_subnet_ids
  security_groups            = [aws_security_group.alb[0].id]
  drop_invalid_header_fields = true
  enable_deletion_protection = var.environment == "prod"
}

resource "aws_lb_target_group" "api" {
  count       = local.aws_only
  name        = "peatwatch-api${var.name_suffix}"
  port        = 8000
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id

  deregistration_delay = 30

  # GET /health (api/src/api/main.py) answers 200 without the API key.
  health_check {
    path                = "/health"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

# HTTP:80. Forwards to the API unless a certificate is supplied, in which case it redirects.
resource "aws_lb_listener" "http" {
  count             = local.aws_only
  load_balancer_arn = aws_lb.api[0].arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = var.alb_certificate_arn == null ? "forward" : "redirect"
    target_group_arn = var.alb_certificate_arn == null ? aws_lb_target_group.api[0].arn : null

    dynamic "redirect" {
      for_each = var.alb_certificate_arn == null ? [] : [1]
      content {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }
  }
}

resource "aws_lb_listener" "https" {
  count             = var.enable_aws_only && var.alb_certificate_arn != null ? 1 : 0
  load_balancer_arn = aws_lb.api[0].arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.alb_certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api[0].arn
  }
}
