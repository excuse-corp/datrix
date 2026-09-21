# Chart Config Reference

模板中所有图表由 `DATA` 对象驱动。图表库按容器**真实像素**生成 `viewBox` 并监听 `resize` 重绘，因此不存在拉伸变形问题——不要手写固定 `viewBox`，也不要给 `.cbox` 设固定宽高以外的尺寸约束。

## 区块布局

| 容器 | 栅格 | 适合放 |
|---|---|---|
| `grid-main` | `0.43fr 1fr` | 左 KPI 卡 + 右橙色主图（固定，不走 gridA/B/C） |
| `watch` | 3 等分 | 分组实体卡（店铺/部门/品类），带迷你走势线 |
| `gridA` | `1.25fr 1fr 1fr` | 宽图 + 环形图 + 横向条形图 |
| `gridB` | `1.55fr 1fr` | 多系列折线 + 堆叠柱 |
| `gridC` | `1fr 1fr` | 两个等宽图 |

不需要的区块设为空数组 `[]`，模板会自动不渲染。

## 通用字段（所有图表）

```js
{
  type: "line" | "multiLine" | "groupedBar" | "bar" | "stackedBar" | "hbar" | "donut",
  title: "卡片标题",
  sub: "副标题（口径/单位）",
  tag: {text:"周环比 +8.4%", tone:"sage"},   // tone: green|amber|red|blue|sage|plum|gray
  yMax: 9000,          // 省略则按数据最大值 *1.15 自动推
  yTicks: [9000,6000,3000,0],  // 省略则 niceTicks 自动取整刻度
  yFmt: "k"|"k1"|"currency"|"plain",
  insight: "一句话结论（必须来自已核对数据）",
  foot: "卡片脚注文本"
}
```

## line / multiLine

```js
{
  type: "multiLine",
  labels: ["8/25","8/26", ...],
  series: [
    {name:"抖音旗舰店", color:"#D67040", data:[1180, 1260, ...]},
    {name:"抖音直播店", color:"#D9A62C", dash:"6 3", data:[...]}   // dash => 虚线
  ],
  dots: true,          // 显示数据点（每 3 个 + 末点）
  yMax: 1800, yFmt: "k1",
  footKpis: [{l:"峰值日", v:"9/5"}, {l:"三店合计", v:"¥121,077"}]
}
```

主图（橙色卡）走 `DATA.hero`，额外支持：

- `dark: true`（自动，勿改）— 网格与文字转白色半透明
- `area: true` 放在某个 series 上 → 该系列下方渲染白色渐变面积
- `xTicks: [["8/9",0],["8/24",.5],["9/7",1]]` — 第二项是 0~1 的横向位置

## groupedBar

```js
{
  type: "groupedBar",
  cats: ["8/31","9/1", ...],
  series: [
    {name:"销售额", color:"#D67040", data:[5240, 6180, ...]},
    {name:"退款额", color:"#6F8D7F", data:[420, 510, ...]}
  ]
}
```
柱宽自动 = `min(15, 槽宽*0.72/系列数)`，最多建议 3 个系列。

## bar（单系列 + 可选均值线）

```js
{
  type: "bar",
  cats: ["连衣裙","外套", ...],
  series: [{name:"周转天数", color:"#3E7C8C", data:[38,52,29,44,21]}],
  meanLine: 36.8,      // 红色虚线，值必须来自复算
  yMax: 60
}
```
柱顶自动标数值。

## stackedBar

```js
{
  type: "stackedBar",
  cats: ["W32","W33","W34","W35"],
  segments: [
    {name:"已发货",  color:"#6F8D7F", data:[4,6,8,10]},
    {name:"生产中",  color:"#D9A62C", data:[12,14,11,9]},
    {name:"物料延期", color:"#B4483C", data:[6,5,8,7]}
  ],
  showValues: true,    // 段内标数值（段高 <12px 自动跳过）
  yMax: 30
}
```
`yMax` 省略时按各类别堆叠总和的最大值自动推。

## hbar（横向条形 + 目标线）

```js
{
  type: "hbar",
  rows: [
    {name:"运营部", value:96, color:"#3E7C8C"},
    {name:"仓储部", value:72, color:"#B4483C"}   // 低于目标建议用红
  ],
  target: 85,          // 灰色虚线目标位
  unit: "%"            // 可改 "分" 等
}
```
`value` 超过 100 会被截断显示，请确保是百分比语义。

## donut

```js
{
  type: "donut",
  items: [
    {name:"抖音旗舰店", value:38766.88, color:"#D67040"},
    {name:"淘宝旗舰店", value:39296.41, color:"#3A6488"}
  ],
  center: {num:"3", txt:"在营店铺"},
  foot: "三店合计 ¥121,076.65 · 与净销售额差额 ¥2,000.00 为退款冲减"
}
```
右侧百分比列表由模板自动算并渲染。**环形图分项之和必须与 `foot` 声明的合计自洽**，这是自检必查项。建议 ≤6 项。

## watch 分组卡

```js
watch: {
  title: "店铺经营", titleEm: "WATCHLIST", tag: "3 个店铺 · 近30日",
  items: [{
    name: "模拟抖音旗舰店",
    delta: 16.5,                 // 正数绿↑，负数自动红↓
    valueLabel: "近30日净销售额",
    value: "¥38,766.88",
    color: "#D67040",            // 迷你走势线颜色，各卡建议不同
    spark: [3,4,3,5,4,6,5,7,6,8,7,9]
  }]
}
```

## audit 复核表（必填）

```js
audit: {
  source: "scripts/verify_calcs.py 输出",
  note: "所有百分比按未四舍五入中间值计算后再取整；因四舍五入，分项相加与合计可能存在 ±0.1 的尾差。",
  rows: [
    {id:"C1", name:"三店销售额合计", formula:"38766.88+39296.41+43013.36",
     computed:"121,076.65", reported:"121,076.65", diff:"0.00", status:"pass"}
  ]
}
```

`status` 取值 `pass`（✓ 一致）/ `warn`（△ 尾差）/ `fail`（✕ 不一致）。
`computed` `diff` `status` **必须来自 `--emit-audit` 输出**，不得手填。
表头汇总（通过/需关注/不一致计数）由模板自动统计。

## 配色速查

```
橙 #D67040   暖金 #EEAB64   鼠尾草绿 #6F8D7F   深绿 #2F7A57
琥珀 #D9A62C 砖红 #B4483C   青蓝 #3E7C8C       靛蓝 #3A6488
紫 #7A5A73   墨绿 #1D322B
```

多系列配色建议按「橙 → 青蓝 → 琥珀 → 鼠尾草绿 → 紫」顺序取，保证色相间隔。
