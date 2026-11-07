# 真实模型评测

启动配置了真实模型的后端后运行：

```powershell
uv run python eval/run.py --url http://127.0.0.1:8080 --output storage/eval.json
```

会调用真实 Embedding 和聊天模型并产生费用；不读取测试 fake。默认 4 份合成教学材料、8 个固定问题，使用独立 eval-<随机ID> 课程，不覆盖个人资料。启用 API_TOKEN 时，需向脚本提供同名环境变量。

报告包含每题原始响应、HTTP 成功率、成功请求中的引用文档命中率、关键词覆盖、空课程/空章节拒答结果。关键词仅检查字面出现，引用命中仅表示命中了目标文档，均不能声称为语义正确率或完整 Recall@K。

首次实现未配置真实模型 Key，仓库不附虚构分数。建议替换为人工核验的真实课程样本，加入参考答案、题源和可验证得分点，再比较检索参数。

评测资料与检查点会留在数据库，报告保存在 gitignore 覆盖的 storage/。当前无自动数据清理。

## 本机 Final Review 与 Langfuse

先启动本机后端。在当前终端设置 `LANGFUSE_BASE_URL`、`LANGFUSE_PUBLIC_KEY` 和
`LANGFUSE_SECRET_KEY`，以及专用本机测试账号的 `FINAL_REVIEW_EVAL_EMAIL` 和
`FINAL_REVIEW_EVAL_PASSWORD`，然后在仓库根目录运行：

```powershell
uv run --no-project eval/langfuse_local.py --url http://127.0.0.1:8080
```

脚本通过登录接口使用专用账号，导入两份合成材料并顺序运行 TCP、DNS 和空课程用例。
新登录流程要求邮箱验证，评测账号应预先准备。追加 `--extended` 可再运行三条缓存资料
用例，检查有效期回答、缺失端口信息和缺失考试评分规则。缺失信息用例接受有依据的
说明或拒答，其字面检查仍需结合原始回答进行证据忠实度复核。
使用 `--case cache-missing-port` 可以单独检查缺失端口场景。重复传入 `--case` 可指定
多个用例及其顺序，便于修复后先检查失败场景，再检查相关回归。
问答走 `/agent/invoke`，会调用本机后端配置的模型。评测数据保留在该专用账号下。
终端会打印实验链接，逐题报告写入 `storage/langfuse-eval/`。在 Langfuse 的
Experiments 找到 `Final Review · Local` 开头的运行，查看 `required_checks` 和每题的
回答、引用及耗时。
