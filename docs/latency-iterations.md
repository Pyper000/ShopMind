# ShopMind MVP 延迟迭代记录

> 口径：脚本化合成用户回归，非真人测试，不代表用户满意度或真实用户研究。

## latency-v6（2026-10-22）

本轮只改体验和链路执行方式，没有改变 MainAgent → SearchAgent → TradeAgent（休眠）的产品边界。

- 首轮模型提交计划后，`explore/clarify` 不再额外调用模型生成重复的“请确认清单”话术；由结构化方案事实渲染短回执。
- 首轮模型偶尔输出“游泳装备/洗漱用品”等大类时，只在自然语言首轮草稿做规则拆分，所有子项仍保持待确认；显式清单选择仍严格拒绝大类和冲突状态。
- 首轮草稿超过 24 项时收敛尾部低优先级项目，避免因展示上限触发一次昂贵的模型返工；显式后续更新仍保留硬上限。
- 清单确认后，`task_dispatch` 仍派发 SearchAgent，但对已结构化的原子槽位走 SearchAgentFactory 的 catalog fast path：由 `product_search_tool` 根据完整 `SearchRequest + SchemeMutation` 自主选择全量召回、局部召回或候选过滤重排，并保留 `agent.dispatch`、`tool.invoke/result`、`agent.result` 事件。
- Search/compare 结果到达后由事实回执直接展示候选数量；已有、待确认和暂缓项目不会进入检索。

真实 DeepSeek-V3.2 回归（本地 SSE `/commerce/intents/stream`）：

| 场景 | 首条有效反馈 | 首份可见清单 | 首批商品 | 整轮结束 |
| --- | ---: | ---: | ---: | ---: |
| 周末海边 | 6.9–10.4s | 16.0s | 19.4s | 19.4s |
| 首次租房 | 4.9–11.4s | 18.0s | 14.6s | 14.6s |

原始逐事件记录：`eval/results/latency-v6-20261022/two-scenes-real-deepseek-rerun.json`。

随后用 5 个独立会话做回归（海边、首次租房、自驾、露营、家庭咖啡）：

- 10 轮请求均 HTTP 200，错误数 0；5/5 确认轮完成 SearchAgent 派发和 catalog fast path。
- 首条有效反馈平均 4.97s，P95 5.72s。
- 首份可见清单平均 12.23s，P95 13.76s。
- 首批商品平均 13.46s，P95 14.94s。
- 整轮结束平均 12.85s，P95 14.95s。
- 商品候选共返回 12 个；自驾场景候选为 0，已如实保留为目录覆盖问题，不把它当作“搜索成功”。

五场景逐事件记录：`eval/results/latency-v6-20261022/five-scenes-real-deepseek.json`。
指标 CSV：`eval/results/latency-v6-20261022/five-scenes-real-deepseek.csv`。

## 回滚

本轮前的可恢复快照：`F:/徐嘉铭/产品学习/shopmind-rollback/20261022-latency-v4`。

本轮完成后另存 v6 快照（不进入 Git）：`F:/徐嘉铭/产品学习/shopmind-rollback/20261022-latency-v6`。

回滚只需停止本地服务后，将快照中的同路径文件复制回仓库；本轮没有提交 Git、没有推送、没有重新部署公网。

## PRD 补充：评测结论与后续质量迭代（2026-10-22）

- 依据：`eval/results/latency-v6-20261022/five-scenes-real-deepseek.csv` 与对应 JSON，5 个合成场景、每场景初始/确认各一轮。10/10 HTTP 200，0 错误；首条反馈均值/P95 4.97/5.72s，首份清单 12.23/13.76s，确认首批商品 13.46/14.94s（只计有候选场景），整轮结束 12.85/14.95s。
- 5/5 确认轮派发 SearchAgent 并走 catalog fast path；共 10 次主模型调用，输入 36,005 Token、输出 2,545 Token。没有采集供应商账单金额。
- 业务 Bad Case：自驾场景目录召回 0 候选；家庭咖啡“手冲咖啡滤杯”被向量语义近似带到“手冲壶”等跨品类候选。故评测分母必须分开：搜索调用成功率、目录候选覆盖率、匹配准确率不能混成单一“成功率”。
- 产品修正：清单任务槽位搜索以目录可检索文本包含任务名为候选底线，不匹配项不展示、不进入候选缓存；无匹配就诚实提示目录缺口。普通自由商品搜索保持现状，MVP Agent 分工不变。
- 下一轮目标：锁定这 5 个回归场景，增加槽位精确相关性和已知空召回断言；随后再扩至至少 10 个场景，同时报告业务正确性、模型质量、分阶段时延和可靠性。成本金额、真人体验、交易 ROI 继续标记未采集。

## 验收门槛

- Python：`325 passed, 1 warning`
- 前端：`npm run build` 成功
- 真实链路：两场景均出现 `plan.preview → plan.update →（确认后）agent.dispatch → product_search_tool → agent.result → final.result`，未出现断联。
