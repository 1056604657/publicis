# Istio 灰度发布（header 定向）

> 用 Istio 的 VirtualService + DestinationRule 实现「header 定向灰度」。
> 核心思路：先按 header 让「测试组」访问新版，测试通过后全量切换。

---

## 一、灰度发布流程（两步）

```
第 1 步：header 灰度（定向测试）
  部署 green 新版 → Istio 配置 header 路由
  → 带 X-User-Group: beta 的测试组用户访问 v2（新版）
  → 其他用户还是 v1（旧版）

第 2 步：人工确认（关键！）
  测试组带 header 访问新版，完成功能验证
  → 验证通过：流水线点「PASS」→ 全量切换
  → 验证不通过：流水线点「FAIL」→ 自动回滚（删除 green，v1 继续服务）

第 3 步：全量切换
  → 100% 流量切到 v2（新版）
  → 旧版 v1 保留作回滚点，稳定后下线

第 4 步：角色轮换（为下次灰度做准备）
  → 把 base 的 backend（blue 稳定版）更新成新镜像
  → 恢复 header 路由（流量默认走 blue，此时 blue 已是新版本）
  → 删除临时 green
  → 下次灰度：blue=当前稳定版，新部署的 green=新版
```

> **为什么必须人工确认**：readiness 探针只能证明进程和依赖正常，不能证明新版本功能正确。所以灰度后由测试组人工验证，通过才切全量，不通过回滚。自动化做能自动化的（构建/部署/探针），需要人判断的（功能好坏）由人确认。

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
    ├─ v1 = version: blue 标签（旧版）
    └─ v2 = version: green 标签（新版）
```

---

## 三、两个流量阶段（对应两个 VirtualService 文件）

| 阶段 | 文件 | 规则 |
|------|------|------|
| header 灰度 | `virtualservice.yaml` | 带 beta header → v2，其他 → v1 |
| 全量切换 | `virtualservice-full.yaml` | 100% → v2 |

---

## 四、核心概念（面试会问）

### 1. subset 是什么？

DestinationRule 里的 `subset` 就是「版本」，用 labels 把同一个 Service 下的 Pod 分成子集：

```yaml
subsets:
  - name: v1
    labels:
      version: blue    # 旧版
  - name: v2
    labels:
      version: green   # 新版
```

### 2. header 路由怎么写

```yaml
http:
  # 规则1：带 beta header → 新版 v2
  - match:
      - headers:
          X-User-Group:
            exact: beta
    route:
      - destination:
          subset: v2
  # 规则2：默认 → 旧版 v1
  - route:
      - destination:
          subset: v1
```

### 3. 回滚机制

新版出问题，回滚就是「改回 VirtualService」：

```bash
# 回滚到旧版（header 规则重新生效，或直接 100% 指回 v1）
kubectl apply -f k8s/istio/virtualservice.yaml
```

秒级生效，不用重启 Pod。

**面试一句话**：

> 「我用 Istio 做 header 灰度。DestinationRule 把服务拆成 v1/v2 两个子集，VirtualService 按 header 路由——带 `X-User-Group: beta` 的测试组用户先访问新版，其他人走旧版。测试组验证新版没问题后，切换 VirtualService 把 100% 流量切到新版，旧版保留作回滚点。」

---

## 五、配置文件清单

```
k8s/istio/
├── gateway.yaml                 # 入口网关（替代 ingress-nginx）
├── destinationrule.yaml         # 定义 v1/v2 版本子集
├── virtualservice.yaml          # header 灰度（测试组 → v2）
├── virtualservice-full.yaml     # 全量切换（100% → v2）
└── README.md                    # 本文件
```

## 六、怎么用

```bash
# 1. 部署 blue/green 两个版本（Jenkinsfile 的 progressiveDeploy 自动完成）

# 2. 应用 Istio 配置（header 灰度）
kubectl apply -f k8s/istio/destinationrule.yaml
kubectl apply -f k8s/istio/virtualservice.yaml

# 3. 测试灰度（带 header 访问新版）
curl -H "X-User-Group: beta" http://<gateway>/api/items   # 走 v2 新版
curl http://<gateway>/api/items                            # 走 v1 旧版

# 4. 测试通过后，全量切换
kubectl apply -f k8s/istio/virtualservice-full.yaml
```
