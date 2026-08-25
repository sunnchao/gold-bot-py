# Replay 对拍夹具

存放移植对拍金标准(H1 级别,L1 对拍测试使用):

- `input.json` — 源仓库 `tests/replay/testdata` 夹具原样复制。
- `expected.json` — 在源仓库运行 `runReplay()` 固化的输出快照(生成脚本:`scripts/generate_golden.py`,M0 建立)。

## 目录约定

```
fixtures/replay/<case>/
├── input.json
└── expected.json
```

## 规则

- 夹具与期望输出一经冻结,只读使用。
- 新引擎 `run_replay` 输出与 `expected.json` 结构 + 数值全等;容差登记制度见 `docs/porting/TDD.md` 第 5 节。
- 新增用例需先在源仓库生成 golden,再进本目录。
