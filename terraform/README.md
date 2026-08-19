# Terraform 基础设施即代码（Task 1）

> 阿里云基础设施编排，覆盖题目要求的全部组件，5 套环境用「workspace + tfvars」双机制隔离。

---

## 一、组件清单（对照题目逐条）

| 题目要求 | 模块 | 阿里云资源 | 状态 |
|---------|------|-----------|:--:|
| 网络（选做） | — | — | 选做，省略 |
| VPC | `modules/vpc` | `alicloud_vpc` + `alicloud_vswitch` | ✅ |
| 负载均衡 | `modules/slb` | `alicloud_slb_load_balancer` | ✅ |
| 计算资源（选做） | — | — | 选做，省略 |
| **Kubernetes 集群** | `modules/ack` | `alicloud_cs_managed_kubernetes` | ✅ |
| 关系型数据库 | `modules/rds` | `alicloud_db_instance` | ✅ |
| NoSQL/Redis | `modules/redis` | `alicloud_kvstore_instance` | ✅ |
| S3/OSS 对象存储 | `modules/oss` | `alicloud_oss_bucket` | ✅ |
| 容器镜像仓库（选做） | `modules/acr` | `alicloud_cr_repository` | ✅ |

共 **7 个模块**，覆盖题目要求的全部必选组件 + 部分选做组件。

---

## 二、5 套环境怎么区分（workspace + tfvars 双机制）

题目要求「5 套环境用 workspace / 变量区分」，我这里**两个机制都用**：

### 1. workspace：隔离 state（防误操作）

Terraform 的 workspace 给每个环境一个**独立的 state 文件**，互不影响：

```
terraform workspace list          # 看有哪些 workspace
terraform workspace new dev       # 建 dev workspace
terraform workspace select prod   # 切到 prod workspace
```

**为什么重要**：不用 workspace 的话，5 个环境的 state 混在一起，`terraform destroy` 可能误删生产资源。用 workspace 后，state 天然隔离，切到哪个 workspace 操作的就是哪套环境。

### 2. tfvars：区分配置值（规格差异）

每个环境一个 tfvars 文件，定义该环境的规格：

| 环境 | RDS 规格 | Redis 规格 | ACK 节点 |
|------|---------|-----------|---------|
| dev | mysql.n2.small.1 | small | 2 × ecs.g6.large |
| test | mysql.n2.small.1 | small | 2 × ecs.g6.large |
| perf | mysql.n2.medium.1 | stand | 3 × ecs.g6.xlarge |
| staging | mysql.n2.large.1 | stand | 3 × ecs.g6.xlarge |
| production | mysql.n2.large.2（高可用） | stand | 3 × ecs.g6.xlarge |

---

## 三、完整使用流程（部署 dev 为例）

```bash
cd terraform

# 1. 初始化（下载 provider + 配置 OSS backend）
terraform init

# 2. 创建并切到 dev workspace（隔离 state）
terraform workspace new dev
terraform workspace select dev

# 3. 预览（用 dev 的配置，密码通过环境变量传入）
TF_VAR_db_password=xxx terraform plan -var-file="environments/dev.tfvars"

# 4. 部署
TF_VAR_db_password=xxx terraform apply -var-file="environments/dev.tfvars"
```

其他环境同理，切 workspace + 换对应 tfvars。

---

## 四、面试话术

> 「基础设施我用 Terraform 编排阿里云，7 个模块覆盖题目全部组件：VPC、SLB 负载均衡、ACK 集群、RDS、Redis、OSS、ACR。5 套环境用 workspace + tfvars 双机制区分——workspace 隔离每套环境的 state 文件防止误操作，tfvars 区分配置值（dev 用小规格，production 用高可用规格）。数据库密码通过 `TF_VAR_db_password` 环境变量传入，不进 git，符合安全规范。」

---

## 五、安全细节

1. **RDS 白名单**：`security_ips` 限制成 VPC 网段（不是 `0.0.0.0/0`），防止数据库裸奔公网
2. **密码不进 git**：`db_password` 用 `sensitive = true`，通过 `TF_VAR_db_password` 环境变量注入
3. **远程 state**：用阿里云 OSS 做 backend，state 不存本地（多人协作 + 防丢失）
