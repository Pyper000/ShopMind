# 租房清单与场景演示本地验收（2026-10-23）

## 本轮改动

- 租房清单将“基础清洁用品 / 收纳用品”等宽泛槽位拆成床品组成、地面清洁工具、清洁布、垃圾桶/垃圾袋、马桶刷、衣架、收纳箱/篮、洗衣篮和烧水壶；各项带数量或适用条件，并提醒先核对房东交付清单、房间配置和尺寸。
- 原先由前端固定问答和模拟商品组成的“场景演示”已替换为真实 LiveChat 对话组件。它使用本地 Commerce API/SSE；已知场景首轮仍优先走快速模板，后续未被规则路由处理的自然语言会进入模型。
- 未推送 Git、未部署公网；原有 8012 / 5185 服务保持不动。

## 验收结果

- npm run build：通过。
- .venv/Scripts/python.exe -m pytest -q tests/test_scene_templates.py：10 passed。
- Edge 本地浏览器：待产、租房、首次养猫均使用全新 session；租房页面实际渲染“睡眠与照明 / 入住清洁 / 收纳与日常”三组，具体数量和条件可见。桌面/平板/手机列数为 3 / 2 / 1，必备黄色、按需绿色。
- 浏览器请求确认由前端 http://127.0.0.1:5186 代理至本轮隔离后端 http://127.0.0.1:8013。健康状态为 DeepSeek V3.2、独立 SQLite、Redis/队列关闭，数据写入本地 eval/results/local_acceptance_20261023_rental_demo/data/。
- 独立新租房 session 的首轮返回模板清单；随后问“为什么清单要先核对房东交付单”收到 commerce_concierge 的真实 model.call 事件，模型耗时 11.062 秒。该数值是模型事件耗时，不是整轮端到端时延。事件摘要保存在 eval/results/local_acceptance_20261023_rental_demo/model-followup.json。

## 产物与入口

- 本地验收 URL：http://127.0.0.1:5186/
- 完整报告：eval/results/local_acceptance_20261023_rental_demo/browser-final/local-browser-acceptance.json
- 截图：同目录的 rental-live-demo-desktop.png（实时场景演示）和 rental-local-desktop.png（租房清单）；另有待产和养猫截图。
- 隔离服务保持运行；既有服务未停止，未进行 Git 推送或公网部署。
