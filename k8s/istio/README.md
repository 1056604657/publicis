# Istio 灰度发布（Canary Deployment via Header）

> 用 Istio 的 VirtualService + DestinationRule 实现「按 header 定向灰度」。
> 这是生产级灰度发布的标准做法，比「副本数比例」精确得多。

---

## 一、为什么用 Istio 而不是副本数比例

| 方案 | 原理 | 精确度 | 问题 |
|------|------|:--:|------|
| 副本数比例 | 新版 1 副本 + 旧版 3 副本 ≈ 25% | 粗 | 不精确，无法定向 |
| **Istio header 灰度** | 按 header 精确路由 | 精确 | 能定向让「测试组」先访问新版 |

---

## 二、三个资源各司其职

```
外部流量
    ↓
Gateway（入口，替代 ingress-nginx）
    ↓
VirtualService（路由规则：看 header 决定去哪）
    ├─ 带 X-User-Group: beta → v2（新版）
    └─ 其他 → v1（旧版）
    ↓
DestinationRule（把 Service 拆成 v1/v2 两个版本子集）
    ├─ v1 = version: blue 标签的 Pod（旧版）
    └─ v2 = version: green 标签的 Pod（新版）
```

---

## 三、灰度发布完整流程（面试核心）

```
第 1 步：部署 v2 新版（少量副本，version=green 标签）
第 2 步：VirtualService 加 header 规则，让「测试组」访问 v2
        （X-User-Group: beta → v2，其他人还是 v1）
第 3 步：观察 v2 无异常（看日志、监控、错误率）
第 4 步：把 header 规则改成 weight 权重（90% v1 + 10% v2 逐步放量）
第 5 步：逐步放大 v2 权重（50/50 → 100% v2）
第 6 步：全部流量切到 v2，灰度完成
第 7 步：v1 保留一段时间作为回滚点，确认稳定后下线
```

---

## 四、关键概念解释（面试会问）

### 1. subset 是什么？

DestinationRule 里的 `subset` 就是「版本」。它通过 `labels` 把同一个 Service 下的 Pod 分成不同子集：

```yaml
subsets:
  - name: v1
    labels:
      version: blue    # 匹配带 version=blue 的 Pod
  - name: v2
    labels:
      version: green   # 匹配带 version=green 的 Pod
```

### 2. header 路由 vs 权重路由

| 方式 | 写法 | 适用 |
|------|------|------|
| header 路由 | `match: headers: X-User-Group: exact: beta` | 灰度初期，定向测试组 |
| 权重路由 | `weight: 90 / weight: 10` | 灰度中后期，逐步放量 |

### 3. 为什么需要 Gateway

集群之前没有 ingress-nginx，只有 Istio。Istio 的入口就是 Gateway + VirtualService 的组合，替代了原生 Ingress 的角色。

---

## 五、和 Jenkinsfile 里的蓝绿部署什么关系

| 部署方式 | 用在哪个环节 | 特点 |
|---------|-------------|------|
| **蓝绿**（Jenkinsfile `blueGreenDeploy()`） | CI/CD 流水线里 production 部署 | 瞬间切换 + 秒级回滚 |
| **Istio header 灰度**（本目录） | 更精细的渐进式发布 | 定向测试 + 逐步放量 |

两者可以配合：**蓝绿部署负责「准备好新版本 + 快速回滚」，Istio 灰度负责「控制流量怎么切过去」**。这是大厂常见的组合打法。

---

## 六、怎么用

```bash
# 1. 部署两个版本（blue 和 green 两个 Deployment，带 version 标签）
#    （这个由 Jenkinsfile 的蓝绿部署自动完成）

# 2. 应用 Istio 灰度配置
kubectl apply -f k8s/istio/destinationrule.yaml
kubectl apply -f k8s/istio/virtualservice.yaml
kubectl apply -f k8s/istio/gateway.yaml

# 3. 测试灰度（带 header 访问新版）
curl -H "X-User-Group: beta" http://<gateway>/api/items   # 走 v2 新版
curl http://<gateway>/api/items                            # 走 v1 旧版
```
