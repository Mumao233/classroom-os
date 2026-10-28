# PRD归档与版本说明

归档日期：2026-10-28（北京时间）。

| 项目 | 记录 |
|---|---|
| 用户提供原件 | `J:\class-assistant\Classroom_OS_Web_Alpha_0.1.0_PRD_v1.1_Package\Classroom_OS_Web_Alpha_0.1.0_PRD_v1.2_含预览_阿里云ASR接入方案.pdf` |
| 归档副本 | `docs/web-alpha/Classroom_OS_Web_Alpha_0.1.0_PRD_v1.2.pdf` |
| SHA-256（原件及副本一致） | `DD7D970D54FB443969CE6BB210B0B18053BFECF972CF419EFA57EDB51B2FF5B8` |
| 页数 | 37个物理页 |
| 处理 | 复制并规范副本文件名；未重新导出、编辑内容、移动或删除原件 |

文件名为v1.2，但封面/正文仍标PRD v1.1。以本次用户指定的整个PDF（含增补）为需求基线；同目录旧v1.1 Markdown/DOCX不包含完整新增内容，不能替代此PDF。不能根据页眉旧版本忽略最后三页。

## 物理页与印刷页对应

| PDF物理页 | 内容/印刷标记 |
|---|---|
| 1–20 | 正文，页脚1–20 |
| 21 | 插入Dashboard视觉概念预览，非功能完成截图 |
| 22–34 | 正文，页脚21–33 |
| 35 | ASR增补第1页：首选决策及A1–A3 |
| 36 | ASR增补第2页：A4–A7 |
| 37 | ASR增补第3页：A8–A10及参考资料 |

最后三页文字层存在编码异常；上一轮已渲染原页逐页核读，不依赖乱码提取文本。原件不做修复性重写，保留原hash。新增需求ID按增补的`ASR-Aliyun-01`至`ASR-Aliyun-10`映射A1–A10。

增补明确实时首选`qwen-audio-3.1-asr-flash-streaming`、北京地域；价格按用户确认输入6元/百万Token、输出4.5元/百万Token。文件识别另做POC，不能用Streaming文件演示代替真正实时接入。其他产品范围、7天原音及视觉要求仍保留。

原PRD文件夹7个文件在执行前记录hash，执行后重新校验；清单见[evidence/baseline-manifest.json](evidence/baseline-manifest.json)。文件夹内没有进行移动、覆盖、重命名或删除。
