# 设备映射（星源 / 希尔塔）

Status: ready-for-agent

## Destination

「Channel ⟷ DBC」标题右侧有设备下拉框，选项「星源」「希尔塔」，默认「星源」。星源的自动绑定建议与今天一致，且不给 `VCUDebug` 通道。希尔塔用另一张通道表，并把 `VCUDebug` 绑到通道 11。切换档位时整表按新表重画，丢掉上一档的手工改动。一批文件共用这一档。

规格： [spec.md](spec.md)

## Notes

- 领域词：绑定、自动绑定建议、显示名、已绑定 / 不导出 / 无数据、批次。设备映射指 `{DBC 主名: 通道号}` 这一档，不进入绑定的身份键。
- 两张票是纵向切片，按依赖从 01 开工。当前前沿是「星源保持为默认档」。
- 测试缝只有取表入口：档名 → `{DBC 主名: 通道号}`，再交给既有自动绑定建议。绑定决策与转换器不感知档名。
- 星源仍是映射文件优先、缺失回退内置表。希尔塔只用内置表。`VCUDebug` 不写入星源映射文件。
- 通道 11 两档不是同一份 DBC：星源是 ZFCANT，希尔塔是 VCUDebug。切换必须整表重算。

## Tickets

- [01 — 星源保持为默认档](issues/01-xingyuan-default-profile.md) — Blocked by: 无
- [02 — 切换到希尔塔并重画通道表](issues/02-switch-to-xierta.md) — Blocked by: 01

## Decisions so far

- [01](issues/01-xingyuan-default-profile.md)：取表入口 `mapping_for_profile` + 界面默认「星源」下拉；星源文件优先/回退与今天一致，VCUDebug 进列表不自动绑。
- [02](issues/02-switch-to-xierta.md)：设备下拉切换希尔塔整表重画（含 VCUDebug→11），空 DBC 只记档；批次提示与忙碌态纳入设备映射。
## Not yet specified

## Out of scope

- 第三种设备，或在界面里编辑、保存自定义通道表
- 为希尔塔新增映射文本，或把 `VCUDebug` 写进星源映射文件
- 同一批次里不同文件使用不同设备档
- 改变绑定三态、未绑定通道不导出原始帧、统计组覆盖规则
- 按设备拆分项目目录，或在星源下把 `VCUDebug.dbc` 从列表里隐藏
