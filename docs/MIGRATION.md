# 项目整理范围

本项目是在线预测与本地表位筛选的独立维护入口，原网站和原流水线保持在原目录。

| 原目录或模块 | 本项目对应位置 |
|---|---|
| lab-tools-v2/backend/analysis_core.py | epitope_screening/core.py，筛选与窗口计算整理为稳定函数 |
| lab-tools-v2/frontend/mhc.html、backend/routers/mhc.py | epitope_screening/gui.py，本地文件选择、进度、取消、结果持久化 |
| population_coverage/ | epitope_screening/vendor/population_coverage/，计算器与频率数据 |
| iedb-pipeline/data/class_I_allele_specific_cutoff.csv | epitope_screening/data/，当前相同快照 |
| iedb-pipeline/pipeline/stage1_predict.py | 原本地预测入口保留；新 epitope_screening/iedb_api.py 通过官方 API 提供同类预测 |
| iedb-pipeline/pipeline/stage5_assess.py | 暂留原项目，理化性质评估不属于本次发布核心 |

不复制服务器路由、用户账号、网站部署、原始研究数据、数 GB 历史输出、缓存或旧 exe。GitHub 仓库仅包含当前筛选工具所需的代码和固定数据、人工示例、测试、文档与构建流程。

网站后续接入方式：安装本包，由网站任务层调用 `epitope_screening.core.run_analysis()`，并按用户/任务隔离输入与输出。当前整理未直接修改正在使用的网站接口。

后续工作：使用指定的 P09257 / P09259 原始 IEDB 预测导出、历史参数及最终表建立独立复现数据包；不得用新版本默认参数冒充历史参数。
