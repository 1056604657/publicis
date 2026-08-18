# Terraform 主配置
# Task 1：基础设施即代码（阿里云）
# 5 套环境通过 workspace 区分，用 variables 参数化
#
# 使用方式：
#   terraform workspace new dev
#   terraform apply -var-file="environments/dev.tfvars"

terraform {
  required_version = ">= 1.5"

  required_providers {
    alicloud = {
      source  = "aliyun/alicloud"
      version = "~> 1.0"
    }
  }

  # 远程状态（阿里云 OSS 做 backend，多环境共享状态）
  backend "oss" {
    bucket = "marriott-terraform-state"
    prefix = "marriott"
  }
}

provider "alicloud" {
  region = var.region
}

# ============================================================
# 网络（VPC + 交换机，选做但建议保留，体现完整架构）
# ============================================================
module "vpc" {
  source = "./modules/vpc"

  vpc_name    = "marriott-${var.environment}"
  vpc_cidr    = var.vpc_cidr
  region      = var.region
  environment = var.environment
}

# ============================================================
# 关系型数据库 RDS（必选）
# ============================================================
module "rds" {
  source = "./modules/rds"

  environment    = var.environment
  instance_class = var.rds_instance_class
  db_name        = "app"
  db_user        = "app"
  db_password    = var.db_password
  vpc_id         = module.vpc.vpc_id
  vswitch_id     = module.vpc.vswitch_id
  vpc_cidr       = var.vpc_cidr
}

# ============================================================
# Redis（必选）
# ============================================================
module "redis" {
  source = "./modules/redis"

  environment   = var.environment
  instance_type = var.redis_instance_type
  vpc_id        = module.vpc.vpc_id
  vswitch_id    = module.vpc.vswitch_id
  vpc_cidr      = var.vpc_cidr
}

# ============================================================
# OSS 对象存储（必选，用于备份/静态资源）
# ============================================================
module "oss" {
  source = "./modules/oss"

  environment = var.environment
  bucket_name = "marriott-${var.environment}-assets"
}

# ============================================================
# 容器镜像仓库 ACR（选做）
# ============================================================
module "acr" {
  source = "./modules/acr"

  environment = var.environment
  repo_name   = "marriott"
}
