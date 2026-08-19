# 用 nginx-unprivileged 镜像（非 root 运行，监听 8080）
# FROM 用内网 Harbor 镜像（Jenkins 构建节点无法访问 Docker Hub）
FROM hub-sh.aijidou.com/base/nginx-unprivileged:1.25-alpine-amd64

# 拷贝前端静态文件
COPY index.html /usr/share/nginx/html/
COPY app.js /usr/share/nginx/html/

# 拷贝 Nginx 配置（含反向代理 /api）
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 8080
