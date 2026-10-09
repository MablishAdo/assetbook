# AssetBook 常用命令。没有 make 也行，照着命令敲一样的效果。
.PHONY: help venv install run dev docker-build up down logs backup export clean

help:
	@echo "make install      创建虚拟环境并装依赖"
	@echo "make run          本地启动（http://127.0.0.1:8000）"
	@echo "make dev          本地启动并热重载"
	@echo "make docker-build 构建 Docker 镜像"
	@echo "make up           用 docker compose 后台启动"
	@echo "make down         停止并移除容器"
	@echo "make logs         看容器日志"
	@echo "make backup       把数据库备份到 backups/ 目录"
	@echo "make export       导出流水为 CSV"

venv:
	python3 -m venv .venv

install: venv
	./.venv/bin/python -m pip install -U pip
	./.venv/bin/python -m pip install -r requirements.txt

run:
	cd backend && ../.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000

dev:
	cd backend && ../.venv/bin/python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000

docker-build:
	docker build -t assetbook:latest .

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

backup:
	mkdir -p backups
	@test -f data/assetbook.db && cp data/assetbook.db "backups/assetbook-$$(date +%Y%m%d-%H%M%S).db" && echo "已备份到 backups/" || echo "还没找到 data/assetbook.db"

export:
	@echo "启动服务后，浏览器打开 设置 → 导出流水，或直接："
	@echo "  curl -b cookie.txt http://127.0.0.1:8000/api/export/transactions.csv"

clean:
	rm -rf .venv __pycache__ backend/__pycache__ backend/routes/__pycache__
