# 部署手册

> 本文档是你明天手动部署的完整操作手册。从零开始，照着做就能把整套应用部署到 new-dev 集群。
> 每一步都有命令 + 验证方法 + 排错提示。

---

## 目录

- [第 0 章：部署前准备](#第-0-章部署前准备)
- [第 1 章：Kustomize 快速上手](#第-1-章kustomize-快速上手)
- [第 2 章：部署顺序总览](#第-2-章部署顺序总览)
- [第 3 章：逐步部署](#第-3-章逐步部署)
- [第 4 章：K8s 架构深度说明](#第-4-章k8s-架构深度说明)
- [第 5 章：常见坑与排错](#第-5-章常见坑与排错)

---

## 第 0 章：部署前准备

### 0.1 前提条件（已就绪，无需再操作）

| 项 | 状态 |
|----|------|
| kubectl 连接的集群 | ✅ `new-dev` |
| docker 登录 Harbor | ✅ 已用 robot 账号登录 |
| 业务镜像 | ✅ 已推到内网 Harbor（见 0.3） |
| Jenkins 镜像 | ✅ 已推到内网 Harbor |

### 0.2 集群现状（部署前必须知道的）

| 集群组件 | 现状 | 影响 |
|----------|------|------|
| **Ingress 网关** | 只有 Istio + Kong，**没有 ingress-nginx** | Ingress 资源能 apply，但不生效（没有 nginx controller 处理） |
| cert-manager | ✅ 已装 | webhook 证书、ClusterIssuer 能用 |
| 存储 | ✅ nfs-storage（默认 StorageClass） | postgres 能持久化 |
| External Secrets Operator (ESO) | ❌ 没装 | staging/prod 的 ExternalSecret 不会生效（但可以 apply 演示） |
| 拉镜像 Secret | ✅ default 有 swr-secret | 但我们的 namespace 需要自己配（见 3.2） |

> **重要结论**：验证服务连通性用 `kubectl port-forward`，不走 Ingress（因为集群没有 ingress-nginx）。Ingress 资源照常 apply 以满足题目要求，但网关层假设「已就绪」。

### 0.3 已推送到内网 Harbor 的镜像清单

> 所有镜像都是 amd64 架构（集群节点是 amd64，本地 Mac 是 arm64，推送时必须用 `--platform linux/amd64`）。

```
hub-sh.aijidou.com/base/marriott-backend:v1-amd64 / latest-amd64   # 后端 Flask API
hub-sh.aijidou.com/base/marriott-frontend:v1-amd64 / latest-amd64  # 前端 Nginx
hub-sh.aijidou.com/base/marriott-webhook:v1-amd64                  # Mutating Webhook
hub-sh.aijidou.com/base/postgres:15-alpine-amd64                   # PostgreSQL
hub-sh.aijidou.com/base/redis:7-alpine-amd64                       # Redis
hub-sh.aijidou.com/base/jenkins:lts-amd64                          # Jenkins
hub-sh.aijidou.com/base/k8s-sidecar:2.10.1-amd64                   # Jenkins JCasC 热加载
```

---

## 第 1 章：Kustomize 快速上手

### 1.1 什么是 Kustomize

Kustomize 是 Kubernetes 官方的配置管理工具，用「基础 + 覆盖」的方式管理多环境配置：

```
k8s/
├── base/                  # 基础配置（所有环境共享）
│   ├── kustomization.yaml
│   ├── backend-deployment.yaml
│   └── ...
└── overlays/              # 每个环境一个目录，只写「和 base 不同的地方」
    ├── dev/kustomization.yaml
    ├── test/
    ├── perf/
    ├── staging/
    └── production/
```

**核心思想**：base 写公共部分，overlay 只写差异（副本数、镜像 tag、域名、数据库地址）。

### 1.2 两条关键命令（务必分清）

```bash
# ① 只看不部署：渲染出最终 YAML，打印到屏幕（不会创建任何资源）
kubectl kustomize k8s/overlays/dev

# ② 真正部署：渲染 + apply（会真正创建资源）
kubectl apply -k k8s/overlays/dev
```

| 命令 | 作用 | 什么时候用 |
|------|------|-----------|
| `kubectl kustomize` | 只看生成什么 | 部署前先预览，确认配置正确 |
| `kubectl apply -k` | 真正部署 | 确认无误后执行 |
| `kubectl delete -k` | 删除该环境所有资源 | 清理环境 |

> **建议**：每次部署前，先用 `kubectl kustomize` 看一遍输出，确认镜像地址、副本数、域名都对，再 `apply`。

### 1.3 5 套环境的差异

| 差异项 | dev | test | perf | staging | production |
|--------|-----|------|------|---------|-----------|
| namespace | marriott-dev | marriott-test | marriott-perf | marriott-staging | marriott-production |
| 副本数 | 1 | 1 | 3 | 2 | 3 |
| 镜像 tag | latest | latest | v1 | v1 | v1 |
| HPA | 无 | 无 | 有 | 有 | 有 |
| PDB/PriorityClass | 无 | 无 | 有 | 有 | 有 |
| 数据库 | 集群内 | 集群内 | 集群内 | 云上 RDS | 云上 RDS |
| Secret 来源 | secretGenerator | secretGenerator | secretGenerator | ESO(KMS) | ESO(KMS) |

---

## 第 2 章：部署顺序总览

依赖关系决定顺序：**先部署有依赖的，后部署业务**。

```
第 1 步：创建 5 个 namespace
第 2 步：部署 dev 环境（最轻量，先练手熟悉 Kustomize）
第 3 步：部署 test 环境
第 4 步：部署 perf 环境
第 5 步：部署 webhook（独立 namespace）
第 6 步：staging / production（只渲染验证，不真部署——依赖云上 RDS + ESO）
第 7 步：部署 Jenkins（K8s 里，先装好备用）
```

> 说明：staging/production 需要阿里云 RDS/Redis（云上资源，笔试不真花钱）和 ESO（集群没装），所以**只渲染验证 YAML 正确性，不实际 apply**。面试时说明「代码完整，生产部署依赖云资源已就绪」。

---

## 第 3 章：逐步部署

### 第 1 步：创建 namespace

虽然 Kustomize overlay 里已经带了 namespace.yaml，但先手动确认：

```bash
# 查看 5 个 namespace 是否已存在（应该没有）
kubectl get namespaces | grep marriott

# 部署时 overlay 会自动创建 namespace（因为我们在 overlay 里加了 namespace.yaml）
# 所以这一步其实不需要单独做，直接进第 2 步
```

### 第 2 步：部署 dev 环境

```bash
cd /Users/babyyy/工作/marriott-devops

# ① 先预览（只看不部署）
kubectl kustomize k8s/overlays/dev

# 确认输出里：
#   - 镜像地址是 hub-sh.aijidou.com/base/marriott-backend:latest-amd64
#   - namespace 是 marriott-dev
#   - 有 postgres、redis（集群内）

# ② 真正部署
kubectl apply -k k8s/overlays/dev
```

**验证**：

```bash
# 查看 Pod 状态（等所有 Pod Running）
kubectl get pods -n marriott-dev -w

# 查看所有资源
kubectl get all -n marriott-dev
```

预期：backend、frontend、postgres、redis 四个 Pod 都是 Running。

**验证服务连通性（port-forward 绕过网关）**：

```bash
# 端口转发：把本机 8080 转到集群里 backend Service
kubectl port-forward -n marriott-dev svc/backend 8080:8080

# 另开一个终端测试
curl http://localhost:8080/healthz    # 期望 200 {"status":"ok"}
curl http://localhost:8080/readyz     # 期望 200 {"status":"ready"}
curl http://localhost:8080/api/items  # 期望返回商品列表
```

### 第 3 步：部署 test 环境

```bash
kubectl kustomize k8s/overlays/test   # 预览
kubectl apply -k k8s/overlays/test    # 部署
kubectl get pods -n marriott-test     # 验证
```

### 第 4 步：部署 perf 环境

```bash
kubectl kustomize k8s/overlays/perf   # 预览（注意这次有 HPA、PDB、PriorityClass，副本数 3）
kubectl apply -k k8s/overlays/perf    # 部署
kubectl get pods -n marriott-perf     # 验证（backend 应该有 3 个副本）
```

### 第 5 步：部署 webhook

webhook 是独立的（不在 Kustomize 里，是单独的 yaml 文件）：

```bash
# webhook 部署在自己的 namespace（webhook-system）
kubectl apply -f k8s/webhook/webhook.yaml

# 验证
kubectl get pods -n webhook-system
kubectl get MutatingWebhookConfiguration
```

> 注意：webhook 的证书依赖 cert-manager 自动签发，如果 cert-manager 工作正常，webhook Pod 会正常起来。

### 第 6 步：staging / production（只验证不部署）

```bash
# 只渲染验证，不 apply（因为依赖云上 RDS + ESO）
kubectl kustomize k8s/overlays/staging
kubectl kustomize k8s/overlays/production

# 确认输出里：
#   - 没有集群内 postgres/redis（已删除，指向云上）
#   - DB_HOST 指向 rm-xxx-prod.mysql.rds.aliyuncs.com
#   - 有 ExternalSecret 和 SecretStore（ESO）
```

### 第 7 步：部署 Jenkins（K8s 里）

Jenkins 用 Helm 部署，配置文件已经写好（`k8s/jenkins/values.yaml`）：

```bash
# ① 添加 Jenkins Helm 仓库
helm repo add jenkins https://charts.jenkins.io
helm repo update

# ② 创建 Jenkins 的 namespace
kubectl create namespace jenkins

# ③ 部署 Jenkins（用我们写好的 values.yaml，含内网镜像 + 插件 + 持久化）
helm install jenkins jenkins/jenkins \
  --namespace jenkins \
  -f k8s/jenkins/values.yaml

# ④ 查看部署状态（等 Pod Running）
kubectl get pods -n jenkins -w

# ⑤ 获取 Jenkins 访问地址（NodePort）
kubectl get svc jenkins -n jenkins

# ⑥ 获取初始管理员密码
kubectl exec -n jenkins -it $(kubectl get pods -n jenkins -l app.kubernetes.io/name=jenkins -o jsonpath='{.items[0].metadata.name}') -- cat /var/jenkins_home/secrets/initialAdminPassword
```

> 说明：Jenkins 装好后，流水线（Jenkinsfile）在项目根目录，需要手动在 Jenkins 里创建 Pipeline 任务并指向代码仓库。

### 第 8 步：Jenkins 流水线（Task 3）

CI/CD 流水线已经写好，两个 Jenkinsfile：

| 文件 | 用途 |
|------|------|
| `Jenkinsfile`（根目录） | 业务服务流水线（代码扫描→测试→镜像合规→分级部署） |
| `terraform/Jenkinsfile` | 基础设施流水线（plan→审批→apply） |

**业务服务流水线的 10 个阶段**：

1. Checkout —— 代码检出
2. Code Scan —— SonarQube 代码静态扫描
3. Unit Test —— 单元测试 + 覆盖率
4. Dependency Scan —— 依赖漏洞扫描（Dependency-Check）
5. Build Image —— 构建三个镜像
6. Image Scan —— 镜像合规检查（Trivy 高危漏洞 + 非 root）
7. Push Image —— 推送到 Harbor
8. Deploy Dev/Test/Perf —— 自动部署
9. Deploy Staging —— 人工审批 + 飞书通知
10. Deploy Production —— 二次审批 + 飞书通知

> 说明：完整流水线需要配置 Jenkins 凭证（Harbor、SonarQube、飞书 webhook 等），见 Jenkinsfile 顶部注释。

---

## 第 4 章：K8s 架构深度说明

### 4.1 5 环境 overlay 设计

用 Kustomize 的 base + overlay 模式，base 写公共配置，overlay 只写环境差异：

- **dev/test**：轻量（单副本、无 HPA、无 PDB），用 latest 镜像快速迭代
- **perf**：压测（3 副本、有 HPA、有 PDB），模拟真实负载
- **staging/prod**：生产级（多副本、HPA、PDB、PriorityClass、云上 RDS、cert-manager 正式证书）

### 4.2 Mutating Webhook（三件事）

1. **资源治理**：给没写 limits 的容器自动补默认资源限制
2. **标签规范**：给业务 Pod 自动加 team 标签
3. **可观测性联动**：识别 Python 应用，自动加 OTel 注入 annotation，交给 OTel Operator 注入 agent

### 4.3 NetworkPolicy 分层

- 集群内（dev/test/perf）：NetworkPolicy 限制 backend → postgres/redis
- 云上（staging/prod）：用阿里云 RDS 白名单限制（terraform 里 security_ips）

### 4.4 Secret 分层

- dev/test/perf：用 Kustomize secretGenerator（开发密码，不敏感）
- staging/prod：用 External Secrets Operator + 阿里云 KMS（密钥真身在 KMS，代码零明文）

> 注：集群没装 ESO，staging/prod 的 ExternalSecret 只作为代码演示，面试时说明「生产环境装 ESO 后从 KMS 拉取真实密钥」。

### 4.5 探针分离

- `/healthz`：存活探针，只检查进程是否活着
- `/readyz`：就绪探针，检查 DB + Redis 是否连通（依赖挂了摘流量，不重启容器）

---

## 第 5 章：常见坑与排错

### 5.1 镜像拉不下来

```bash
kubectl describe pod <pod名> -n marriott-dev
# 看 Events，如果是 ImagePullBackOff，说明镜像地址不对或没登录
```

**排查**：确认镜像地址是 `hub-sh.aijidou.com/base/...`，且 docker 已登录 Harbor。

### 5.2 Pod 起不来（CrashLoopBackOff）

```bash
kubectl logs <pod名> -n marriott-dev
# 看应用日志
```

**常见原因**：postgres 没起来（backend 等 DB）、Secret 缺失、配置错误。

### 5.3 就绪探针一直失败

```bash
kubectl describe pod <pod名> -n marriott-dev
# 看 Events，如果 Readiness probe failed
```

**原因**：backend 连不上 DB/Redis。先确认 postgres/redis Pod 是否 Running。

### 5.4 删除环境重来

```bash
kubectl delete -k k8s/overlays/dev   # 删除 dev 环境所有资源
kubectl apply -k k8s/overlays/dev    # 重新部署
```

---

## 附录：完整命令速查

```bash
# 预览
kubectl kustomize k8s/overlays/<env>

# 部署
kubectl apply -k k8s/overlays/<env>

# 查看状态
kubectl get pods -n marriott-<env>
kubectl get all -n marriott-<env>

# 查看日志
kubectl logs -n marriott-<env> <pod名>

# 端口转发验证
kubectl port-forward -n marriott-<env> svc/backend 8080:8080

# 删除
kubectl delete -k k8s/overlays/<env>
```
