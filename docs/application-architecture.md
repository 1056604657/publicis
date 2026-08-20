# 应用架构设计

> 本文档描述三层 Hello-World 应用在 Kubernetes 上的完整部署架构：五套环境各部署了哪些 K8s 资源、流量如何流转、与代码的对应关系。
>
> 题目要求：Deployment / Service / Ingress / HPA / cert-manager + 多环境，已全部覆盖，详见各章节。

---

## 一、组件总览

本项目有三层应用加一个基础设施组件：

| 组件 | 类型 | 代码位置 | 用途 |
|------|------|---------|------|
| **frontend** | K8s Deployment + Service | `k8s/base/frontend-deployment.yaml` | Nginx 反向代理，提供静态页 + `/api` 路由到 backend |
| **backend** | K8s Deployment + Service | `k8s/base/backend-deployment.yaml` | Flask REST API，处理业务逻辑，连接 PG + Redis |
| **postgres** | K8s StatefulSet + Headless Service | `k8s/base/postgres-statefulset.yaml` | 持久化存储，建表脚本通过 ConfigMap 注入 |
| **redis** | K8s Deployment + Service | `k8s/base/redis-deployment.yaml` | 缓存层，key 为 `items:list`，TTL 60 秒 |
| **mutate-webhook** | K8s Deployment + Service + MutatingWebhookConfiguration | `k8s/webhook/webhook.yaml` | Pod 创建时注入治理策略（资源限制 / 标签 / OTel 注解） |
| **jenkins** | Helm Release | `k8s/jenkins/values.yaml` | CI/CD 流水线控制器，部署在 `jenkins` namespace |

---

## 二、每套环境部署的 K8s 资源清单

五套环境共用 `k8s/base/` 下的基础资源，通过 `k8s/overlays/<env>/` 做差异化覆盖。

### 2.1 dev 环境（marriott-dev）

**用途**：开发人员日常调试，快速迭代。

**部署命令**：`kubectl apply -k k8s/overlays/dev`

**部署到集群的资源**（共约 18 个对象）：

| 资源类型 | 资源名称 | 说明 |
|---------|---------|------|
| Namespace | `marriott-dev` | 隔离环境，标签 `environment: dev` |
| Deployment | `frontend` | 1 副本，无 HPA，无 PDB，无 PriorityClass |
| Service | `frontend` | ClusterIP，端口 80 → 8080 |
| Deployment | `backend` | 1 副本，无 HPA，无 PDB，无 PriorityClass，无 version 标签 |
| Service | `backend` | ClusterIP，端口 8080 |
| StatefulSet | `postgres` | 1 副本，挂载 PVC（nfs-storage，1Gi），init.sql ConfigMap 注入建表 |
| Service | `postgres` | Headless（clusterIP: None），端口 5432 |
| ConfigMap | `postgres-init` | init.sql 建 items 表，幂等插入两条示例数据 |
| Deployment | `redis` | 1 副本，端口 6379 |
| Service | `redis` | ClusterIP，端口 6379 |
| ConfigMap | `app-config` | DB_HOST=postgres、REDIS_HOST=redis 等非敏感配置 |
| Secret | `app-secrets` | Kustomize secretGenerator 生成，DB_USER=app、DB_PASSWORD=dev_password_123 |
| Ingress | `app-ingress` | 域名 `dev.app.example.com`，ingressClassName: nginx |
| ServiceAccount | `backend` | backend Pod 专用身份 |
| ServiceAccount | `frontend` | frontend Pod 专用身份 |
| Role + RoleBinding | `backend-readonly` | backend SA 只读自己 namespace 的 pod/service（演示最小权限） |
| NetworkPolicy | `postgres-allow-backend` | 只允许带 `app: backend` 标签的 Pod 访问 postgres 5432 |
| NetworkPolicy | `redis-allow-backend` | 只允许带 `app: backend` 标签的 Pod 访问 redis 6379 |
| NetworkPolicy | `backend-allow-frontend` | 只允许带 `app: frontend` 标签的 Pod 访问 backend 8080；ingress-nginx namespace 白名单 |

**dev 环境流量路径**：

```
外部用户
    │
    ▼
DNS: dev.app.example.com
    │
    ▼
K8s Ingress（资源存在但集群无 nginx controller，流量不从此进）
    │
    ▼
→ kubectl port-forward svc/frontend 80:80  // 开发调试用
    │
    ▼
frontend Service (ClusterIP:80 → Pod:8080)
    │
    ▼
Nginx (8080) 静态页 / → index.html
         /api/* → 反向代理 → backend Service:8080
         /healthz → 返回 200（存活检查）
    │
    ▼
backend Service (ClusterIP:8080 → Pod:8080)
    │
    ▼
Flask/gunicorn /api/items
    │
    ├─ Redis (items:list) 缓存命中 → 返回 source=cache
    │
    ├─ Redis miss → 查 PostgreSQL → 写回 Redis → 返回 source=database
    │
    └─ Redis 挂了 → 直连 PostgreSQL（降级，不阻断）
```

**dev 环境特殊性**：
- 无 HPA（单副本，负载低且可预期，不需要弹性）
- 无 PDB（单副本，节点维护时 Pod 会被驱逐，无法保证可用）
- PriorityClass 不存在（`$patch: delete`），deployment 里也去掉了 `priorityClassName` 引用（避免 no PriorityClass found 报错）
- Secret 明文生成（开发密码不敏感，简单直接）
- 镜像 tag = `latest-amd64`（快速迭代，不用每次 commit 改 tag）

---

### 2.2 test 环境（marriott-test）

**用途**：QA 内部测试，验证功能正确性。

**部署命令**：`kubectl apply -k k8s/overlays/test`

**与 dev 环境的区别**：

| 差异项 | dev | test |
|--------|-----|------|
| Namespace | `marriott-dev` | `marriott-test` |
| Ingress 域名 | `dev.app.example.com` | `test.app.example.com` |
| Secret 密码 | `dev_password_123` | `test_password_123` |
| 副本数 | 1 | 1 |
| HPA / PDB / PriorityClass | 无 | 无 |
| 集群内 DB/Redis | 有 | 有 |
| 其他资源 | 同 dev | 同 dev |

**test 环境流量路径**：与 dev 完全相同，只是 namespace 隔离、域名不同。

---

### 2.3 perf 环境（marriott-perf）

**用途**：性能压测，模拟真实生产高负载，验证 HPA 弹性扩缩容。

**部署命令**：`kubectl apply -k k8s/overlays/perf`

**部署到集群的资源**（在 dev 的基础上增加了以下，删除了某些）：

| 资源类型 | 资源名称 | 说明 |
|---------|---------|------|
| Namespace | `marriott-perf` | 标签 `environment: perf` |
| Deployment | `frontend` | **2 副本**，有 HPA、有 PDB，有 PriorityClass |
| Deployment | `backend` | **3 副本**，有 HPA（2-10 副本，CPU 70% / Memory 80% 触发），有 PDB，有 PriorityClass |
| HorizontalPodAutoscaler | `backend-hpa` | minReplicas:2 / maxReplicas:10，CPU 70% / Memory 80%，含 scaleUp/scaleDown 防抖策略 |
| PodDisruptionBudget | `backend-pdb` | minAvailable: 1，保证节点维护时至少 1 个 backend pod 可用 |
| PodDisruptionBudget | `frontend-pdb` | minAvailable: 1，保证节点维护时至少 1 个 frontend pod 可用 |
| PriorityClass | `app-priority` | value: 100000，高优先级，资源紧张时优先调度 |
| Ingress | `app-ingress` | 域名 `perf.app.example.com`，无 cert-manager annotation（内网集群无公网入口，证书代码就绪） |
| Secret | `app-secrets` | `perf_password_123` |
| NetworkPolicy | 同 dev | 同 dev |
| 镜像 tag | `v1-amd64` | perf 用固定版本 tag（与 staging/prod 一致） |

**perf 环境特殊性**：
- **HPA 弹性扩缩容**：压测时模拟高并发，CPU/内存超过阈值自动扩容至 10 副本，压测结束自动缩回
- **HPA 防抖策略**：`scaleUp` 立即响应（stabilizationWindowSeconds: 0），`scaleDown` 冷却 5 分钟（stabilizationWindowSeconds: 300，防止短抖动反复扩缩）
- **Pod 跨节点分布**：backend/frontend 的 `topologySpreadConstraints`（maxSkew:1, hostname），单节点故障不会全挂
- **PDB**：配合 HPA 使用——HPA 缩容时 PDB 保证至少 minAvailable 个 pod 可用，节点维护时 K8s 不会同时驱逐超过 PDB 限制的 pod
- **PriorityClass**：backend/frontend pod 用 `app-priority`（100000），保证核心服务优先调度，不被低优先级任务抢占
- 集群内 postgres/redis 保留（压测需要真实存储，模拟生产存储层）

**perf 环境流量路径**（与 dev 相同，副本数更多，HPA 自动调整）：

```
外部压测流量
    │
    ▼
DNS: perf.app.example.com
    │
    ▼
Ingress → frontend Service（2 副本，HPA 扩至更多）
    │
    ▼
frontend（2+ 副本）→ backend Service（3+ 副本）
    │
    ▼
backend（3+ 副本，HPA 按 CPU/Memory 自动扩缩）
    │
    ├─ Redis（1 副本）
    └─ PostgreSQL（1 副本，PVC 持久化）
```

---

### 2.4 staging 环境（marriott-staging）

**用途**：UAT / 集成测试 / 预发布验证，模拟生产真实云上基础设施。

**部署命令**：`kubectl apply -k k8s/overlays/staging`

**关键差异：数据层指向云上**

staging 的 backend/frontend 连接**阿里云 RDS + Redis**，而不是集群内的 postgres/redis：

| 资源差异 | dev / test / perf | staging |
|---------|-------------------|---------|
| DB | 集群内 postgres StatefulSet | **阿里云 RDS**（地址由 terraform apply 注入到 `__RDS_ENDPOINT__`） |
| Redis | 集群内 redis Deployment | **阿里云 Redis**（地址由 terraform apply 注入到 `__REDIS_ENDPOINT__`） |
| 集群内 postgres StatefulSet | 部署 | **删除**（$patch: delete） |
| 集群内 redis Deployment | 部署 | **删除**（$patch: delete） |
| 集群内 postgres/redis NetworkPolicy | 生效 | **删除**（死策略，云上数据库用 RDS 白名单保护） |
| Secret 来源 | secretGenerator 明文 | **ExternalSecret + ESO**（从阿里云 KMS 拉取，零明文） |
| cert-manager | selfsigned（自签名） | **letsencrypt-prod**（正式 ACME 证书，HTTP-01 挑战） |
| Namespace 标签 | 无 | `istio-injection: enabled`（开启 Istio sidecar 自动注入） |
| 副本数 | 1-3 | 2（backend + frontend） |
| HPA | 无（dev/test）或有（perf） | **有**（2-10 副本） |
| Istio 灰度 | 无 | 无（staging 不做灰度，production 才灰度） |

**部署到集群的资源**：

| 资源类型 | 资源名称 | 说明 |
|---------|---------|------|
| Namespace | `marriott-staging` | 标签 `environment: staging`，`istio-injection: enabled` |
| Deployment | `frontend` | 2 副本，有 HPA、有 PDB，有 PriorityClass |
| Deployment | `backend` | 2 副本，有 HPA、有 PDB，有 PriorityClass |
| HPA / PDB / PriorityClass | 同 perf | 同 perf |
| Ingress | `app-ingress` | 域名 `staging.app.example.com`，cert-manager annotation → `letsencrypt-prod` |
| SecretStore | `aliyun-kms` | ESO 访问阿里云 KMS 的凭证定义 |
| ExternalSecret | `app-secrets` | 从 KMS 同步 DB_USER / DB_PASSWORD，不在代码中明文存放 |
| ConfigMap | `app-config` | DB_HOST=__RDS_ENDPOINT__（terraform apply 注入），REDIS_HOST=__REDIS_ENDPOINT__ |
| Istio Gateway | `marriott-gateway` | `k8s/istio/gateway.yaml` → istio-system（staging/prod 共用） |
| Istio VirtualService | 无 | 灰度只在 production，staging 不部署 VirtualService（`k8s/istio/virtualservice.yaml` 的 namespace 是 marriott-production） |

**staging 环境特殊性**：
- External Secrets Operator（ESO）从阿里云 KMS 拉取密钥，代码零明文密码——这是生产级密钥管理实践
- cert-manager 用 `letsencrypt-prod` 签发正式域名证书（HTTP-01 ACME 挑战，自动化签发和续期）
- namespace 开启 Istio sidecar 注入：`istio-injection: enabled`——所有 pod 自动带 envoy sidecar，接受 VirtualService 流量规则控制
- Istio Gateway/VirtualService 已部署（文件在 `k8s/istio/`），但 staging 流水线不执行灰度发布（只做全量部署）
- `__RDS_ENDPOINT__` / `__REDIS_ENDPOINT__` 占位符：由 terraform apply 阶段替换为真实阿里云内网地址

**staging 环境流量路径**：

```
外部用户（UAT 测试人员）
    │
    ▼
DNS: staging.app.example.com
    │
    ▼
阿里云 SLB / CDN（DNS 层面，题外）
    │
    ▼
Ingress（cert-manager 自动签 TLS 证书）
    │
    ▼
Istio Gateway（istio-system）
    │
    ▼
frontend Service（2 副本，envoy sidecar 注入）
    │
    ▼
Nginx → backend Service（2 副本，envoy sidecar 注入）
    │
    ▼
Flask → 阿里云 RDS（__RDS_ENDPOINT__）
        → 阿里云 Redis（__REDIS_ENDPOINT__）
```

---

### 2.5 production 环境（marriott-production）

**用途**：生产环境，最严格的安全和发布流程。

**部署命令**：`kubectl apply -k k8s/overlays/production`

**与 staging 的区别**：

| 差异项 | staging | production |
|--------|---------|------------|
| Namespace | `marriott-staging` | `marriott-production` |
| Ingress 域名 | `staging.app.example.com` | `app.marriott.com` |
| 副本数 | 2（backend + frontend） | **0**（笔试场景云资源未就绪，云上 RDS/ESO 就绪后改为 3） |
| HPA | 有（2-10） | **删除**（0 副本无意义，云资源就绪后恢复） |
| Secret | ESO + KMS | ESO + KMS（同 staging） |
| 镜像 tag | `v1-amd64` | `v1-amd64`（overlay 基线；CI 部署时用 commit SHA tag 写回） |
| 灰度发布 | 无（全量） | **Istio header 灰度**（X-User-Group: beta 走 v2，其他走 v1） |
| OTEL | 无（ConfigMap 未配置） | **有**（OTEL_EXPORTER_OTLP_ENDPOINT: http://otel-collector:4317） |

**部署到集群的资源**：

| 资源类型 | 资源名称 | 说明 |
|---------|---------|------|
| Namespace | `marriott-production` | 标签 `environment: production`，`istio-injection: enabled` |
| Deployment | `frontend` | **0 副本**（云资源就绪后改为 3），有 PDB，有 PriorityClass |
| Deployment | `backend` | **0 副本**（云资源就绪后改为 3），有 PDB，有 PriorityClass |
| PodDisruptionBudget | `backend-pdb` / `frontend-pdb` | minAvailable: 1（云资源就绪后生效） |
| Ingress | `app-ingress` | 域名 `app.marriott.com`，cert-manager → `letsencrypt-prod` |
| SecretStore + ExternalSecret | 同 staging | 同 staging |
| ConfigMap | `app-config` | DB_HOST=__RDS_ENDPOINT__，REDIS_HOST=__REDIS_ENDPOINT__，OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317 |
| Istio Gateway | `marriott-gateway` | → istio-system |
| Istio VirtualService | `backend-route` | → marriott-production，**header 灰度规则** |
| Istio DestinationRule | `backend-destination` | 定义 v1（version: blue）和 v2（version: green）两个 subset |
| 其他 RBAC / NetworkPolicy / ServiceAccount | 同 staging | 同 staging |

**production 环境特殊性**：
- 0 副本是**诚实设计**：生产依赖云上 RDS/ESO，笔试场景未就绪，Pod 起来连不上云库会 CrashLoop。所有 K8s 资源（PDB/Service/Ingress/Istio/RBAC）完整创建，只是 Deployment 副本数 = 0。云资源到位后把 `replicas: 0` 改为 `replicas: 3`，整站立刻上线。
- **Istio header 灰度发布**：这是 production 独有的发布策略（详见第三章）
- **OTel 可观测性**：production 连接 OTel Collector，Flask 请求自动埋点（Trace/Metric），可观测性数据汇聚到 OTel 后端
- 生产镜像 tag 用 `v1-amd64`（基于 commit SHA 的不可变 tag），不用 `latest`

---

## 三、生产环境 Istio 灰度发布（production 独有）

### 3.1 灰度发布三件套

| 资源 | 代码位置 | 作用 |
|------|---------|------|
| Gateway | `k8s/istio/gateway.yaml` | 入口网关，绑定 istio-system 的 ingressgateway（端口 80），定义允许的 host |
| DestinationRule | `k8s/istio/destinationrule.yaml` | 定义 subset：v1 = version:blue（旧版），v2 = version:green（新版） |
| VirtualService | `k8s/istio/virtualservice.yaml` | 按 header 路由：X-User-Group:beta → v2，其他 → v1 |
| VirtualService（全量版） | `k8s/istio/virtualservice-full.yaml` | 灰度完成后，100% 流量切 v2 |

### 3.2 灰度发布完整流程

**前提**：namespace `marriott-production` 有 `istio-injection: enabled`，所有 backend/frontend pod 自动带 envoy sidecar，VirtualService 规则才生效。

**第 1 步：部署 green 新版（version=green 标签）**

Jenkins 流水线 `progressiveDeploy` 函数执行：

```bash
kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: backend-green    # 新版 deployment，名字与 base 的 backend（blue）区分
  namespace: marriott-production
spec:
  replicas: 1
  selector:
    matchLabels:
      app: backend
      version: green     # ← 关键：version 标签让 Istio subset 能匹配到这个 pod
  template:
    metadata:
      labels:
        app: backend
        version: green   # ← Istio DestinationRule 用这个标签定义 v2 subset
EOF
```

此时集群里有：
- `backend`（blue，version=blue）：旧版 pod，副本数 0（production 默认 0 副本）
- `backend-green`（green，version=green）：新版 pod，副本数 1

**第 2 步：测试组带 header 验证新版**

测试人员在请求里加 header 访问：

```bash
curl -H "X-User-Group: beta" http://<gateway>/api/items  # 走 backend-green（v2）
curl http://<gateway>/api/items                            # 走 backend（v1，production 默认 0 副本所以此请求失败）
```

VirtualService 规则：带 `X-User-Group: beta` 的请求命中 `X-User-Group exact: beta` 规则，路由到 subset v2（version=green）；其他请求走 v1（version=blue）。

**第 3 步：测试通过，人工确认全量切换**

Jenkins 流水线弹出人工审批：
- 选择 **PASS（测试通过，全量切换）**：`kubectl apply -f k8s/istio/virtualservice-full.yaml`，100% 流量切到 v2
- 选择 **FAIL（测试不通过，回滚）**：`kubectl delete deployment backend-green`，v1 继续服务，无损失

**第 4 步：角色轮换（为下次灰度做准备）**

blue（backend）是常驻稳定版，green 是每次灰度的临时新版。灰度完成后角色轮换：

1. 更新 base 的 `backend`（version=blue）为新镜像——blue 变成新稳定版
2. 恢复 header 路由（`kubectl apply -f k8s/istio/virtualservice.yaml`），流量默认走 blue（此时 blue 已是新版本）
3. 删除临时的 `backend-green`

这样 blue 永远是稳定版，green 永远是待验证新版，每次发布循环复用同一套角色，不会同名冲突。

### 3.3 灰度发布的流量模型

```
外部用户（不带 header）
    │
    ▼
Istio Gateway (istio-system:80)
    │
    ▼
VirtualService: default 规则
    │
    ▼
DestinationRule subset v1 → backend pods (version=blue)
    │
    ▼
旧版服务（production 默认 0 副本 → 除非 blue 已更新到新版本）

=========================================

测试组用户（带 X-User-Group: beta）
    │
    ▼
Istio Gateway (istio-system:80)
    │
    ▼
VirtualService: header 匹配规则
    │
    ▼
DestinationRule subset v2 → backend-green pods (version=green)
    │
    ▼
新版服务（v2 新功能验证）

=========================================

全量切换后（virtualservice-full.yaml）
    │
    ▼
100% 流量 → v2（backend-green 已合并进 blue，或 blue 已更新为新版镜像）
```

---

## 四、环境横向对比

| 项目 | dev | test | perf | staging | production |
|------|-----|------|------|---------|------------|
| **Namespace** | marriott-dev | marriott-test | marriott-perf | marriott-staging | marriott-production |
| **副本数** | 1 | 1 | 3 | 2 | 0* |
| **HPA** | 无 | 无 | 有（2-10） | 有（2-10） | 无* |
| **PDB** | 无 | 无 | 有 | 有 | 有* |
| **PriorityClass** | 无 | 无 | 有 | 有 | 有 |
| **数据库** | 集群内 PG | 集群内 PG | 集群内 PG | 阿里云 RDS | 阿里云 RDS |
| **Redis** | 集群内 Redis | 集群内 Redis | 集群内 Redis | 阿里云 Redis | 阿里云 Redis |
| **Secret 来源** | secretGenerator | secretGenerator | secretGenerator | ESO + KMS | ESO + KMS |
| **证书** | selfsigned | selfsigned | selfsigned | letsencrypt-prod | letsencrypt-prod |
| **Istio sidecar** | 无 | 无 | 无 | 有 | 有 |
| **灰度发布** | 无 | 无 | 无 | 无 | Istio header 灰度 |
| **镜像 tag** | latest-amd64 | latest-amd64 | v1-amd64 | v1-amd64 | v1-amd64 |
| **NetworkPolicy** | 有 | 有 | 有 | 无（删了） | 无（删了） |
| **拓扑分布** | 无 | 无 | topologySpread | topologySpread | topologySpread |
| **OTel** | 无 | 无 | 无 | 无 | 有 |

> \* production 的 0 副本/HPA/PDB：云上 RDS/ESO 就绪后改为 replicas:3，HPA 和 PDB 随之生效。

---

## 五、平台治理（所有环境通用）

以下 K8s 资源在 dev/test 环境通过 overlay patch 移除，在 perf/staging/production 环境保留：

### 5.1 HPA（HorizontalPodAutoscaler）

**资源**：`k8s/base/hpa.yaml`

backend 的水平自动扩缩容，perf/staging 环境生效：
- 扩缩容指标：CPU 利用率 70%、Memory 利用率 80%
- 扩缩容范围：minReplicas 2 → maxReplicas 10
- **防抖策略**：
  - 扩容立即响应（stabilizationWindowSeconds: 0），避免流量高峰来不及扩容
  - 缩容冷却 5 分钟（stabilizationWindowSeconds: 300），避免短时流量抖动反复扩缩
  - 扩容上限：30 秒内最多翻倍（100%）或 +4 pod，取较大值

### 5.2 PDB（PodDisruptionBudget）

**资源**：`k8s/base/pdb.yaml`

backend 和 frontend 各一个 PDB，节点维护/驱逐时保证服务不中断：
- `backend-pdb`：minAvailable: 1（3 副本时最多驱逐 2 个）
- `frontend-pdb`：minAvailable: 1

### 5.3 PriorityClass

**资源**：`k8s/base/priorityclass.yaml`

- `app-priority`：value 100000，高优先级
- backend 和 frontend 的 Pod 使用此 PriorityClass
- 节点资源紧张时，高优先级 Pod 优先调度，最后被驱逐

### 5.4 NetworkPolicy（网络隔离）

**资源**：`k8s/base/networkpolicy.yaml`

三条白名单策略（默认拒绝所有，只放行明确声明的流量）：

1. **postgres-allow-backend**：只允许带 `app: backend` 标签的 Pod 访问 postgres 5432
2. **redis-allow-backend**：只允许带 `app: backend` 标签的 Pod 访问 redis 6379
3. **backend-allow-frontend**：只允许带 `app: frontend` 标签的 Pod 访问 backend 8080；ingress-nginx namespace 白名单

staging/production 删除了前两条（数据库在云上，用 RDS 白名单保护）。

### 5.5 ServiceAccount + RBAC

**资源**：`k8s/base/serviceaccount.yaml`

- backend 和 frontend 各有独立的 ServiceAccount（不用 default，最小权限）
- backend SA 有个只读 Role（读自己 namespace 的 pod/service），演示最小权限实践

### 5.6 Mutating Webhook（平台治理自动化）

**资源**：`k8s/webhook/webhook.yaml`

Pod 创建前由 apiserver 调用 webhook（端口 8443，HTTPS），webhook 返回 JSON Patch 做三件事：

1. **资源治理**：容器没有写 `resources.limits` 时补默认值（CPU 500m、Memory 512Mi）
2. **标签规范**：给带 `app` 标签的 Pod 补 `team: platform` 标签
3. **OTel 可观测性联动**：调用 Harbor API 读镜像 OCI metadata label（`language=python`），自动识别 Python 应用并加 `instrumentation.opentelemetry.io/inject-python: "true"` 注解，OTel Operator 自动注入 agent

约束：
- namespaceSelector 只处理 `marriott-production / marriott-staging / marriott-perf`
- failurePolicy: Ignore（webhook 挂了不阻塞 Pod 创建）
- 证书：cert-manager 自签名（`webhook-selfsigned` Issuer）

### 5.7 Pod topologySpreadConstraints（跨节点分布）

backend 和 frontend 的 Pod template 包含 `topologySpreadConstraints`：
- maxSkew: 1：节点间 pod 数量差不超过 1
- topologyKey: kubernetes.io/hostname：以节点维度均匀分布
- whenUnsatisfiable: ScheduleAnyway：不满足时仍然调度（避免 2 节点集群卡住）

单节点故障时，pod 自动分布到其他节点，不会全挂。

---

## 六、流量总览（从用户请求到数据层）

### 通用流量路径（所有环境相同部分）

```
用户浏览器
    │
    ▼ HTTPS（TLS 终止在 Ingress 或 Istio Gateway）
DNS 解析 → SLB / Ingress / Istio Gateway
    │
    ▼ HTTP
frontend Service (ClusterIP)
    │
    ▼
Nginx Pod（监听 8080，nginx-unprivileged 非 root 运行）
    │
    ├─ /             → 静态文件（index.html）
    ├─ /healthz      → 返回 200（存活探针路径）
    └─ /api/*        → 反向代理到 backend Service:8080
    │
    ▼
backend Service (ClusterIP)
    │
    ▼
Flask/gunicorn Pod（监听 8080）
    │
    ├─ GET  /api/items
    │       ├─ Redis GET items:list
    │       │       ├─ 命中 → 返回 JSON（source=cache）
    │       │       └─ miss → PostgreSQL SELECT → Redis SET items:list <data> EX 60 → 返回 JSON（source=database）
    │       │
    │       └─ Redis 挂了 → 直连 PostgreSQL（降级，不阻断请求）
    │
    ├─ POST /api/items  → 写入 PostgreSQL → DELETE items:list（清缓存，保证下次读新数据）
    ├─ GET  /healthz    → 返回 {"status":"ok"}（存活探针）
    └─ GET  /readyz     → pg_isready + redis-cli ping → 全通返回 {"status":"ready"}，否则 503（就绪探针）
    │
    ▼
数据层（dev/test/perf → 集群内 | staging/production → 阿里云）
    ├─ PostgreSQL 5432（StatefulSet Headless，或阿里云 RDS）
    └─ Redis 6379（Deployment，或阿里云 Redis）
```

### 各环境流量终止点差异

| 环境 | 外部流量入口 | TLS 终止位置 | 数据层位置 |
|------|------------|------------|----------|
| dev | port-forward（调试用） | 无 | 集群内 |
| test | port-forward（调试用） | 无 | 集群内 |
| perf | DNS + Ingress | Ingress（selfsigned） | 集群内 |
| staging | DNS + Ingress | Ingress（letsencrypt-prod） | 阿里云 RDS/Redis |
| production | DNS + Istio Gateway | Istio Gateway（letsencrypt-prod） | 阿里云 RDS/Redis |

> 说明：集群没有 ingress-nginx controller（只有 Istio + Kong），dev/test 环境用 port-forward 调试，perf/staging 用 Ingress 资源（代码就绪），production 用 Istio Gateway 作为实际入口。

---

## 七、与代码的对应关系

每一条 K8s 资源都能在代码目录找到对应文件：

| K8s 资源 | 代码文件 | 说明 |
|---------|---------|------|
| 所有基础 Deployment/Service/HPA/PDB/... | `k8s/base/` | 所有环境共享 |
| dev 环境差异化 | `k8s/overlays/dev/` | 镜像 tag、域名、secret、删除 HPA/PDB/PC |
| test 环境差异化 | `k8s/overlays/test/` | 同上 |
| perf 环境差异化 | `k8s/overlays/perf/` | 镜像 tag、域名、3 副本、保留 HPA/PDB/PC |
| staging 环境差异化 | `k8s/overlays/staging/` | RDS 地址、ESO Secret、cert annotation、删除集群内 DB/Redis |
| production 环境差异化 | `k8s/overlays/production/` | 同 staging + 生产域名 + OTEL + 0 副本 |
| Istio 灰度资源 | `k8s/istio/` | gateway.yaml / virtualservice.yaml / destinationrule.yaml |
| cert-manager | `k8s/cert-manager/` | selfsigned.yaml / letsencrypt-prod.yaml |
| Mutating Webhook | `k8s/webhook/webhook.yaml` + `src/webhook/` | 部署资源 + webhook 服务代码 |
| Jenkins | `k8s/jenkins/values.yaml` | Helm values 配置 |
| 后端应用代码 | `src/backend/main.py` | Flask 应用，健康检查、缓存逻辑、OTel 埋点 |
| 前端 Nginx 配置 | `src/frontend/nginx.conf` | 反向代理规则 |
| 数据库初始化 | `src/db/init.sql` → `k8s/base/postgres-init-configmap.yaml` | 建表 + 幂等示例数据 |
