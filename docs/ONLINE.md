# 从 FASTA 开始：官方 API + 本地筛选

在线模式不依赖作者的电脑、网站或 Docker。用户的电脑直接向 IEDB 提交序列，预测在 IEDB 服务器运行，随后下载结果并调用与离线模式相同的本地筛选核心。

## 开始使用

桌面界面默认在线模式，只需选择 FASTA、结果目录和参数。HLA 可以逗号分隔编辑，复杂参数也可导入 / 保存独立的预测 JSON。筛选参数 JSON 和预测参数 JSON 为两个文件，避免混用。

```bash
# 先试每类一个 HLA 的官方公开示例
python -m epitope_screening online --fasta examples/public_lcmv.fasta --prediction-config examples/prediction-small.json --config examples/config.json --output results

# 使用默认 27 + 27 HLA 面板
python -m epitope_screening online --fasta protein.fasta --prediction-config examples/prediction.json --config examples/config.json --output results

# 恢复同一任务，复用已确认提交的远程任务编号与完成结果
python -m epitope_screening online --fasta protein.fasta --prediction-config examples/prediction.json --config examples/config.json --resume results/online_UTC_ID
```

## 模型及科学约定

- MHC-I：`netmhcpan_ba`，本次官方参数快照说明为 NetMHCpan 4.1 BA；与 `immunogenicity` 一起请求，mask 为 `default`。
- MHC-II：明确指定 `netmhciipan_ba-4.3`，与 `cd4episcore` 一起请求，`peptide_spacing=1`，保证每个位置有评分。
- MHC-II 筛选使用 CD4Episcore **原始 score**；不使用 API 返回的 `combined_score`。
- `netmhcpan_ba` 是官方默认版本别名。每次任务保存官方参数接口快照供版本审计；未来官方修改默认版本后，应在使用前重新验证，不能仅凭软件版本认为预测器永久不变。
- 在线默认 MHC-I 8–14 aa、MHC-II 15 aa。API 可以返回其他 MHC-II 长度的评分，但官方警告 CD4Episcore 只对 15mer 验证，其他长度有效性未知。允许用户修改，警告保存在任务中。
- 27 + 27 HLA 是原流水线配置的面板，不表示与历史论文的 HLA 选择完全相同。

## 状态、批次和恢复

每次最多 4 个 HLA 一批，按顺序提交；可配置 1–8 个。每个任务默认最多等待 1800 秒，每 10 秒查询一次。每批保存请求、提交响应、结果编号及原始结果，随后检查所有 HLA–肽长–坐标组合是否齐全，并校验肽段与 FASTA 一致、IC50 / rank / 原始评分有效。

GET 查询遇到短暂网络故障可重试；POST 提交后如果响应丢失，不自动重发，避免制造重复任务。该情形保存为 `submission_unknown`，需要人工核对已有记录或远程任务。恢复时改变序列、HLA、模型、肽长、筛选参数或 cutoff 会报错；允许修改代理、查询间隔和等待时限。

### 恢复 v0.2.0 的 MHC-II HLA 名称错误

v0.2.0 默认面板的 `DRB1*01:01` 等本地别名未转换成接口需要的 `HLA-DRB1*01:01`，会被 IEDB 拒绝。v0.2.1 在构造请求时转换人类 HLA 名称；DQ / DP 配对按 `HLA-DQA1*05:01/DQB1*02:01` 等格式提交，本地参数和筛选含义不变。

使用 v0.2.1 程序，选择原 FASTA，在「恢复在线任务目录」选中原 `online_...` 文件夹，HLA 列表、模型、肽长和筛选参数保持原值，然后开始。不要手动修改旧任务 JSON，也不必给界面中的别名补前缀。

软件读取已完成的 MHC-I 缓存。对明确返回 invalid alleles 且未取得 `result_id` 的旧请求，仅当所有其他请求内容一致时，保留旧记录到该批次的 `rejected_<ID>/`，再提交修正格式的请求。有远程任务编号、提交状态不明或修改了模型等内容时不会自动重提。每次恢复在总清单记录 `last_run_version`。

取消只停止本地轮询和后续提交。已经提交的官方任务可能继续运行；保留任务 ID，恢复后继续读取。暂不支持同时由多个进程恢复同一个任务目录。

```text
online_UTC_ID/
├── input.fasta
├── cutoff.csv                     # 使用 allele cutoff 时
├── pipeline_manifest.json          # 参数、状态、警告、原始文件哈希
├── mhci_parameters.json            # 官方模型参数快照
├── mhcii_parameters.json
├── predictions/
│   ├── mhci_000/                   # 其他批次同样保存
│   │   ├── request.json
│   │   ├── submitted.json
│   │   ├── result.json
│   │   └── normalized.csv
│   ├── mhci.csv
│   └── mhcii.csv
└── screening/<UTC_ID>/results.xlsx
```

## 网络与数据

在线模式会将蛋白序列及 HLA、模型、肽长发送到 IEDB；不会把数据发送到作者自建服务器。不要把需要保密且不能外传的数据直接用于在线模式。可使用导入预测表的离线模式。

支持系统标准 HTTP(S) 代理，或在界面 / `--proxy http://127.0.0.1:端口` 指定进程级代理；不会更改系统设置。代理字段不接收账号或密码。始终验证 HTTPS 证书，不提供关闭 TLS 校验的开关。特殊网卡绑定仅在开发诊断脚本使用，不硬编码到跨平台软件。

官方接口：`https://api-nextgen-tools.iedb.org/api/v1/pipeline`、`.../results/{result_id}`。目前实测无需 API Key。服务可用性、排队时长、版本及使用约束由 IEDB 决定；没有承诺固定吞吐率或无限使用。

来源：[官方 API 工作流程](https://nextgen-tools.iedb.org/docs/api/workflow/)、[端点及请求结构](https://nextgen-tools.iedb.org/docs/api/endpoints/)、[MHC-II 官方参数](https://api-nextgen-tools.iedb.org/api/v1/mhcii)。
