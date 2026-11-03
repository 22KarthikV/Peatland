# RDS PostgreSQL + PostGIS (I2). AWS-only, validate/plan only; gated on var.enable_aws_only.
#
# PostGIS on RDS is an extension, not an engine flavour: the instance is plain PostgreSQL and the
# extension is created with `CREATE EXTENSION IF NOT EXISTS postgis`. The api's first Alembic
# migration (api/alembic/versions/0001_initial.py) already does that, and the master user holds
# rds_superuser, which is enough. No shared_preload_libraries entry is needed for PostGIS.
#
# The master password is generated and stored by RDS in Secrets Manager
# (manage_master_user_password), so it is never in Terraform state, variables or git. ECS injects
# it into the tasks (ecs.tf).

variable "db_name" {
  type    = string
  default = "peatwatch"
}

variable "db_instance_class" {
  description = "Small and burstable. Graviton keeps cost down."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_allocated_storage" {
  description = "GiB of gp3 storage (autoscaling up to db_max_allocated_storage)."
  type        = number
  default     = 20
}

variable "db_max_allocated_storage" {
  type    = number
  default = 100
}

variable "db_engine_version" {
  description = "PostgreSQL major version; matches postgis/postgis:16-3.4 used locally."
  type        = string
  default     = "16"
}

locals {
  db_host       = one(aws_db_instance.this[*].address)
  db_secret_arn = try(aws_db_instance.this[0].master_user_secret[0].secret_arn, null)
}

resource "aws_db_subnet_group" "this" {
  count      = local.aws_only
  name       = "peatwatch${var.name_suffix}"
  subnet_ids = var.private_subnet_ids
}

resource "aws_db_parameter_group" "this" {
  count       = local.aws_only
  name        = "peatwatch-pg16${var.name_suffix}"
  family      = "postgres16"
  description = "PeatWatch PostgreSQL 16: TLS required"

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
}

resource "aws_security_group" "db" {
  count       = local.aws_only
  name        = "peatwatch-db${var.name_suffix}"
  description = "PostgreSQL from the Fargate tasks only"
  vpc_id      = var.vpc_id
}

resource "aws_vpc_security_group_ingress_rule" "db_from_tasks" {
  count                        = local.aws_only
  security_group_id            = aws_security_group.db[0].id
  referenced_security_group_id = aws_security_group.tasks[0].id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
  description                  = "PostgreSQL from api and worker tasks"
}

resource "aws_db_instance" "this" {
  count      = local.aws_only
  identifier = "peatwatch${var.name_suffix}"

  engine         = "postgres"
  engine_version = var.db_engine_version
  instance_class = var.db_instance_class

  allocated_storage     = var.db_allocated_storage
  max_allocated_storage = var.db_max_allocated_storage
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name                     = var.db_name
  username                    = "peatwatch"
  manage_master_user_password = true

  db_subnet_group_name   = aws_db_subnet_group.this[0].name
  parameter_group_name   = aws_db_parameter_group.this[0].name
  vpc_security_group_ids = [aws_security_group.db[0].id]
  publicly_accessible    = false
  multi_az               = var.environment == "prod"

  backup_retention_period    = var.environment == "prod" ? 7 : 1
  auto_minor_version_upgrade = true
  copy_tags_to_snapshot      = true
  deletion_protection        = var.environment == "prod"
  skip_final_snapshot        = var.environment != "prod"
  final_snapshot_identifier  = var.environment == "prod" ? "peatwatch${var.name_suffix}-final" : null
  apply_immediately          = false
}
