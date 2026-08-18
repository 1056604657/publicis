"""
Mutating Admission Webhook

作用：Pod 创建前自动做一些标准化处理，演示「K8s admission 内部机制」能力。

Webhook 原理：
  1. K8s apiserver 创建 Pod 前，把 Pod 的 AdmissionReview 请求 POST 给这个 webhook
  2. webhook 检查 Pod 是否满足条件，返回一个 JSON Patch
  3. apiserver 应用这个 patch 后再真正创建 Pod

本 webhook 做的三件事：
  ① 资源治理：给没写 resources.limits 的容器自动注入默认资源限制
  ② 标签规范：给业务 Pod 自动注入 team: platform 标签（便于成本分摊/权限管理）
  ③ 可观测性联动：读镜像 OCI metadata label（language=python）自动识别 Python 应用，
     自动加 OTel 注入 annotation，交给 OpenTelemetry Operator 去注入 agent
     （不重复造轮子，只做「自动发现」；不猜镜像名，读镜像真实 metadata）

技术：Python + Flask（处理 AdmissionReview JSON，返回 JSON Patch）
证书：配合 cert-manager 签发（Task 2 加分项联动）
"""

import base64
import json
import os

import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

# 默认注入的资源配置（只在 Pod 没写 limits 时注入）
DEFAULT_CPU_LIMIT = os.getenv("DEFAULT_CPU_LIMIT", "500m")
DEFAULT_MEM_LIMIT = os.getenv("DEFAULT_MEM_LIMIT", "512Mi")

# 自动注入的团队标签（用于成本分摊、权限管理、资源归属）
TEAM_LABEL_KEY = "team"
TEAM_LABEL_VALUE = "platform"

# OTel 自动注入的 annotation key
# 注意：这个 key 含斜杠，JSON Pointer 里要用 ~1 转义（/ → ~1）
OTEL_INJECT_PYTHON_ANNOTATION = "instrumentation.opentelemetry.io/inject-python"

# Harbor 镜像仓库地址（用于读镜像 metadata label）
HARBOR_REGISTRY = os.getenv("HARBOR_REGISTRY", "hub-sh.aijidou.com")
# Harbor 只读凭证（webhook 读镜像 label 用，只读权限即可）
HARBOR_USERNAME = os.getenv("HARBOR_USERNAME", "")
HARBOR_PASSWORD = os.getenv("HARBOR_PASSWORD", "")


# ============================================================
# 读镜像 OCI metadata 里的 label（不猜镜像名）
# ============================================================
def get_image_labels(image_ref):
    """从 Harbor 读镜像的 OCI config label。
    image_ref 格式：hub-sh.aijidou.com/base/marriott-backend:v1
    流程：
      1. GET /v2/<repo>/manifests/<tag> 拿 manifest
      2. 从 manifest.config.digest 拿 config 的 digest
      3. GET /v2/<repo>/blobs/<digest> 拉 config
      4. 读 config.config.Labels
    """
    try:
        # 解析 image_ref：registry/repo:tag
        # 注意 repo 可能含多级路径（如 base/marriott-backend）
        parts = image_ref.split("/", 1)
        registry = parts[0]
        repo_and_tag = parts[1]
        if ":" in repo_and_tag:
            repo, tag = repo_and_tag.rsplit(":", 1)
        else:
            repo, tag = repo_and_tag, "latest"

        scheme = "https"
        base_url = f"{scheme}://{registry}"

        # 1. 拿 manifest
        manifest_url = f"{base_url}/v2/{repo}/manifests/{tag}"
        headers = {
            "Accept": "application/vnd.docker.distribution.manifest.v2+json"
        }
        resp = requests.get(manifest_url, headers=headers, timeout=5)
        if resp.status_code != 200:
            return {}
        manifest = resp.json()

        # 2. 拿 config digest
        config_digest = manifest.get("config", {}).get("digest", "")
        if not config_digest:
            return {}

        # 3. 拉 config blob，读 labels
        blob_url = f"{base_url}/v2/{repo}/blobs/{config_digest}"
        resp = requests.get(blob_url, timeout=5)
        if resp.status_code != 200:
            return {}
        config = resp.json()
        return config.get("config", {}).get("Labels", {})

    except Exception:
        # 读不到 label 时返回空（不阻断 Pod 创建，安全兜底）
        return {}


# ============================================================
# ① 资源治理：补默认资源限制
# ============================================================
def patch_resource_limits(containers):
    """给缺 resources.limits 的容器补上默认值"""
    patch = []
    for idx, container in enumerate(containers):
        # 如果容器没写 resources.limits，就注入默认值
        if "resources" not in container or "limits" not in container.get("resources", {}):
            patch.append({
                "op": "add",
                "path": f"/spec/containers/{idx}/resources",
                "value": {
                    "limits": {
                        "cpu": DEFAULT_CPU_LIMIT,
                        "memory": DEFAULT_MEM_LIMIT,
                    }
                },
            })
    return patch


# ============================================================
# ② 标签规范：注入 team 标签
# ============================================================
def patch_team_label(pod):
    """给 Pod 加 team 标签（如果还没有）"""
    labels = pod.get("metadata", {}).get("labels", {})
    if TEAM_LABEL_KEY in labels:
        return []  # 已有，跳过

    if labels:
        # labels 对象已存在，直接加 key
        return [{
            "op": "add",
            "path": f"/metadata/labels/{TEAM_LABEL_KEY}",
            "value": TEAM_LABEL_VALUE,
        }]
    else:
        # labels 对象不存在，先创建整个 labels 对象
        return [{
            "op": "add",
            "path": "/metadata/labels",
            "value": {TEAM_LABEL_KEY: TEAM_LABEL_VALUE},
        }]


# ============================================================
# ③ 可观测性联动：读镜像 metadata label，识别 Python 应用
# ============================================================
def is_python_app(container, labels):
    """判断容器是不是 Python 应用：
    读镜像的 OCI metadata label（language=python），不猜镜像名。
    这样构建镜像时声明一次语言，部署时 webhook 自动识别，用户无需手动打标签。
    """
    image = container.get("image", "")
    if not image:
        return False

    # 读镜像 metadata 里的 label
    image_labels = get_image_labels(image)
    return image_labels.get("language") == "python"


def patch_otel_annotation(pod, containers):
    """识别 Python 应用（读镜像 metadata label），自动加 OTel 注入 annotation，
    交给 OpenTelemetry Operator 去注入 agent（不重复造轮子）"""
    labels = pod.get("metadata", {}).get("labels", {})
    annotations = pod.get("metadata", {}).get("annotations", {})

    # 已经手动标了 OTel 注入，跳过
    if OTEL_INJECT_PYTHON_ANNOTATION in annotations:
        return []

    # 判断是否有 Python 容器（读镜像 metadata label）
    has_python = any(is_python_app(c, labels) for c in containers)
    if not has_python:
        return []

    # annotation key 含斜杠，JSON Pointer 需转义：/ → ~1
    escaped_key = OTEL_INJECT_PYTHON_ANNOTATION.replace("/", "~1")

    if annotations:
        # annotations 对象已存在，直接加 key
        return [{
            "op": "add",
            "path": f"/metadata/annotations/{escaped_key}",
            "value": "true",
        }]
    else:
        # annotations 对象不存在，先创建整个 annotations 对象
        return [{
            "op": "add",
            "path": "/metadata/annotations",
            "value": {OTEL_INJECT_PYTHON_ANNOTATION: "true"},
        }]


# ============================================================
# Webhook 入口
# ============================================================
@app.route("/mutate", methods=["POST"])
def mutate():
    """处理 apiserver 发来的 AdmissionReview 请求"""
    admission_review = request.get_json()

    pod = admission_review.get("request", {}).get("object", {})
    containers = pod.get("spec", {}).get("containers", [])
    labels = pod.get("metadata", {}).get("labels", {})

    # 只处理带 app 标签的 Pod（避免影响 kube-system 等系统组件）
    if "app" not in labels:
        return jsonify({
            "apiVersion": "admission.k8s.io/v1",
            "kind": "AdmissionReview",
            "response": {"uid": admission_review["request"]["uid"], "allowed": True},
        })

    # 汇总三件事的 patch
    patch = []
    patch += patch_resource_limits(containers)
    patch += patch_team_label(pod)
    patch += patch_otel_annotation(pod, containers)

    # 构建 response：返回 patch（base64 编码）
    patch_bytes = json.dumps(patch).encode()
    patch_b64 = base64.b64encode(patch_bytes).decode()

    response = {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "response": {
            "uid": admission_review["request"]["uid"],
            "allowed": True,
            "patchType": "JSONPatch",
            "patch": patch_b64,
        },
    }
    return jsonify(response)


@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify(status="ok")


if __name__ == "__main__":
    # webhook 必须走 HTTPS（apiserver 要求），证书由 cert-manager 签发
    app.run(
        host="0.0.0.0",
        port=8443,
        ssl_context=(
            os.getenv("TLS_CERT", "/certs/tls.crt"),
            os.getenv("TLS_KEY", "/certs/tls.key"),
        ),
    )
