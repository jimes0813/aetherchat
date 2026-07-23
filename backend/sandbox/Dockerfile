# backend/sandbox/Dockerfile - 最小安全沙箱镜像
FROM python:3.11-alpine

RUN apk add --no-cache git bash pytest

# 仅安装必要工具
RUN adduser -D aetheruser
USER aetheruser

WORKDIR /workspace
# 镜像内无网络，默认只读（运行时由 host 控制）
