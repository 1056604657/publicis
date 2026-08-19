# FROM 用内网 Harbor 镜像（Jenkins 构建节点无法访问 Docker Hub）
FROM hub-sh.aijidou.com/base/python:3.12-slim-amd64

# OCI 镜像 label：声明运行时语言（供 Mutating Webhook 自动识别做 OTel 注入）
LABEL language="python"

WORKDIR /app

# 用阿里云 pip 镜像（国内环境加速）
COPY requirements.txt .
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt

COPY mutate.py .

# webhook 证书由 cert-manager 挂载到 /certs
EXPOSE 8443

# 非 root 用户运行，显式 UID=1000
RUN useradd -m -u 1000 appuser
USER 1000

CMD ["python", "mutate.py"]
