# 子机 Agent 构建脚本
# Go 版：静态编译 linux/amd64 单二进制（零运行时依赖）
#   make build-go   -> dist/agent-linux-amd64
# Python 版：无需构建，agent.py 直接分发

GO ?= go
GOFLAGS ?= CGO_ENABLED=0 GOOS=linux GOARCH=amd64

.PHONY: build-go fmt test clean

build-go:
	mkdir -p dist
	$(GOFLAGS) $(GO) build -trimpath -ldflags "-s -w" -o dist/agent-linux-amd64 agent.go

fmt:
	$(GO) fmt ./...

vet:
	$(GO) vet .

clean:
	rm -rf dist
