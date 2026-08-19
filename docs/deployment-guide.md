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

### 第 1 步：创建 namespace + 复制镜像拉取凭证

**这一步很关键，漏了会导致所有 Pod 拉镜像失败（ImagePullBackOff）。**

```bash
# ① 创建 5 个 namespace（也可以直接 apply overlay，会自动创建）
kubectl create namespace marriott-dev
kubectl create namespace marriott-test
kubectl create namespace marriott-perf
kubectl create namespace marriott-staging
kubectl create namespace marriott-production

# ② 复制镜像拉取凭证到每个 namespace（必须！否则拉 hub-sh.aijidou.com 镜像会报 no basic auth）
# 从 default namespace 的 swr-secret 复制（里面存了 Harbor 的登录凭证）
for ns in dev test perf staging production; do
  kubectl get secret swr-secret -n default -o jsonpath='{.data.\.dockerconfigjson}' | \
    base64 -d > /tmp/dockerconfig.json
  kubectl create secret generic swr-secret -n marriott-$ns \
    --from-file=.dockerconfigjson=/tmp/dockerconfig.json \
    --type=kubernetes.io/dockerconfigjson
done

# ③ 验证 secret 都创建好了
kubectl get secret swr-secret -n marriott-dev
kubectl get secret swr-secret -n marriott-test
kubectl get secret swr-secret -n marriott-perf
kubectl get secret swr-secret -n marriott-staging
kubectl get secret swr-secret -n marriott-production
```

> ⚠️ **为什么必须这一步**：内网 Harbor（hub-sh.aijidou.com）拉镜像需要登录凭证，而 Secret 是 namespace 隔离的——default 的 swr-secret 不会被 marriott-dev 用到。所以每个 namespace 都要有自己的 swr-secret。deployment 里已经配了 `imagePullSecrets: swr-secret`，这里只是把 secret 创建出来。

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
10. Deploy Production —— 二次审批 + **Istio header 灰度**（测试组先验证，通过后全量切换）

> 说明：完整流水线需要配置 Jenkins 凭证（Harbor、SonarQube、飞书 webhook 等），见 Jenkinsfile 顶部注释。

### 第 9 步：Istio 灰度发布（选做加分项）

生产环境的灰度发布用 Istio（header 定向），配置文件在 `k8s/istio/`：

```bash
# 1. 部署两个版本（blue 旧版 + green 新版，带 version 标签）
#    （Jenkinsfile 的灰度发布会自动创建）

# 2. 应用 Istio 灰度配置
kubectl apply -f k8s/istio/destinationrule.yaml   # 定义 v1(blue)/v2(green) 子集
kubectl apply -f k8s/istio/virtualservice.yaml    # header 路由规则
kubectl apply -f k8s/istio/gateway.yaml           # 集群入口网关

# 3. 验证灰度（带 header 访问新版，不带走旧版）
curl -H "X-User-Group: beta" http://<gateway>/api/items   # 走 v2 新版
curl http://<gateway>/api/items                            # 走 v1 旧版

# 4. 测试通过后，全量切换
kubectl apply -f k8s/istio/virtualservice-full.yaml        # 100% 流量切到 v2
```

> 灰度原理：VirtualService 按 header 路由——带 `X-User-Group: beta` 的测试组用户先访问新版，其他人走旧版。测试通过后，apply `virtualservice-full.yaml` 把 100% 流量切到新版，旧版保留作回滚点。详见 `k8s/istio/README.md`。

---

## 第 4 章：K8s 架构深度说明（面试重点，每个配置都讲清楚）

> 这一章是面试的核心。每个组件都讲清楚：**它是什么、为什么加、每个关键字段是什么意思、面试官会怎么问、你怎么答**。明天部署完，把这一章吃透，面试基本不会答不上来。

### 4.1 探针分离（healthz vs readyz）—— 最常问的面试题

**是什么**：K8s 探针分两种，用途完全不同：

| 探针 | 路径 | 检查什么 | 失败后果 |
|------|------|---------|---------|
| 存活探针 livenessProbe | `/healthz` | 进程还活着吗 | 重启容器 |
| 就绪探针 readinessProbe | `/readyz` | 能对外服务吗（DB/Redis 通吗） | 从 Service 摘除，不重启 |

**为什么分离**（这是核心，面试必问）：

> 假设 DB 挂了。如果只有一个探针（比如只检查进程），会发生什么？
> - 进程是活的（只是连不上 DB），探针返回正常 → 流量继续打到这个 Pod → 但 Pod 处理不了请求 → 用户看到 500
> - 如果探针失败就重启 → 重启也没用（DB 还是挂的），陷入 CrashLoop

**正确做法**：
- `/healthz`（存活）：进程活着就 200。DB 挂了不影响，进程不用重启
- `/readyz`（就绪）：DB + Redis 都通才 200。DB 挂了返回 503，K8s 把它从 Service 摘除，流量转到其他健康的 Pod

**面试话术**：
> 「我把存活探针和就绪探针分离。存活探针只检查进程是否活着，就绪探针检查依赖（DB/Redis）是否连通。这样 DB 挂了的时候，容器不会被反复重启（重启也没用），而是被就绪探针摘除流量，等 DB 恢复自动加回来。这是 K8s 生产实践的关键——区分『进程死了』和『依赖挂了』两种完全不同的故障。」

**关键字段**：
```yaml
livenessProbe:        # 存活探针
  httpGet:            # HTTP 方式探测
    path: /healthz    # 探测路径
    port: 8080
  initialDelaySeconds: 10   # 启动后等 10 秒再开始探测（给应用启动时间）
  periodSeconds: 10         # 每 10 秒探测一次
```

### 4.2 Kustomize 5 环境 overlay —— 为什么用 base + overlay

**是什么**：Kustomize 是 K8s 官方配置管理工具，用「基础 + 覆盖」管理多环境。

**为什么不用 Helm**（面试可能问）：
- Helm 用模板（Go template），复杂、有学习成本
- Kustomize 是**纯 YAML 叠加**，base 写公共配置，overlay 只写差异，声明式、直观
- Kustomize 是 `kubectl` 内置的，不需要额外安装

**为什么不用「复制 5 份完整 YAML」**：
- 复制 5 份，改一个公共配置要改 5 处，容易漏
- base + overlay：公共配置改 1 处，环境差异各自维护，不会互相影响

**面试话术**：
> 「我用 Kustomize 的 base + overlay 管理 5 套环境。base 写所有环境共享的配置（Deployment、Service、探针），overlay 只写每个环境的差异（dev 单副本、perf 3 副本 + HPA、生产指向云上 RDS）。这样一套代码管 5 个环境，公共改动只改 base 一处，环境差异清晰隔离。」

**关键机制**：
- `replicas`：overlay 覆盖副本数（dev=1，perf=3）
- `images`：overlay 覆盖镜像 tag（dev=latest，perf=v1）
- `patches`：overlay 增删改 base 的资源（如 dev 删掉 HPA、删掉 PDB）
- `$patch: delete`：删除 base 里不需要的资源

### 4.3 HPA（水平自动扩缩容）—— 为什么只有 perf/staging/prod 有

**是什么**：HPA 根据 CPU/内存使用率自动增减 Pod 副本数。

**为什么 dev/test 不用**：
- dev/test 是开发测试环境，负载低且可预期，固定副本数就够
- 开了 HPA 反而增加复杂度（开发时副本数忽多忽少，排障麻烦）

**为什么 perf 必须有**：
- perf 是压测环境，要模拟真实生产的高负载 + 弹性扩缩容
- 压测时看 HPA 能不能正确扩容，这是验证生产容量规划的关键

**面试话术**：
> 「我 HPA 只在 perf/staging/prod 开，dev/test 不开。因为开发测试环境负载低，固定副本数够用，开 HPA 反而干扰排障。perf 环境必须开 HPA，因为压测就是要验证高负载下能不能自动扩容。」

**关键字段**：
```yaml
spec:
  minReplicas: 2        # 最小副本数
  maxReplicas: 10       # 最大副本数
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          averageUtilization: 70   # CPU 平均使用率超过 70% 就扩容
```

### 4.4 PDB（Pod 中断预算）—— 保证节点维护时服务不中断

**是什么**：PDB 告诉 K8s「这个服务至少要有几个副本在跑」，防止节点维护/驱逐时把服务搞挂。

**为什么需要**：
- 假设生产 3 个副本，某天运维要维护一个节点，K8s 会驱逐该节点上的 Pod
- 如果没有 PDB，K8s 可能同时驱逐 2-3 个副本，服务瞬间没副本可用
- 有 PDB（如 `minAvailable: 2`），K8s 会保证任何时候至少 2 个副本在跑，驱逐是「滚动」的

**面试话术**：
> 「我加了 PDB，生产环境保证至少 N-1 个副本可用。这样节点维护、滚动升级时，K8s 不会把所有副本同时驱逐，服务始终有副本在对外服务，实现零停机维护。」

**关键字段**：
```yaml
spec:
  minAvailable: 2       # 至少 2 个副本可用（3 副本时最多驱逐 1 个）
  selector:
    matchLabels:
      app: backend
```

### 4.5 PriorityClass（优先级）—— 保证关键服务优先调度

**是什么**：PriorityClass 给 Pod 标优先级，节点资源紧张时，高优先级 Pod 优先被调度、低优先级先被驱逐。

**为什么需要**：
- 集群资源紧张时，如果 backend（业务核心）和某些不重要任务抢资源，没有优先级就可能 backend 调度失败
- 给 backend/frontend 标高优先级，保证关键服务在资源紧张时也能跑

**面试话术**：
> 「我给核心服务配了 PriorityClass，标记为高优先级。这样集群资源紧张或节点故障时，关键业务 Pod 优先被调度、最后被驱逐，保证核心服务稳定。」

### 4.6 NetworkPolicy（网络隔离）—— 最小访问权限

**是什么**：NetworkPolicy 是「集群内防火墙」，控制 Pod 之间的流量。

**为什么需要**（安全基线）：
- 默认情况下，K8s 集群内所有 Pod 可以互相访问（无隔离）
- 这意味着：如果某个 Pod 被攻破，攻击者可以访问同集群的所有其他服务（包括数据库）
- NetworkPolicy 做「白名单」：只允许 backend 访问 postgres/redis，其他 Pod 一律拒绝

**三条策略**：
1. `postgres-allow-backend`：只有 backend 能访问 postgres 的 5432
2. `redis-allow-backend`：只有 backend 能访问 redis 的 6379
3. `backend-allow-frontend`：只有 frontend 能访问 backend 的 8080

**面试话术**：
> 「我加了 NetworkPolicy 做最小访问控制。默认所有 Pod 互相隔离，只有明确声明的流量才放行——backend 能访问 postgres 和 redis，frontend 能访问 backend，其他 Pod 一律拒绝。这是安全基线，防止某个服务被攻破后横向移动攻击数据库。」

**重要说明**：NetworkPolicy 只对「集群内」的流量生效。staging/prod 的数据库在云上 RDS，集群内没有 postgres/redis，所以用**阿里云 RDS 白名单**（`security_ips` 限制成 VPC 网段）来限制访问——这是「集群内 vs 集群外」两层安全边界。

### 4.7 ServiceAccount + RBAC（最小权限）

**是什么**：ServiceAccount 是 Pod 的身份，RBAC 控制这个身份能做什么。

**为什么不用 default SA**：
- 每个 namespace 有个 `default` ServiceAccount，所有 Pod 默认用它
- 如果所有 Pod 用同一个 default SA，权限无法区分，也无法做最小权限
- 给 backend/frontend 各建独立 SA，权限清晰、可审计

**面试话术**：
> 「我给 backend 和 frontend 分别建了独立的 ServiceAccount，不用默认的。这样每个服务的身份独立，配合 RBAC 可以做最小权限——比如 backend 只需要读自己的 Secret，不需要集群级的任何权限。」

### 4.8 Secret 分层（dev 用 generator，prod 用 KMS）

**是什么**：数据库密码是敏感信息，不同环境用不同的管理方式。

| 环境 | 方式 | 为什么 |
|------|------|--------|
| dev/test/perf | Kustomize secretGenerator（明文在 yaml） | 开发密码不敏感，简单方便 |
| staging/prod | External Secrets Operator + 阿里云 KMS | 生产密钥真身在 KMS，代码零明文 |

**为什么生产不能用明文 secret**：
- 代码即一切要求所有配置进 git，但**明文密码不能进 git**（泄露风险）
- 解法：生产用 External Secrets Operator，从阿里云 KMS 拉取密钥，git 里只有「声明要拉哪个 key」，没有明文

**面试话术**：
> 「我做了 Secret 分层。dev/test/perf 用 Kustomize 的 secretGenerator 生成开发密码，简单方便。staging/prod 用 External Secrets Operator 从阿里云 KMS 拉取密钥——密钥真身在 KMS，git 里只有 ExternalSecret 声明，没有明文密码。这体现『密钥管理和代码分离』的安全意识。」

### 4.9 Mutating Webhook（三件事）—— K8s 深度加分项

**是什么**：Mutating Webhook 是 K8s 的准入控制机制——Pod 创建前，K8s 先把 Pod 定义发给你的 webhook，webhook 可以「修改」Pod 定义（比如自动加字段），K8s 再按修改后的定义创建 Pod。webhook 本质是一个 HTTPS 服务，返回 JSON Patch 给 apiserver 应用。

**我写的 webhook 做三件事**：
1. **资源治理**：给没写 `resources.limits` 的容器自动补默认值（防止有人忘配资源限制，导致 Pod 抢占资源）
2. **标签规范**：给业务 Pod 自动加 `team: platform` 标签（统一标签，方便成本分摊、权限管理）
3. **可观测性联动**：**读镜像的 OCI metadata label（`language=python`）自动识别 Python 应用**，自动加 OTel 注入 annotation，交给 OTel Operator 注入 agent

**第三件事的关键设计——为什么不猜镜像名**：

> 识别 Python 应用不能靠「镜像名含 python」这种猜测（生产镜像名都是业务名，如 `marriott-backend`，不会含 python）。正确做法是读镜像的 OCI metadata——构建镜像时用 `LABEL language=python` 声明一次，webhook 调 Harbor registry API 读镜像 config 里的 label，自动识别。用户部署时零感知。

**为什么「读 metadata」比「打 deployment 标签」更好**（面试关键）：
- 如果要在 deployment 里打 `language: python` 标签，那用户直接打 OTel 注入注解就行，webhook 就失去「自动发现」的价值了
- 正确设计：**构建时声明一次语言，部署时 webhook 自动识别，用户什么都不用管**——这才是 Mutating Webhook 该干的事

**webhook 的依赖**（部署时注意）：
- 需要 Harbor 凭证（读镜像 label 用，环境变量 `HARBOR_USERNAME`/`HARBOR_PASSWORD`，只读权限即可）
- 读不到 label 时安全兜底（返回空，不阻断 Pod 创建）

**面试话术**：
> 「我写了一个 Mutating Webhook，利用 K8s 准入控制机制，在 Pod 创建前自动做三件事：补资源限制、加 team 标签、识别 Python 应用自动加 OTel 注解。识别语言我不是猜镜像名，而是读镜像的 OCI metadata label——构建时 `LABEL language=python` 声明一次，webhook 调 Harbor API 读 label 自动识别，用户部署时无感知。这体现两点：一是理解 admission 机制本质是 HTTP 交互返回 JSON Patch；二是 webhook 的价值在于自动发现，而不是让用户手动打标签。」

### 4.10 Istio header 灰度发布 —— 生产发布策略

**是什么**：用 Istio 的 VirtualService 按 header 定向灰度。

**为什么用 header 定向（而不是随机分流）**：
- 随机分流（副本数比例）不精确，新版本有问题会影响一部分随机用户
- header 定向能精确让「测试组」先访问新版，出问题只影响测试组，风险可控

**两步流程（header 灰度 → 人工确认 → 全量切换）**：
1. header 灰度：带 `X-User-Group: beta` 的测试组 → v2 新版，其他 → v1 旧版
2. **人工确认**：测试组带 header 访问新版，完成功能验证；流水线里点「PASS 测试通过」→ 全量切换；点「FAIL 测试不通过」→ 自动回滚（删除 green，v1 继续服务）
3. 全量切换：100% 流量切到 v2，旧版保留作回滚点

**为什么必须有人工确认**（面试重点）：
> readiness 探针只能证明「进程起来了、依赖连上了」，不能证明「新版本业务逻辑是对的」。所以 header 灰度后必须由测试组人工验证新功能，验证通过才在流水线点确认切全量，不通过就回滚。这是生产级灰度发布的标准——自动化能做的（构建、部署、探针）全自动，需要人判断的（新功能好不好）由人确认。

**三个核心概念**：
| 概念 | 作用 | 类比 |
|------|------|------|
| Gateway | 集群入口 | 大门 |
| VirtualService | 路由规则（header → 版本） | 门卫看你是谁决定进哪个房间 |
| DestinationRule | 定义版本子集（v1/v2） | 房间登记表 |

**面试话术**：
> 「我用 Istio 做 header 灰度发布，分两步：先用 VirtualService 按 header 路由，带 X-User-Group: beta 的测试组用户先访问新版，其他人走旧版；测试组验证没问题后，切换 VirtualService 把 100% 流量切到新版，旧版保留作回滚点。这样蓝绿、金丝雀、header 灰度本质都是流量切换，Istio 一套 VirtualService 就能覆盖，不需要维护多套发布逻辑。」

**关键前提**：Istio 灰度要生效，namespace 必须开 `istio-injection: enabled`（让 Pod 注入 envoy sidecar），否则 VirtualService 规则不会执行。production/staging 的 namespace.yaml 里已经配了。

### 4.11 cert-manager 证书签发 —— 两层证书设计

**cert-manager 在本项目有两个用途，用不同的 Issuer 区分「内部 vs 公开」：**

| 用途 | 用的 Issuer | 证书类型 | 为什么 |
|------|------------|---------|--------|
| Mutating Webhook 证书 | `webhook-selfsigned`（自签名） | 内部自签名 | webhook 只在集群内（apiserver↔webhook），自签名 + 注入 CA 就够 |
| staging/prod 域名证书 | `letsencrypt-prod`（ClusterIssuer） | Let's Encrypt 正式 ACME | 公网服务要浏览器信任，必须公开 CA |

**关键认知 1：Let's Encrypt 免费，cert-manager 全自动**

- Let's Encrypt 是免费的非营利证书机构（不是公司，不收钱）
- cert-manager 用 ACME 协议全自动：申请 → 验证 → 签发 → 续期，零手工
- `letsencrypt-prod` 里的 prod 指「正式签发环境」（区别于测试用 staging 环境），不是付费

**关键认知 2：HTTP-01 挑战为什么不用 ingress-nginx**

集群没有 ingress-nginx（只有 Istio + Kong），但证书签发**不一定需要 nginx**：

```
cert-manager 支持 Gateway API solver（v1.21+）
    ↓
集群里 Istio 已注册为 Gateway API 实现者（gateway-controller，ACCEPTED=True）
    ↓
所以：有公网入口后，cert-manager 创建 HTTPRoute，Istio 网关响应 ACME 挑战
    ↓
不需要再装 ingress-nginx（避免 80/443 端口冲突 + 重复造轮子）
```

**关键认知 3：为什么现在签不出证书（内网集群限制）**

这是内网集群，`istio-ingressgateway` 是 LoadBalancer 类型但 `EXTERNAL-IP = <pending>`（没有公网 IP）。Let's Encrypt 从公网访问不到域名，所以无论用 nginx 还是 Istio 都签不出证书——**这是物理限制，不是配置问题**。

**面试话术**：
> 「证书签发我用 cert-manager，分两层：webhook 用自签名证书（集群内部通信，自签名 + 注入 CA 就够），staging/prod 域名用 Let's Encrypt 正式证书（公网服务要浏览器信任）。HTTP-01 挑战这块，我集群有 Istio 且已注册为 Gateway API 实现者，所以不需要装 ingress-nginx——cert-manager 走 Gateway API solver，Istio 网关响应挑战。这是内网集群没有公网入口，所以域名证书是『代码就绪、待公网 LB』状态，符合题目『网络假设已就绪』的设定。生产公网就绪后，整条链路全自动签发和续期。」

---

## 第 5 章：常见坑与排错

> 这些坑都是**真实踩过的**（部署 Jenkins 时全部遇到过），明天部署 dev 环境大概率也会遇到，提前知道怎么查。

### 5.1 镜像拉不下来（ImagePullBackOff）

**现象**：`kubectl get pods` 显示 `ImagePullBackOff` 或 `ErrImagePull`。

**原因**：内网 Harbor 拉镜像需要凭证，但 secret 没复制到对应 namespace。

```bash
# 看具体错误
kubectl describe pod <pod名> -n marriott-dev | grep -A5 Events
# 如果看到 "no basic auth credentials"，就是缺 swr-secret
```

**解决**：确认第 1 步「复制 swr-secret」做了，且 secret 名字是 `swr-secret`。

### 5.2 架构不匹配（exec format error）

**现象**：Pod 显示 `CrashLoopBackOff`，日志报 `exec format error`。

**原因**：本地 Mac 是 arm64，集群节点是 amd64。如果镜像推成 arm64，节点执行会报这个错。

**解决**：所有镜像已经用 `--platform linux/amd64` 重推了，tag 带 `-amd64` 后缀。如果还报错，确认镜像 tag 是 `-amd64` 结尾。

### 5.3 Pod 起不来（CrashLoopBackOff）

**现象**：backend Pod 反复重启。

```bash
kubectl logs <pod名> -n marriott-dev
# 看应用日志，常见原因：
#   - postgres 没起来（backend 连不上 DB）
#   - 数据库表没创建（之前漏挂 init.sql，已修复）
```

**排查顺序**：先看 postgres 和 redis 是否 Running，再看 backend 日志。

### 5.4 就绪探针一直失败（readyz 返回 503）

**现象**：Pod 是 Running，但 READY 列是 0/1。

**原因**：backend 的 `/readyz` 检查 DB + Redis 连通，如果这两个没起来，就绪探针一直失败。

```bash
kubectl describe pod <pod名> -n marriott-dev | grep -A5 "Readiness"
kubectl logs <pod名> -n marriott-dev   # 看是不是 database_unreachable 或 redis_unreachable
```

**解决**：等 postgres 和 redis 先 Running，backend 就绪探针自然通过。

### 5.5 删除环境重来

```bash
kubectl delete -k k8s/overlays/dev   # 删除 dev 环境所有资源
kubectl apply -k k8s/overlays/dev    # 重新部署
```

> 注意：`kubectl delete -k` 会删除 namespace 里的所有资源，但**不会删除 PVC**（postgres 的持久化数据还在）。如果数据库数据脏了，需要手动 `kubectl delete pvc -n marriott-dev postgres-data-postgres-0`。

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
