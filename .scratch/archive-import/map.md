# 压缩包拖入

Status: ready-for-agent

## Destination

拖入 zip / 7z / rar 到「输入 BLF」后，工具把它强制解压到同级 `<压缩包主名>/`，再按文件夹来源进入现有批次；产物落在同级镜像输出树 `<压缩包主名>_t/`。散 `.blf` 与文件夹拖入保持原样。

规格： [spec.md](spec.md)

## Notes

- 领域词用 CONTEXT.md：批次、镜像输出树、取消信号、无残留。
- 三张票是纵向切片，按依赖从 01 开工。当前前沿是「zip 拖入解压并进入批次」。
- 解压根与产物树并排；每次导入删除解压根后重建。压缩包文件和已有 `<主名>_t/` 不删。
- 父目录不可写则失败，不改解到其他位置。
- zip / rar 用系统 tar；7z 用 py7zr。不捆绑第三方解压二进制。
- 测试接缝是现有来源解析：给路径集合，断言候选与解压根。界面只断言压缩包走进解析、不走单个 `.blf` 加载。

## Tickets

- [01 — zip 拖入解压并进入批次](issues/01-zip-drop-to-batch.md) — Blocked by: 无
- [02 — rar 与 7z 走同一条来源](issues/02-rar-and-7z.md) — Blocked by: 01
- [03 — 解压失败、取消与越界收口](issues/03-extract-failure-and-cancel.md) — Blocked by: 02

## Decisions so far

<!-- 每张票解决后在此追加一行：gist + 链接 -->

## Not yet specified

## Out of scope

- 「浏览…」选择压缩包、多选或选文件夹
- 加密包密码、嵌套包递归解压、字节数封顶
- 解压到临时目录、复用旧解压内容、不可写时改解到别处
- 批内并发、统一输出目录、批次持久化、非 Windows 兜底
