# 基础设施架构设计

> 本文档描述 Marriott 项目的阿里云基础设施：Terraform 7 个模块覆盖题目全部组件、5 套环境的 workspace + tfvars 双机制、安全设计、与 K8s 的联动方式。
>
> 代码位置：`terraform/` 目录（main.tf、variables.tf、outputs.tf、environments/*.tfvars、modules/、Jenkinsfile）。

---

## 一、组件总览（7 个模块，对照题目逐条）

| 题目要求 | 模块 | 阿里云资源 | 状态 |
|---------|------|-----------|:--:|
| VPC 虚拟私有云 | `modules/vpc` | `alicloud_vpc` + `alicloud_vswitch` | ✅ |
| 负载均衡 | `modules/slb` | `alicloud_slb_load_balancer` | ✅ |
| **Kubernetes 集群** | `modules/ack` | `alicloud_cs_managed_kubernetes` | ✅ |
| 关系型数据库 | `modules/rds` | `alicloud_db_instance`（MySQL 8.0） | ✅ |
| NoSQL/Redis | `modules/redis` | `alicloud_kvstore_instance`（Redis 7.0） | ✅ |
| S3/OSS 对象存储 | `modules/oss` | `alicloud_oss_bucket` | ✅ |
| 容器镜像仓库 | `modules/acr` | `alicloud_cr_repository` | ✅ |

共 **7 个模块**，覆盖题目要求的全部必选组件 + 选做组件。

```
terraform/
├── main.tf              # 主配置，调用 7 个模块
├── variables.tf         # 变量定义（db_password 标记 sensitive）
├── outputs.tf           # 输出（rds_endpoint/redis_endpoint/oss_bucket/vpc_id/slb_address）
├── environments/        # 5 套环境的 tfvars
│   ├── dev.tfvars
│   ├── test.tfvars
│   ├── perf.tfvars
│   ├── staging.tfvars
│   └── production.tfvars
├── Jenkinsfile          # 基础设施流水线（Validate→Plan→Approval→Apply）
└── modules/             # 7 个模块
    ├── vpc/             # 网络：alicloud_vpc + alicloud_vswitch
    ├── slb/             # 负载均衡：alicloud_slb_load_balancer
    ├── ack/             # K8s 集群：alicloud_cs_managed_kubernetes
    ├── rds/             # 关系型数据库：alicloud_db_instance（MySQL 8.0）
    ├── redis/           # 缓存：alicloud_kvstore_instance（Redis 7.0）
    ├── oss/             # 对象存储：alicloud_oss_bucket
    └── acr/             # 镜像仓库：alicloud_cr_repository
```

---

## 二、5 套环境隔离（workspace + tfvars 双机制）

题目要求「5 套环境用 workspace / 变量区分」，本项目**两个机制都用**：

### 1. workspace：隔离 state（防误操作）

Terraform workspace 给每个环境一个**独立的 state 文件**，互不影响：

```
terraform workspace list          # 看有哪些 workspace
terraform workspace new dev       # 建 dev workspace
terraform workspace select prod   # 切到 prod workspace
```

**为什么重要**：不用 workspace 的话，5 个环境的 state 混在一起，`terraform destroy` 可能误删生产资源。用 workspace 后，state 天然隔离，切到哪个 workspace 操作的就是哪套环境。

### 2. tfvars：区分配置值（规格差异）

每个环境一个 tfvars 文件，定义该环境的规格（与 `terraform/environments/*.tfvars` 完全一致）：

| 环境 | VPC 网段 | RDS 规格 | Redis 规格 | ACK Worker | 用途 |
|------|----------|----------|-----------|-----------|------|
| dev | 10.0.0.0/16 | mysql.n2.small.1 | redis.master.small.default | 2 × ecs.g6.large | 开发 |
| test | 10.1.0.0/16 | mysql.n2.small.1 | redis.master.small.default | 2 × ecs.g6.large | QA 测试 |
| perf | 10.2.0.0/16 | mysql.n2.medium.1 | redis.master.stand.default | 3 × ecs.g6.xlarge | 压测 |
| staging | 10.3.0.0/16 | mysql.n2.large.1 | redis.master.stand.default | 3 × ecs.g6.xlarge | 预发布/UAT |
| production | 10.4.0.0/16 | mysql.n2.large.2（高可用） | redis.master.stand.default | 3 × ecs.g6.xlarge | 生产 |

每套环境 VPC 网段独立（10.0/10.1/10.2/10.3/10.4），避免环境间互通时路由冲突。

---

## 三、模块详解

| 模块 | 资源 | 关键配置 |
|------|------|---------|
| **vpc** | `alicloud_vpc` + `alicloud_vswitch` | 每环境独立网段（10.x.0.0/16） |
| **slb** | `alicloud_slb_load_balancer` | 应用入口流量分发，输出 `slb_address` |
| **ack** | `alicloud_cs_managed_kubernetes` | 托管集群，Worker 节点规格/数量按环境配置（2-3 台） |
| **rds** | `alicloud_db_instance` | MySQL 8.0，20GB；白名单 = VPC 网段；production 自动 `HighAvailability`（多可用区），其他 `Basic` |
| **redis** | `alicloud_kvstore_instance` | Redis 7.0；白名单 = VPC 网段 |
| **oss** | `alicloud_oss_bucket` | 桶名 `marriott-<env>-assets` |
| **acr** | `alicloud_cr_repository` | 仓库名 `marriott` |

---

## 四、安全设计

1. **RDS/Redis 白名单**：`security_ips = [var.vpc_cidr]`（只允许 VPC 内网访问，不用 0.0.0.0/0——那是裸奔）
2. **生产 RDS 高可用**：`category = var.environment == "production" ? "HighAvailability" : "Basic"`（多可用区）
3. **密码不进 git**：`db_password` 变量标记 `sensitive = true`，通过 `TF_VAR_db_password` 环境变量传入
4. **远程 state**：`backend "oss"`（bucket `marriott-terraform-state`），state 不存本地——多人协作 + 防丢失

---

## 五、与 K8s 的联动（staging/production 数据层）

`terraform apply` 后，`outputs.tf` 输出的 RDS/Redis endpoint 注入到 K8s 的 staging/production overlay：

```
terraform output rds_endpoint    # rm-xxxx.mysql.rds.aliyuncs.com
terraform output redis_endpoint  # r-xxxx.redis.rds.aliyuncs.com
```

overlay 里 ConfigMap 的 `__RDS_ENDPOINT__` / `__REDIS_ENDPOINT__` 占位符由 terraform/Jenkinsfile apply 阶段替换（k8s/overlays/staging、k8s/overlays/production 的 app-config）。

---

## 六、使用流程（部署 dev 为例）

```bash
cd terraform

# 1. 初始化（下载 provider + 配置 OSS backend）
terraform init

# 2. 创建并切到 dev workspace（隔离 state）
terraform workspace new dev
terraform workspace select dev

# 3. 预览（密码通过环境变量传入，不进 git）
TF_VAR_db_password=xxx terraform plan -var-file="environments/dev.tfvars"

# 4. 部署
TF_VAR_db_password=xxx terraform apply -var-file="environments/dev.tfvars"
```

其他环境同理：切 workspace + 换对应 tfvars。

**生产环境变更**走 `terraform/Jenkinsfile` 流水线（Validate → Plan → 人工审批 → Apply，production 二次确认），阿里云 AK/SK 用 Jenkins credentials 注入。

---

## 七、面试话术

> 「基础设施我用 Terraform 编排阿里云，7 个模块覆盖题目全部组件：VPC、SLB 负载均衡、ACK 集群、RDS、Redis、OSS、ACR。5 套环境用 workspace + tfvars 双机制区分——workspace 隔离每套环境的 state 文件防止误操作，tfvars 区分配置值（dev 用小规格，production 用高可用规格）。数据库密码通过 TF_VAR_db_password 环境变量传入，不进 git，符合安全规范。RDS/Redis 白名单只开 VPC 网段，生产 RDS 自动高可用，远程 state 存 OSS。」

---

## 八、与代码的对应关系

| 内容 | 代码文件 | 说明 |
|------|---------|------|
| 主配置 | `terraform/main.tf` | 调用 7 个模块 + OSS backend |
| 变量定义 | `terraform/variables.tf` | region/environment/vpc_cidr/规格/db_password（sensitive） |
| 输出 | `terraform/outputs.tf` | rds_endpoint/redis_endpoint/oss_bucket/vpc_id/slb_address |
| 环境配置 | `terraform/environments/*.tfvars` | 每环境规格 |
| 模块 | `terraform/modules/` | 7 个模块 |
| 基础设施流水线 | `terraform/Jenkinsfile` | Validate → Plan → Approval → Apply |
