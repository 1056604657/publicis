"""
三层 Hello-World 应用 - 后端服务 (backend)

一个简单的 REST API，演示三层架构的「业务层」：
- 提供健康检查 /healthz（存活）和 /readyz（就绪，检查依赖连通）
- 提供商品列表的增查接口，演示 Redis 缓存 + PostgreSQL 持久化

技术栈：Flask + psycopg2 + redis + OpenTelemetry SDK
零第三方框架以外的依赖，所有配置通过环境变量注入（12-factor）
"""

import os
import time

from flask import Flask, jsonify, request
import psycopg2
import psycopg2.extras
import redis

# ---- OpenTelemetry 埋点（可观测性，Task 加分项）----
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.flask import FlaskInstrumentor

app = Flask(__name__)

# ---- 从环境变量读配置（12-factor，不硬编码）----
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_USER = os.getenv("DB_USER", "app")
DB_PASSWORD = os.getenv("DB_PASSWORD", "app_password")
DB_NAME = os.getenv("DB_NAME", "app")
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
APP_PORT = int(os.getenv("APP_PORT", "8080"))

# ---- OpenTelemetry 初始化（可选：有 OTLP endpoint 才启用导出）----
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")


def init_tracing():
    if not OTEL_ENDPOINT:
        # 没配 OTLP endpoint 时，只启用本地 trace provider（不导出）
        trace.set_tracer_provider(TracerProvider(
            resource=Resource.create({"service.name": "marriott-backend"})
        ))
        return
    provider = TracerProvider(
        resource=Resource.create({"service.name": "marriott-backend"})
    )
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True))
    )
    trace.set_tracer_provider(provider)


init_tracing()
FlaskInstrumentor().instrument_app(app)


# ---- 数据库连接 ----
def get_db_conn():
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        dbname=DB_NAME,
        connect_timeout=3,
    )


def get_redis():
    return redis.Redis(host=REDIS_HOST, port=REDIS_PORT, socket_connect_timeout=3)


# ---- 健康检查（探针）----
@app.route("/healthz")
def healthz():
    """存活探针：进程活着就返回 200，不检查依赖"""
    return jsonify(status="ok")


@app.route("/readyz")
def readyz():
    """就绪探针：检查 DB 和 Redis 都连通才返回 200"""
    try:
        conn = get_db_conn()
        conn.close()
    except Exception:
        return jsonify(status="not_ready", reason="database_unreachable"), 503
    try:
        r = get_redis()
        r.ping()
    except Exception:
        return jsonify(status="not_ready", reason="redis_unreachable"), 503
    return jsonify(status="ready")


# ---- 业务接口：商品列表 ----
@app.route("/")
def index():
    """根路径：返回一个简单的服务信息页，方便浏览器直接访问确认服务存活"""
    return """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>Marriott Backend API</title>
    <style>
        body { font-family: -apple-system, sans-serif; max-width: 800px; margin: 40px auto; padding: 0 20px; }
        h1 { color: #333; }
        a { color: #4CAF50; text-decoration: none; }
        li { margin: 8px 0; }
    </style>
</head>
<body>
    <h1>Marriott Backend API</h1>
    <p>三层应用后端服务，提供以下接口：</p>
    <ul>
        <li><a href="/healthz">/healthz</a> —— 存活探针</li>
        <li><a href="/readyz">/readyz</a> —— 就绪探针（检查 DB / Redis 连通）</li>
        <li><a href="/api/items">/api/items</a> —— 商品列表（GET / POST）</li>
    </ul>
</body>
</html>
"""


@app.route("/api/items", methods=["GET"])
def list_items():
    """查询商品列表：先查 Redis 缓存，miss 则查 PostgreSQL 并写回缓存"""
    r = get_redis()
    try:
        cached = r.get("items:list")
        if cached:
            return jsonify(source="cache", items=eval(cached))
    except Exception:
        pass  # Redis 挂了降级到直接查 DB

    conn = get_db_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT id, name, description, created_at FROM items ORDER BY id")
    rows = cur.fetchall()
    cur.close()
    conn.close()

    items = [
        {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "created_at": str(row["created_at"]),
        }
        for row in rows
    ]

    # 写回缓存（60 秒过期）
    try:
        r.setex("items:list", 60, str(items))
    except Exception:
        pass

    return jsonify(source="database", items=items)


@app.route("/api/items", methods=["POST"])
def create_item():
    """新增商品：写 PostgreSQL，并失效缓存"""
    data = request.get_json() or {}
    name = data.get("name", "")
    description = data.get("description", "")
    if not name:
        return jsonify(error="name is required"), 400

    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO items (name, description) VALUES (%s, %s) RETURNING id",
        (name, description),
    )
    item_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()

    # 失效缓存
    try:
        get_redis().delete("items:list")
    except Exception:
        pass

    return jsonify(id=item_id, name=name, description=description), 201


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=APP_PORT)
