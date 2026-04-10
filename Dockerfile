FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=120

# 用腾讯云内网镜像，云托管构建机上更稳
COPY requirements-web.txt .
RUN pip install -i https://mirrors.cloud.tencent.com/pypi/simple -r requirements-web.txt

COPY app ./app

# 云托管默认把请求转发到容器的 80 端口
EXPOSE 80

CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "80"]
