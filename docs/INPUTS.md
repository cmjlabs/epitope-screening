# 输入格式

每次运行一个蛋白，FASTA 必须恰含一个 `>` 条目，使用 20 种标准氨基酸。多蛋白分别运行；不拼接多条序列。输入须包含真实表位坐标，使用 1-based、首尾包含，且肽段必须与 FASTA 对应区段完全一致。重复序列按原始坐标处理，不重新查找首次出现位置。

标准预测表列：

| 列 | 含义 |
|---|---|
| allele | HLA 名称 |
| peptide | 表位序列 |
| start / end | 表位起止位置 |
| ic50 | IC50，nM |
| percentile 或 rank | 百分位排名，0–100 |
| score | 免疫原性评分，使用预测器原始单位 |

也接受已有 IEDB Excel 列：MHC-I 的 `netmhcpan_ba IC50`、`netmhcpan_ba percentile`；MHC-II 的 `netmhciipan_ba-4.3 IC50`、`netmhciipan_ba-4.3 percentile`；评分为 `immunogenicity score`。大小写不敏感。MHC-II 版本号可不同，但如果同时存在多个模型的 IC50 / percentile 列则报错，需先明确选择一个模型。

缺失评分或无效数值会报错，不会自动跳过阈值筛选。旧版二进制 XLS 需先另存为 XLSX。CSV 使用逗号，TSV / TXT 使用制表符。

cutoff 表要求 `Allele`、`IC50 cutoff` 列，Allele 唯一、阈值非负。启用 allele-specific cutoff 时，缺少文件会报错；无法匹配的 HLA 会在 IC50 步骤淘汰，并记录于 manifest，默认不会退回统一 IC50 阈值。
