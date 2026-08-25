# 契约测试夹具

存放 HTTP 契约测试(L2)所需的请求/响应样例,源自:

- `docs/porting/snapshots/http_contracts.md`(端点 × 方法 × 载荷矩阵)
- 源仓库 `apps/app-server/src/routes/*.spec.ts` 中的请求/响应样例

## 目录约定

```
fixtures/contracts/<route>/<scenario>.json
```

每个场景为一个请求(方法/path/headers/body)+ 期望(状态码/响应 JSON)对。

## 规则

- 覆盖三态:有效载荷、无效载荷(类型/缺字段/越界)、鉴权失败。
- 样例仅取结构与数值,不得携带真实 token 之外的敏感内容(测试 token 用占位值)。
