# IEDB API 实测记录

测试时间：2026-10-29（Asia/Shanghai）。仅使用官方公开 LCMV 示例，未上传用户研究序列。

官方接口：https://api-nextgen-tools.iedb.org/api/v1

| 项目 | 方法 | HLA | 肽长 | 返回行数 | 结果 |
|---|---|---|---|---|---|
| MHC-I | netmhcpan_ba + immunogenicity | HLA-A*02:01 | 9 | 24 | done，无错误或警告 |
| MHC-II | netmhciipan_ba-4.3 + cd4episcore | HLA-DRB1*01:01 | 15 | 18 | done，无错误或警告 |

两项任务均成功完成 POST 提交、GET 轮询、JSON 读取及表格导出；全部结果的肽段坐标与输入序列相符，IC50、percentile、评分均为有限数值。

MHC-I 示例：ALPHIIDEV，10–18，IC50 11.76 nM，percentile 0.11，免疫原性评分 0.36105。
MHC-II 示例：ALPHIIDEVINIVII，10–24，IC50 701.26 nM，percentile 62，CD4Episcore 92.1408。

此为连通性与功能测试，不是论文复现。单个 HLA、短序列的成功不代表所有 HLA、肽长组合或批量负载均已验证。

本机全局代理出口访问 IEDB 失败；仅本次测试进程绑定现有网络接口并使用公开 DNS 的真实地址后成功。保持 HTTPS 证书和主机名校验，没有更改系统代理、路由或 DNS。该诊断参数不应硬编码到分发版本。

测试脚本：scripts/probe_iedb_api.py。普通网络运行：

```bash
python scripts/probe_iedb_api.py --output results/api-smoke
```

如已有 submitted.json，添加 --resume 读取同一任务，不重复提交。测试不需要 API Key 或账号。

官方文档：https://nextgen-tools.iedb.org/docs/api/endpoints/
