# 大屏、知识库与智能对话

## 功能入口

- `/dashboard`：运营驾驶舱，展示资产可达率、活动告警、任务单、告警趋势和资产矩阵。
- `/knowledge`：上传 PDF、DOCX、Markdown、TXT、YAML、JSON、LOG 等运维材料，系统会提取正文、建立关键词索引并调用 AI 生成摘要与标签。
- `/chat`：带知识库引用和实时资产/异常/任务单上下文的运维对话面板。

## AI 网关配置

接口兼容 OpenAI Chat Completions 协议。复制 `.env.example` 为 `.env`，填写：

```dotenv
CHAT_API_KEY=替换为企业模型网关密钥
CHAT_BASE_URL=http://116.162.79.176:8001/v1
CHAT_MODEL=glm-4.7-channel-cg
```

不要把真实密钥写入代码、日志或知识库文档。未配置密钥时，文档仍可入库和检索；对话接口会返回状态兜底摘要。

## 权限

- `dashboard:read`：大屏查看。
- `knowledge:read`：知识库查看和检索。
- `knowledge:write`：文档上传、分析和删除。
- `knowledge:chat`：智能对话。默认 viewer/operator 均可使用，变更动作仍需人工确认。

知识文档正文存储在 `knowledge_documents` 表，适合 API 多副本部署；内置 `knowledge/` 目录下的手册会作为只读知识源参与检索。
