FROM python:3.11-slim-bullseye

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

# bullseye 基础镜像兼容老版 Docker；bullseye 已过 LTS（security 源不稳定），
# 故不安装系统包：psycopg2-binary 自带 libpq，运行时也无需 curl
COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY backend/ /app/
COPY playbooks/ /app/playbooks/
COPY knowledge/ /app/knowledge/

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
