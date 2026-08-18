# Marriott 高级 DevOps 工程师测试项目

> 三层 Hello-World 应用，从基础设施到 K8s 部署到 CI/CD 流水线的完整交付，覆盖 5 套环境（dev/test/perf/staging/production）。

---

## 项目概览

这是一个「代码即一切（Everything as Code）」的完整示例项目，演示多层架构应用的完整 DevOps 生命周期。

**技术栈**：
- 应用：Python Flask + Nginx + PostgreSQL + Redis
- 基础设施：Terraform（阿里云）
- 编排：Kubernetes + Kustomize + cert-manager + Mutating Webhook + External Secrets
- CI/CD：Jenkins（部署在 K8s 里，含代码扫描/镜像合规/飞书审批）
- 可观测性：OpenTelemetry

---

## 目录结构

```
marriott-devops/
├── src/                       # 应用源码（Task 0）
│   ├── backend/               # 后端 Flask API + OTel 埋点
│   ├── frontend/              # 前端 Nginx + 静态页
│   ├── webhook/               # Mutating Admission Webhook
│   └── db/                    # 数据库初始化脚本
├── k8s/                       # K8s manifests（Task 2）
│   ├── base/                  # 基础配置（所有环境共享）
│   ├── overlays/              # 5 套环境差异化配置
│   ├── webhook/               # Webhook 部署资源
│   ├── cert-manager/          # ClusterIssuer 定义
│   └── jenkins/               # Jenkins 部署 values.yaml
├── terraform/                 # IaC（Task 1）
│   ├── modules/               # 模块化（vpc/rds/redis/oss/acr）
│   ├── environments/          # 5 套环境 tfvars
│   └── Jenkinsfile            # 基础设施流水线（Task 3.1）
├── Jenkinsfile                # 业务服务流水线（Task 3.2）
├── scripts/                   # 飞书通知等脚本
├── docs/                      # 架构图 + 部署手册（Task 4）
├── docker-compose.yml         # 本地开发验证
└── README.md
```

---

## 文档导航

- 📖 **[部署手册](docs/deployment-guide.md)** —— 从零部署到集群的完整操作步骤
- 🏗 [应用架构设计图](docs/application-architecture.md)
- ☁️ [基础设施架构设计图](docs/infrastructure-architecture.md)
- 🔄 [CI/CD 流水线架构设计图](docs/cicd-pipeline-architecture.md)

---

## 5 套环境

| 环境 | 用途 | K8s namespace | Terraform workspace |
|------|------|---------------|---------------------|
| dev | 开发 | marriott-dev | dev |
| test | QA 测试 | marriott-test | test |
| perf | 性能压测 | marriott-perf | perf |
| staging | 预发布/UAT | marriott-staging | staging |
| production | 生产 | marriott-production | production |

---

## 快速开始

### 本地验证（docker-compose）

```bash
docker-compose up -d
curl http://localhost:8080/healthz
```

### 部署到集群（详见部署手册）

```bash
kubectl kustomize k8s/overlays/dev   # 预览
kubectl apply -k k8s/overlays/dev    # 部署 dev 环境
```

---

## 关键设计决策

1. **5 套环境用两套隔离机制**：Terraform workspace（基础设施）+ K8s namespace/overlay（应用）
2. **不同环境不同资源策略**：dev/test 用集群内轻量 DB，staging/prod 用云上 RDS
3. **健康检查分离**：`/healthz`（存活）和 `/readyz`（就绪）分开
4. **镜像 tag 策略**：dev/test 用 latest，perf/staging/prod 用 v1（commit SHA）
5. **分级发布**：dev/test/perf 自动，staging/prod 人工审批
6. **Mutating Webhook**：资源治理 + 标签规范 + OTel 自动注入联动
7. **Secret 分层**：dev/test/perf 用 secretGenerator，staging/prod 用 ESO + KMS
