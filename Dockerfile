# AssetBook · 资产管理与记账系统
# 单阶段镜像，体积小、启动快，数据落在挂载卷 /data 里。
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    TZ=Asia/Shanghai

WORKDIR /app

# 先装依赖，利用镜像层缓存
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 再拷后端与静态前端
COPY backend ./backend

# 「月花 1000」一键配置脚本（零依赖，用标准库）
# 部署后可以直接在容器里跑：docker compose exec assetbook python /app/setup-1000.py
COPY setup-1000.py ./

# 数据目录（sqlite 文件 + WAL）用卷持久化，容器重建不丢账
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status==200 else 1)"

WORKDIR /app/backend
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
